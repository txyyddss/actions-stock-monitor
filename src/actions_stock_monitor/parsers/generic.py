from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qs, urlparse
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Product
from .common import compact_ws, extract_availability, extract_price, extract_specs, normalize_url_for_id


@dataclass(frozen=True)
class GenericParserConfig:
    domain: str
    card_class_hints: tuple[str, ...] = ("plan", "product", "package", "pricing", "card")
    link_hints: tuple[str, ...] = ("cart", "order", "buy", "checkout", "product", "plan", "package")


class GenericDomainParser:
    def __init__(self, cfg: GenericParserConfig) -> None:
        self._cfg = cfg

    @property
    def domain(self) -> str:
        return self._cfg.domain

    def parse(self, html: str, *, base_url: str) -> list[Product]:
        soup = BeautifulSoup(html, "lxml")
        # Some storefront pages (notably certain WHMCS cart pages) can be very large.
        # Avoid worst-case selector scans by capping candidates and skipping expensive fallbacks.
        is_large = len(html or "") >= 600_000
        max_cards = 400 if is_large else 900
        cards = []
        seen_cards: set[int] = set()
        for tag in self._iter_cards(soup, fast_only=is_large):
            tid = id(tag)
            if tid in seen_cards:
                continue
            seen_cards.add(tid)
            cards.append(tag)
            if len(cards) >= max_cards:
                break
        products: list[Product] = []
        seen: set[str] = set()
        # Some templates (notably WHMCS store themes) use nested "package-*" elements.
        # Promote candidates to their best parent card to avoid capturing only footers/buttons.
        promoted: list = []
        promoted_seen: set[int] = set()
        for c in cards:
            card = self._promote_to_best_card(c, base_url=base_url)
            if id(card) in promoted_seen:
                continue
            promoted_seen.add(id(card))
            promoted.append(card)

        for card in promoted:
            text = compact_ws(card.get_text(" ", strip=True))
            if not text or len(text) < 8:
                continue

            # Skip obvious multi-product containers; these often collapse many products into one "card".
            if self._distinct_product_link_count(card, base_url=base_url) >= 3:
                continue

            url = self._extract_buy_url(card, base_url=base_url)
            if not url or self._is_non_product_url(url):
                continue
            try:
                pu = urlparse(url)
                if pu.netloc and pu.netloc.lower() == urlparse(base_url).netloc.lower() and pu.path.rstrip("/") in ("", "/"):
                    # Links back to the landing page are almost never product purchase links.
                    continue
            except Exception:
                pass

            name = self._extract_name(card) or self._name_from_text(text) or self.domain
            if self._looks_like_action_label(name):
                name = self._name_from_url(url) or name
            if self._looks_like_non_name(name):
                name = self._name_from_url(url) or name
            if compact_ws(name).lower() in {self.domain.lower(), urlparse(url).netloc.lower()}:
                name = self._name_from_url(url) or name
            price, currency = extract_price(text)
            available = extract_availability(text)
            if available is None:
                available = self._infer_availability(card, url=url)
            specs = self._extract_specs(card) or self._extract_specs_from_text(text) or extract_specs(text)
            description = self._extract_description(card, name=name) or (text[:400] if text else None)

            norm = normalize_url_for_id(url)
            pid = f"{self.domain}::{norm}"
            if pid in seen:
                continue
            seen.add(pid)
            products.append(
                Product(
                    id=pid,
                    domain=self.domain,
                    url=url,
                    name=name,
                    price=price,
                    currency=currency,
                    description=description,
                    specs=specs,
                    available=available,
                    raw=None,
                )
            )
        return products

    def _iter_cards(self, soup: BeautifulSoup, *, fast_only: bool = False) -> Iterable:
        # High-signal "card" selectors used by common hosting storefronts.
        for sel in [".package", ".product", ".plan", ".pricing", ".card", ".tt-single-product"]:
            for tag in soup.select(sel):
                yield tag

        if fast_only:
            return

        # Generic class-substring fallback for unknown templates.
        for hint in self._cfg.card_class_hints:
            for tag in soup.select(f"[class*='{hint}']"):
                yield tag

        for tag in soup.find_all(["section", "article", "div", "li"]):
            cls = " ".join(tag.get("class", [])) if hasattr(tag, "get") else ""
            if any(h in cls.lower() for h in self._cfg.card_class_hints):
                yield tag

    def _extract_name(self, tag) -> str | None:
        for sel in [
            "h1",
            "h2",
            "h3",
            ".title",
            ".name",
            ".product-name",
            ".producttitle",
            ".product-title",
            ".plan-title",
            ".tt-product-name",
            "[class*='title']",
            "[class*='name']",
        ]:
            t = tag.select_one(sel)
            if t:
                name = compact_ws(t.get_text(" ", strip=True))
                if 2 <= len(name) <= 120:
                    return name
        # Fallback to link text that doesn't look like an action button.
        bad = (
            "buy",
            "order",
            "checkout",
            "cart",
            "learn more",
            "details",
            "view",
            "立即订购",
            "立即購買",
            "立即购买",
            "立即訂購",
            "加入购物车",
            "加入購物車",
            "查看购物车",
            "查看購物車",
            "购物车",
            "購物車",
        )
        candidates: list[str] = []
        for a in tag.find_all("a"):
            label = compact_ws(a.get_text(" ", strip=True))
            if not (2 <= len(label) <= 120):
                continue
            ll = label.lower()
            if any(b in ll for b in bad):
                continue
            candidates.append(label)
        if candidates:
            # Prefer the longest label; button labels are typically short.
            return sorted(candidates, key=len, reverse=True)[0]
        return None

    @staticmethod
    def _looks_like_non_name(name: str) -> bool:
        n = compact_ws(name)
        if not n:
            return True
        # Many sites use pipes in short plan names (e.g. "JP | TKY | Global 01").
        if "|" in n:
            if len(n) > 60:
                return True
            if n.count("|") >= 3:
                return True
        if len(n) > 60:
            return True
        # Very long, sentence-like headings are usually introductions/category blurbs.
        if sum(1 for ch in n if ch in ",，。!?") >= 1 and len(n) > 35:
            return True
        if n.count(" ") >= 7:
            return True
        return False

    def _extract_buy_url(self, tag, *, base_url: str) -> str | None:
        candidates: list[tuple[int, str]] = []
        def add_candidate(href: str | None, label: str) -> None:
            if not href:
                return
            href = str(href).strip()
            if not href or href.startswith("#") or href.startswith("javascript:"):
                return
            abs_url = urljoin(base_url, href)
            if self._is_non_product_url(abs_url):
                return
            url_l = abs_url.lower()
            lab_l = compact_ws(label).lower()

            score = 0
            if "cart.php" in url_l:
                if "a=add" in url_l or ("pid=" in url_l and "a=view" not in url_l):
                    score += 6
                elif "a=view" in url_l and "pid=" in url_l:
                    score += 4
            if ("/store/" in url_l or "rp=/store/" in url_l) and not self._is_non_product_url(abs_url):
                score += 4
            if "/products/" in url_l and not self._is_non_product_url(abs_url):
                score += 3
            if any(h in url_l for h in self._cfg.link_hints):
                score += 1
            if any(h in lab_l for h in ("buy", "order", "checkout", "cart", "add", "subscribe", "立即", "订购", "購買", "购买", "下单")):
                score += 2
            candidates.append((score, abs_url))

        for a in tag.find_all("a"):
            add_candidate(a.get("href"), a.get_text(" ", strip=True))
            for attr in ("data-href", "data-url"):
                add_candidate(a.get(attr), a.get_text(" ", strip=True))

        for el in tag.select("button, input"):
            onclick = el.get("onclick")
            if onclick and isinstance(onclick, str):
                add_candidate(self._extract_url_from_onclick(onclick), getattr(el, "get_text", lambda *a, **k: "")(" ", strip=True))
            for attr in ("data-href", "data-url"):
                add_candidate(el.get(attr), getattr(el, "get_text", lambda *a, **k: "")(" ", strip=True))

        for form in tag.find_all("form"):
            action = form.get("action")
            if not action:
                continue
            action_abs = urljoin(base_url, action)
            if "cart.php" not in action_abs.lower():
                continue
            pid = None
            try:
                inp = form.find("input", attrs={"name": "pid"})
                pid = inp.get("value") if inp else None
            except Exception:
                pid = None
            if pid and str(pid).isdigit():
                add_candidate(f"cart.php?a=add&pid={pid}", "order")

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_url = candidates[0]
        if best_score < 2:
            return None
        return best_url

    @staticmethod
    def _is_cart_view_url(url: str) -> bool:
        u = url.lower()
        return "cart.php" in u and "a=view" in u

    @staticmethod
    def _is_non_product_url(url: str) -> bool:
        p = urlparse(url)
        qs = parse_qs(p.query)
        rp = (qs.get("rp") or [None])[0]
        if isinstance(rp, str):
            rp_l = rp.lower()
            if rp_l.startswith("/knowledgebase") or rp_l.startswith("/announcements"):
                return True
        if isinstance(rp, str) and rp.startswith("/store/"):
            # /store/<category> is not a product; /store/<category>/<product> is.
            parts = [x for x in rp.strip("/").split("/") if x]
            return len(parts) <= 2
        path = p.path.lower()
        if any(x in path for x in ["clientarea.php", "submitticket.php", "announcements", "knowledgebase", "downloads", "serverstatus", "register", "login"]):
            return True
        if any(x in path for x in ["contact", "about", "privacy", "terms", "tos", "changelog", "refund", "protocol", "faq", "blog"]):
            return True
        if "/products/" in path:
            after = path.split("/products/", 1)[1]
            parts = [x for x in after.split("/") if x]
            return len(parts) <= 1
        if "/store/" in path:
            after = path.split("/store/", 1)[1]
            parts = [x for x in after.split("/") if x]
            return len(parts) <= 1
        if path.endswith("/cart.php"):
            # WHMCS cart pages:
            # - cart.php (listing), cart.php?gid=.. (group listing), cart.php?a=view (view cart) -> non-product
            # - cart.php?a=add&pid=.. or cart.php?pid=.. -> product-ish
            if "pid" in qs:
                return False
            a = (qs.get("a") or [None])[0]
            if isinstance(a, str) and a.lower() == "add":
                return False
            return True
        return False

    @staticmethod
    def _name_from_url(url: str) -> str | None:
        p = urlparse(url)
        qs = parse_qs(p.query)
        rp = (qs.get("rp") or [None])[0]
        if isinstance(rp, str) and rp.startswith("/store/"):
            parts = [x for x in rp.strip("/").split("/") if x]
            if len(parts) >= 3:
                return parts[-1]
        if p.path.lower().endswith("/cart.php"):
            pid = (qs.get("pid") or [None])[0]
            if isinstance(pid, str) and pid.strip():
                return f"pid-{pid.strip()}"
        path_parts = [x for x in p.path.split("/") if x]
        if path_parts:
            return path_parts[-1]
        return None

    _NAME_PRICE_SPLIT_RE = re.compile(r"(?:HK\$|US\$|\$|€|£|¥|USD|EUR|GBP|HKD|CNY|RMB)\s*\d", re.IGNORECASE)
    _NAME_TRIM_TAIL_RE = re.compile(r"(?:\b\d+\s*(?:available|left|in\s*stock)\b|\bavailable\b|可用)\s*$", re.IGNORECASE)

    @staticmethod
    def _name_from_text(text: str) -> str | None:
        t = compact_ws(text)
        if not t:
            return None
        m = GenericDomainParser._NAME_PRICE_SPLIT_RE.search(t)
        prefix = (t[: m.start()] if m else t).strip()
        if not prefix:
            return None
        # Many storefront cards prefix a promo chunk like "Save 0 %".
        prefix = re.split(r"\bsave\b", prefix, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        prefix = prefix.replace("Monthly", " ").replace("Per Month", " ").replace("每月", " ")
        prefix = compact_ws(GenericDomainParser._NAME_TRIM_TAIL_RE.sub("", prefix))
        if not (2 <= len(prefix) <= 120):
            return None
        # Avoid using a whole paragraph as a "name".
        if prefix.count(" ") >= 10:
            return None
        return prefix

    @staticmethod
    def _looks_like_action_label(name: str) -> bool:
        n = compact_ws(name).lower()
        if not n:
            return True
        bad_substrings = (
            "buy",
            "order",
            "checkout",
            "cart",
            "learn more",
            "details",
            "view",
            "立即订购",
            "立即購買",
            "立即购买",
            "立即訂購",
            "加入购物车",
            "加入購物車",
            "查看购物车",
            "查看購物車",
            "购物车",
            "購物車",
        )
        return any(b in n for b in bad_substrings) or len(n) <= 2

    def _promote_to_best_card(self, tag, *, base_url: str):
        best = tag
        best_score = self._card_score(tag, base_url=base_url)
        cur = tag
        cur_link_count = self._distinct_product_link_count(cur, base_url=base_url)
        for _ in range(5):
            cur = getattr(cur, "parent", None)
            if not cur or not hasattr(cur, "get_text"):
                break
            parent_link_count = self._distinct_product_link_count(cur, base_url=base_url)
            # Stop before promoting into a multi-product container; this prevents collapsing
            # multiple products into a single parsed card.
            if parent_link_count > 1 and cur_link_count <= 1:
                break
            score = self._card_score(cur, base_url=base_url)
            if score > best_score:
                best, best_score = cur, score
                cur_link_count = parent_link_count
        return best

    def _card_score(self, tag, *, base_url: str) -> int:
        try:
            text = compact_ws(tag.get_text(" ", strip=True))
        except Exception:
            return -999
        if not text:
            return -999

        score = 0
        if 30 <= len(text) <= 2600:
            score += 1
        if self._extract_name(tag):
            score += 2
        if extract_price(text)[0]:
            score += 2
        if self._extract_specs(tag):
            score += 1
        url = self._extract_buy_url(tag, base_url=base_url)
        if url:
            score += 2
        anchor_count = len(tag.find_all("a"))
        if anchor_count <= 8:
            score += 1
        elif anchor_count >= 20:
            score -= 1
        return score

    @staticmethod
    def _infer_availability(tag, *, url: str) -> bool | None:
        # Disabled "order" buttons are a strong OOS signal.
        try:
            for el in tag.select("a,button,input"):
                cls = " ".join(el.get("class", [])) if hasattr(el, "get") else ""
                cls_l = cls.lower()
                if "sold" in cls_l or "soldout" in cls_l or "out-of-stock" in cls_l or "outofstock" in cls_l:
                    return False
                if "disabled" in cls_l or getattr(el, "has_attr", lambda *_: False)("disabled"):
                    lab = compact_ws(getattr(el, "get_text", lambda *a, **k: "")(" ", strip=True)).lower()
                    if extract_availability(lab) is False:
                        return False
        except Exception:
            pass

        u = (url or "").lower()
        if "cart.php" in u and ("a=add" in u or ("pid=" in u and "a=view" not in u)):
            return True
        if ("rp=/store/" in u or "/store/" in u) and not GenericDomainParser._is_non_product_url(url):
            return True
        return None

    @staticmethod
    def _extract_url_from_onclick(onclick: str) -> str | None:
        s = onclick or ""
        for quote in ("'", '"'):
            parts = s.split(quote)
            for part in parts[1:]:
                p = part.strip()
                if p.startswith(("http://", "https://", "/")) or "cart.php" in p or "index.php?rp=" in p:
                    return p
        return None

    def _distinct_product_link_count(self, tag, *, base_url: str) -> int:
        seen: set[str] = set()
        for a in tag.find_all("a"):
            href = a.get("href")
            if not href:
                continue
            abs_url = urljoin(base_url, str(href))
            if self._is_non_product_url(abs_url):
                continue
            ul = abs_url.lower()
            if "cart.php" in ul and ("pid=" in ul or "a=add" in ul):
                seen.add(normalize_url_for_id(abs_url))
                continue
            if ("/store/" in ul or "rp=/store/" in ul) and not self._is_non_product_url(abs_url):
                seen.add(normalize_url_for_id(abs_url))
                continue
            if "/products/" in ul and not self._is_non_product_url(abs_url):
                seen.add(normalize_url_for_id(abs_url))
                continue
        return len(seen)

    @staticmethod
    def _extract_description(tag, *, name: str) -> str | None:
        name_l = compact_ws(name).lower()
        selectors = [
            ".description",
            ".desc",
            ".product-description",
            ".plan-description",
            "[class*='description']",
            "p",
        ]
        for sel in selectors:
            el = tag.select_one(sel)
            if not el:
                continue
            t = compact_ws(el.get_text(" ", strip=True))
            if not t or len(t) < 16:
                continue
            if name_l and compact_ws(t).lower() == name_l:
                continue
            # Avoid pulling in entire cards as descriptions.
            if len(t) > 500:
                t = t[:500]
            return t
        return None

    def _extract_specs(self, tag) -> dict[str, str] | None:
        specs: dict[str, str] = {}

        # Definition lists: <dt>Key</dt><dd>Value</dd>
        for dl in tag.find_all("dl"):
            dts = dl.find_all("dt")
            dds = dl.find_all("dd")
            for dt, dd in zip(dts, dds):
                k = compact_ws(dt.get_text(" ", strip=True))
                v = compact_ws(dd.get_text(" ", strip=True))
                if k and v and 1 <= len(k) <= 60 and 1 <= len(v) <= 160:
                    specs[k] = v

        # Tables: <tr><th>Key</th><td>Value</td></tr>
        for tr in tag.select("table tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            k = compact_ws(cells[0].get_text(" ", strip=True))
            v = compact_ws(cells[1].get_text(" ", strip=True))
            if k and v and 1 <= len(k) <= 60 and 1 <= len(v) <= 160:
                specs[k] = v

        # Bullet lists: store as numbered specs, splitting on ":" where possible.
        items: list[str] = []
        for li in tag.select("ul li, ol li"):
            t = compact_ws(li.get_text(" ", strip=True))
            if not t or len(t) < 3 or len(t) > 180:
                continue
            items.append(t)

        # De-dupe while preserving order.
        seen: set[str] = set()
        items_deduped: list[str] = []
        for it in items:
            if it in seen:
                continue
            seen.add(it)
            items_deduped.append(it)

        idx = 1
        for it in items_deduped[:20]:
            if ":" in it:
                k, v = it.split(":", 1)
                k = compact_ws(k)
                v = compact_ws(v)
                if k and v and k not in specs:
                    specs[k[:60]] = v[:160]
                    continue
            specs[str(idx)] = it[:160]
            idx += 1

        return specs or None

    @staticmethod
    def _extract_specs_from_text(text: str) -> dict[str, str] | None:
        # Many storefront templates separate features with pipes.
        parts = [compact_ws(p) for p in (text or "").split("|")]
        parts = [p for p in parts if p and 2 <= len(p) <= 120]
        if len(parts) < 3:
            return None

        specs: dict[str, str] = {}
        idx = 1
        for p in parts[:25]:
            # Avoid including full-page boilerplate in "specs" for very large cards.
            if idx > 20:
                break
            specs[str(idx)] = p
            idx += 1
        return specs or None
