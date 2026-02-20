from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Product
from .common import (
    compact_ws,
    extract_availability,
    extract_billing_cycles,
    extract_billing_cycles_from_text,
    extract_cycle_prices,
    extract_price,
    extract_specs,
    looks_like_purchase_action,
    looks_like_special_offer,
    normalize_url_for_id,
)


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
            url_name = self._name_from_url(url)
            if self._looks_like_action_label(name):
                name = url_name or name
            if self._looks_like_non_name(name):
                name = url_name or name
            if compact_ws(name).lower() in {self.domain.lower(), urlparse(url).netloc.lower()}:
                name = url_name or name
            # Some plans are dotted codes like TYO.AS3.Pro.TINY while card headers only show
            # the trailing token (e.g. "TINY"). Prefer the full URL-derived code when it matches.
            if url_name and "." in url_name and name:
                trailing = compact_ws(url_name.split(".")[-1]).lower()
                if trailing and compact_ws(name).lower() == trailing:
                    name = url_name
            price, currency = extract_price(text)
            if not price:
                price, currency = self._price_from_cycle_options(card)
            available = extract_availability(text)
            if available is None:
                available = self._infer_availability(card, url=url)
            specs = self._extract_specs(card) or self._extract_specs_from_text(text) or extract_specs(text)
            billing_cycles = self._extract_billing_cycles(card, text=text)
            cycle_prices = self._extract_cycle_prices(card)
            if specs and billing_cycles and "Cycles" not in specs:
                specs = dict(specs)
                specs["Cycles"] = ", ".join(billing_cycles)
            description = self._extract_description(card, name=name) or (text[:1200] if text else None)
            variant_of, location = self._infer_variant_and_location(url=url, name=name, specs=specs)
            is_special = looks_like_special_offer(name=name, url=url, description=description)

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
                    variant_of=variant_of,
                    location=location,
                    billing_cycles=billing_cycles,
                    cycle_prices=cycle_prices,
                    is_special=is_special,
                )
            )
        return products

    def _iter_cards(self, soup: BeautifulSoup, *, fast_only: bool = False) -> Iterable:
        # High-signal "card" selectors used by common hosting storefronts.
        for sel in [".package", ".product", ".plan", ".pricing", ".card", ".tt-single-product", ".cart-product", ".cartitem", ".bordered-section"]:
            for tag in soup.select(sel):
                yield tag

        # HostBill-style forms that POST action=add&id=<product_id>.
        for form in soup.find_all("form"):
            if self._is_add_form(form):
                yield form

        # Links to checkout pages often sit inside template-specific wrappers.
        link_count = 0
        for a in soup.select("a[href]"):
            href = str(a.get("href") or "")
            h = href.lower()
            if not any(k in h for k in ("a=add", "pid=", "/checkout", "/products/", "/store/", "/cart/")):
                continue
            link_count += 1
            if link_count > 300:
                break
            wrapper = self._nearest_card_like_ancestor(a)
            if wrapper is not None:
                yield wrapper

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

    @staticmethod
    def _nearest_card_like_ancestor(tag):
        cur = tag
        for _ in range(7):
            cur = getattr(cur, "parent", None)
            if not cur or not hasattr(cur, "get_text"):
                return None
            if cur.name not in {"form", "div", "li", "article", "section"}:
                continue
            text = compact_ws(cur.get_text(" ", strip=True))
            if 20 <= len(text) <= 2600:
                return cur
        return None

    def _extract_name(self, tag) -> str | None:
        for sel in [
            "h1",
            "h2",
            "h3",
            "h4",
            ".title",
            ".name",
            ".product-name",
            ".producttitle",
            ".product-title",
            ".plan-title",
            ".card-title",
            ".tt-product-name",
            "[class*='title']",
            "[class*='name']",
        ]:
            t = tag.select_one(sel)
            if t:
                name = compact_ws(t.get_text(" ", strip=True))
                if 2 <= len(name) <= 140:
                    return name

        # HostBill-style forms: first meaningful stripped string is usually the plan name.
        if getattr(tag, "name", "") == "form":
            for s in tag.stripped_strings:
                cand = compact_ws(str(s))
                if not cand or len(cand) > 80:
                    continue
                cl = cand.lower()
                if any(k in cl for k in ("continue", "monthly", "quarterly", "annually", "semi", "biennially", "triennially")):
                    continue
                if extract_price(cand)[0]:
                    continue
                return cand

        # Fallback to link text that doesn't look like an action button.
        bad = (
            "buy",
            "order",
            "checkout",
            "cart",
            "learn more",
            "details",
            "view",
            "continue",
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
        n_l = n.lower()
        if any(k in n_l for k in ("total due today", "subtotal", "proceed to checkout", "your cart is empty")):
            return True
        if n_l in {"cart", "products", "store"}:
            return True
        # Many sites use pipes in short plan names (e.g. "JP | TKY | Global 01").
        if "|" in n:
            if len(n) > 70:
                return True
            if n.count("|") >= 3:
                return True
        if len(n) > 90:
            return True
        # Very long, sentence-like headings are usually introductions/category blurbs.
        if sum(1 for ch in n if ch in ",，。:：") >= 2 and len(n) > 45:
            return True
        if n.count(" ") >= 9:
            return True
        return False

    def _extract_buy_url(self, tag, *, base_url: str) -> str | None:
        candidates: list[tuple[int, str]] = []

        def add_candidate(href: str | None, label: str, *, base_score: int = 0) -> None:
            if not href:
                return
            href = str(href).strip()
            if not href or href.startswith("#") or href.startswith("javascript:"):
                return
            abs_url = self._resolve_href(base_url, href)
            if self._is_non_product_url(abs_url):
                return

            url_l = abs_url.lower()
            lab_l = compact_ws(label).lower()

            score = base_score
            if "cart.php" in url_l:
                if "a=add" in url_l or ("pid=" in url_l and "a=view" not in url_l):
                    score += 7
                elif "a=view" in url_l and "pid=" in url_l:
                    score += 3
            if "/checkout" in url_l:
                score += 7
            if "action=add" in url_l and ("id=" in url_l or "pid=" in url_l):
                score += 7
            if "action=add" in url_l and "id=" in url_l:
                score += 2
            if ("/store/" in url_l or "rp=/store/" in url_l) and not self._is_non_product_url(abs_url):
                score += 4
            if "/products/" in url_l and not self._is_non_product_url(abs_url):
                score += 3
            if any(h in url_l for h in self._cfg.link_hints):
                score += 1
            if any(h in lab_l for h in ("buy", "order", "checkout", "cart", "add", "subscribe", "continue", "立即", "訂購", "購買", "购买", "下单")):
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

        forms = [tag] if getattr(tag, "name", "") == "form" else list(tag.find_all("form"))
        for form in forms:
            action = form.get("action")
            if action:
                action_abs = urljoin(base_url, action)
                if "cart.php" in action_abs.lower():
                    pid = None
                    try:
                        inp = form.find("input", attrs={"name": "pid"})
                        pid = inp.get("value") if inp else None
                    except Exception:
                        pid = None
                    if pid and str(pid).isdigit():
                        add_candidate(f"cart.php?a=add&pid={pid}", "order")

            order_url = self._hostbill_order_url(form, base_url=base_url)
            if order_url:
                add_candidate(order_url, "order", base_score=6)

        if not candidates:
            data_value = str(getattr(tag, "get", lambda *_: None)("data-value") or "").strip()
            if data_value.isdigit():
                add_candidate(self._append_query(base_url, {"action": "add", "id": data_value}), "order", base_score=6)

        if not candidates:
            cls = " ".join(tag.get("class", []) if hasattr(tag, "get") else "")
            if "cartitem" in cls.lower():
                name = self._extract_name(tag) or self._name_from_text(compact_ws(tag.get_text(" ", strip=True))) or "item"
                add_candidate(self._append_query(base_url, {"product": self._slugify(name)}), name, base_score=4)

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_url = candidates[0]
        if best_score < 2:
            return None
        return best_url

    @staticmethod
    def _append_query(url: str, params: dict[str, str]) -> str:
        p = urlparse(url)
        raw_query = p.query or ""
        route_prefix = ""
        tail_query = raw_query
        if raw_query.startswith("/"):
            if "&" in raw_query:
                route_prefix, tail_query = raw_query.split("&", 1)
            else:
                route_prefix, tail_query = raw_query, ""

        query_items = parse_qsl(tail_query, keep_blank_values=True)
        for k, v in params.items():
            if v is None:
                continue
            query_items.append((k, str(v)))
        query = urlencode(query_items)
        if route_prefix:
            query = f"{route_prefix}&{query}" if query else route_prefix
        return urlunparse((p.scheme, p.netloc, p.path, p.params, query, p.fragment))

    @staticmethod
    def _resolve_href(base_url: str, href: str) -> str:
        href = str(href or "").strip()
        if not href:
            return base_url
        if href.startswith(("http://", "https://", "/")):
            return urljoin(base_url, href)
        href_l = href.lower()
        if href_l.startswith(
            (
                "cart/",
                "products/",
                "store/",
                "billing/",
                "cart.php",
                "index.php?/cart/",
                "index.php?/products/",
                "index.php?rp=/store",
            )
        ):
            p = urlparse(base_url)
            root = f"{p.scheme}://{p.netloc}/"
            return urljoin(root, href)
        return urljoin(base_url, href)

    @staticmethod
    def _is_add_form(form) -> bool:
        try:
            action_field = form.find("input", attrs={"name": re.compile(r"^action$", re.IGNORECASE)})
            id_field = form.find("input", attrs={"name": re.compile(r"^(id|pid|product|product_id)$", re.IGNORECASE)})
        except Exception:
            return False
        if not id_field:
            return False
        id_val = str(id_field.get("value") or "").strip()
        if not id_val:
            return False
        if not action_field:
            return True
        action_val = compact_ws(str(action_field.get("value") or "")).lower()
        return action_val in {"add", "order", "configure", "checkout"}

    def _hostbill_order_url(self, form, *, base_url: str) -> str | None:
        if not self._is_add_form(form):
            return None
        action = form.get("action") or base_url
        action_abs = self._resolve_href(base_url, action)
        id_field = (
            form.find("input", attrs={"name": re.compile(r"^id$", re.IGNORECASE)})
            or form.find("input", attrs={"name": re.compile(r"^pid$", re.IGNORECASE)})
            or form.find("input", attrs={"name": re.compile(r"^product(_id)?$", re.IGNORECASE)})
        )
        if not id_field:
            return None
        id_val = str(id_field.get("value") or "").strip()
        if not id_val:
            return None

        query = {"action": "add", "id": id_val}
        select_cycle = form.find("select", attrs={"name": re.compile(r"cycle", re.IGNORECASE)})
        if select_cycle:
            first_opt = select_cycle.find("option")
            if first_opt and first_opt.get("value"):
                query["cycle"] = str(first_opt.get("value"))

        return self._append_query(action_abs, query)

    @staticmethod
    def _is_cart_view_url(url: str) -> bool:
        u = url.lower()
        return "cart.php" in u and "a=view" in u

    @staticmethod
    def _is_non_product_url(url: str) -> bool:
        p = urlparse(url)
        qs = parse_qs(p.query)
        url_l = url.lower()

        # HostBill route-style cart links.
        if "?/cart/" in url_l:
            if any(k in url_l for k in ("action=add", "pid=", "id=", "product=")):
                return False
            tail = url_l.split("?/cart/", 1)[1]
            tail = tail.split("&", 1)[0].strip("/")
            if not tail:
                return True
            # /cart/<category> is a listing page.
            if tail.count("/") <= 0:
                return True

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
        if any(x in path for x in ["clientarea.php", "submitticket.php", "announcements", "knowledgebase", "downloads", "serverstatus", "register", "login", "affiliates"]):
            return True
        if any(x in path for x in ["contact", "about", "privacy", "terms", "tos", "changelog", "refund", "protocol", "faq", "blog"]):
            return True

        # HostBill category/listing pages frequently use /products/cart/<category>.
        if "/products/cart/" in path:
            if any(k in qs for k in ["pid", "id", "product", "fid", "gid"]):
                return False
            a = (qs.get("a") or [None])[0]
            if isinstance(a, str) and a.lower() in {"add", "configure"}:
                return False
            if isinstance(qs.get("action", [None])[0], str) and str(qs.get("action", [None])[0]).lower() == "add":
                return False
            return True

        # /cart and /products roots are listing pages.
        if path.rstrip("/") in {"/cart", "/products", "/store"}:
            if any(k in qs for k in ["pid", "id", "product", "fid", "gid"]):
                return False
            a = (qs.get("a") or [None])[0]
            if isinstance(a, str) and a.lower() in {"add", "configure"}:
                return False
            if isinstance(qs.get("action", [None])[0], str) and str(qs.get("action", [None])[0]).lower() == "add":
                return False
            return True

        if "index.php?/products/" in url_l:
            if any(k in url_l for k in ("action=add", "pid=", "id=", "product=")):
                return False
            return True

        if "/products/" in path:
            after = path.split("/products/", 1)[1]
            parts = [x for x in after.split("/") if x]
            if parts and parts[-1] in {"checkout", "configure"}:
                return False
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

        # /cart/<category> on HostBill-style pages is a category listing.
        if "/cart/" in path:
            after = path.split("/cart/", 1)[1]
            parts = [x for x in after.split("/") if x]
            if len(parts) <= 1 and not any(k in url_l for k in ("action=add", "pid=", "id=", "product=")):
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
                prev = parts[-2]
                last = parts[-1]
                if "." in prev and "." not in last and len(last) <= 12:
                    return f"{prev}.{last}"
                return parts[-1]
        if p.path.lower().endswith("/cart.php"):
            product = (qs.get("product") or [None])[0]
            if isinstance(product, str) and product.strip():
                return product.strip()
            pid = (qs.get("pid") or [None])[0]
            if isinstance(pid, str) and pid.strip():
                return f"pid-{pid.strip()}"

        product = (qs.get("product") or [None])[0]
        if isinstance(product, str) and product.strip():
            return product.strip()

        url_l = url.lower()
        if "?/cart/" in url_l:
            tail = url_l.split("?/cart/", 1)[1]
            parts = [x for x in tail.split("/") if x and "=" not in x]
            if parts:
                return parts[-1]
        if p.query.startswith("/cart/"):
            tail = p.query.split("/cart/", 1)[1]
            tail = tail.split("&", 1)[0]
            parts = [x for x in tail.split("/") if x and "=" not in x]
            if parts:
                return parts[-1]

        if "/products/" in p.path.lower():
            parts = [x for x in p.path.split("/") if x]
            if len(parts) >= 3:
                prev = parts[-2]
                last = parts[-1]
                if "." in prev and "." not in last and len(last) <= 12:
                    return f"{prev}.{last}"
                return parts[-1]

        path_parts = [x for x in p.path.split("/") if x]
        if path_parts:
            return path_parts[-1]
        return None

    _NAME_PRICE_SPLIT_RE = re.compile(r"(?:HK\$|US\$|NT\$|\$|€|£|¥|￥|元|USD|EUR|GBP|HKD|CNY|RMB|JPY|TWD)\s*\d", re.IGNORECASE)
    _NAME_TRIM_TAIL_RE = re.compile(r"(?:\b\d+\s*(?:available|left|in\s*stock)\b|\bavailable\b|可用|库存|庫存)\s*$", re.IGNORECASE)

    @staticmethod
    def _name_from_text(text: str) -> str | None:
        t = compact_ws(text)
        if not t:
            return None
        m = GenericDomainParser._NAME_PRICE_SPLIT_RE.search(t)
        prefix = (t[: m.start()] if m else t).strip()
        if not prefix:
            return None
        prefix = re.sub(r"^(?:out of stock|sold out|in stock)\s+", "", prefix, flags=re.IGNORECASE)
        # Many storefront cards prefix a promo chunk like "Save 0 %".
        prefix = re.split(r"\bsave\b", prefix, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        prefix = prefix.replace("Monthly", " ").replace("Per Month", " ").replace("每月", " ")
        prefix = compact_ws(GenericDomainParser._NAME_TRIM_TAIL_RE.sub("", prefix))
        if not (2 <= len(prefix) <= 140):
            return None
        # Avoid using a whole paragraph as a "name".
        if prefix.count(" ") >= 14:
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
            "continue",
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
        cls = " ".join(tag.get("class", []) if hasattr(tag, "get") else "").lower()
        if "cart-product" in cls or "cartitem" in cls:
            return tag
        if getattr(tag, "name", "") == "form" and self._is_add_form(tag):
            return tag

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
        if self._extract_billing_cycles(tag, text=text):
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
            for el in tag.select("a,button,input,span,.stock,.availability,[class*='stock'],[class*='avail']"):
                cls = " ".join(el.get("class", [])) if hasattr(el, "get") else ""
                cls_l = cls.lower()
                if any(k in cls_l for k in ("sold", "soldout", "out-of-stock", "outofstock", "unavailable")):
                    return False
                if "disabled" in cls_l or getattr(el, "has_attr", lambda *_: False)("disabled"):
                    lab = compact_ws(getattr(el, "get_text", lambda *a, **k: "")(" ", strip=True)).lower()
                    if extract_availability(lab) is False:
                        return False
        except Exception:
            pass

        # Only mark In Stock when there is an explicit positive signal in actionable controls.
        try:
            for el in tag.select("a,button,input[type='submit'],input[type='button']"):
                cls = " ".join(el.get("class", [])) if hasattr(el, "get") else ""
                cls_l = cls.lower()
                if "disabled" in cls_l or getattr(el, "has_attr", lambda *_: False)("disabled"):
                    continue
                label = compact_ws(getattr(el, "get_text", lambda *a, **k: "")(" ", strip=True))
                if not label and hasattr(el, "get"):
                    label = compact_ws(str(el.get("value") or ""))
                marker = extract_availability(label)
                if marker is True:
                    return True
                if marker is None and looks_like_purchase_action(label):
                    return True
        except Exception:
            pass

        return None

    @staticmethod
    def _extract_url_from_onclick(onclick: str) -> str | None:
        s = onclick or ""
        for m in re.finditer(r"""['"]([^'"]+)['"]""", s):
            p = m.group(1).strip()
            if p.startswith(("http://", "https://", "/")):
                return p
            if any(k in p for k in ("cart.php", "index.php?rp=", "/cart/", "/products/", "/checkout")):
                return p
        return None

    def _distinct_product_link_count(self, tag, *, base_url: str) -> int:
        seen: set[str] = set()
        for a in tag.find_all("a"):
            href = a.get("href")
            if not href:
                continue
            abs_url = self._resolve_href(base_url, str(href))
            if self._is_non_product_url(abs_url):
                continue
            ul = abs_url.lower()
            if "cart.php" in ul and ("pid=" in ul or "a=add" in ul):
                seen.add(normalize_url_for_id(abs_url))
                continue
            if "action=add" in ul and ("id=" in ul or "pid=" in ul):
                seen.add(normalize_url_for_id(abs_url))
                continue
            if "/checkout" in ul:
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
            if not t or len(t) < 12:
                continue
            if name_l and compact_ws(t).lower() == name_l:
                continue
            # Avoid pulling in entire cards as descriptions.
            if len(t) > 1200:
                t = t[:1200]
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
                if k and v and 1 <= len(k) <= 80 and 1 <= len(v) <= 220:
                    specs[k] = v

        # Tables: <tr><th>Key</th><td>Value</td></tr>
        for tr in tag.select("table tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            k = compact_ws(cells[0].get_text(" ", strip=True))
            v = compact_ws(cells[1].get_text(" ", strip=True))
            if k and v and 1 <= len(k) <= 80 and 1 <= len(v) <= 220:
                specs[k] = v

        # Bullet lists: store as numbered specs, splitting on ":" where possible.
        items: list[str] = []
        for li in tag.select("ul li, ol li"):
            t = compact_ws(li.get_text(" ", strip=True))
            if not t or len(t) < 3 or len(t) > 220:
                continue
            items.append(t)

        # Paragraph lines with key-value separators.
        for p in tag.select("p, div.text-small, .cart-product-section"):
            line = compact_ws(p.get_text(" ", strip=True))
            if not line or len(line) > 220:
                continue
            if ":" in line or "：" in line:
                items.append(line)

        # De-dupe while preserving order.
        seen: set[str] = set()
        items_deduped: list[str] = []
        for it in items:
            if it in seen:
                continue
            seen.add(it)
            items_deduped.append(it)

        idx = 1
        for it in items_deduped[:24]:
            sep = ":" if ":" in it else ("：" if "：" in it else None)
            if sep:
                k, v = it.split(sep, 1)
                k = compact_ws(k)
                v = compact_ws(v)
                if k and v and k not in specs:
                    specs[k[:80]] = v[:220]
                    continue
            specs[str(idx)] = it[:220]
            idx += 1

        return specs or None

    @staticmethod
    def _extract_specs_from_text(text: str) -> dict[str, str] | None:
        # Many storefront templates separate features with pipes.
        parts = [compact_ws(p) for p in (text or "").split("|")]
        parts = [p for p in parts if p and 2 <= len(p) <= 140]
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

    @staticmethod
    def _slugify(value: str) -> str:
        v = compact_ws(value).lower()
        v = re.sub(r"[^a-z0-9]+", "-", v)
        v = v.strip("-")
        return v or "item"

    @staticmethod
    def _price_from_cycle_options(tag) -> tuple[str | None, str | None]:
        for opt in tag.select("select[name='cycle'] option, select[name*='cycle'] option, select[name='billingcycle'] option, select[name*='billingcycle'] option"):
            txt = compact_ws(opt.get_text(" ", strip=True))
            price, currency = extract_price(txt)
            if price:
                return price, currency
        return None, None

    @staticmethod
    def _extract_billing_cycles(tag, *, text: str) -> list[str] | None:
        cycles: list[str] = []
        for c in extract_billing_cycles(str(tag)) or []:
            if c not in cycles:
                cycles.append(c)
        for c in extract_billing_cycles_from_text(text) or []:
            if c not in cycles:
                cycles.append(c)
        return cycles or None

    @staticmethod
    def _extract_cycle_prices(tag) -> dict[str, str] | None:
        return extract_cycle_prices(str(tag))

    def _infer_variant_and_location(self, *, url: str, name: str, specs: dict[str, str] | None) -> tuple[str | None, str | None]:
        location = None
        if specs:
            for key in ("Location", "Data Center", "Datacenter", "Zone", "Region", "Node"):
                if key in specs and specs.get(key):
                    location = specs.get(key)
                    break

        category = self._category_label_from_url(url)
        if not location and category:
            location = self._location_from_category(category)
        variant_of = None
        if category:
            cl = category.lower()
            if name and cl not in name.lower():
                variant_of = category

        return variant_of, location

    @staticmethod
    def _category_label_from_url(url: str) -> str | None:
        u = url.lower()
        slug = None

        if "?/cart/" in u:
            tail = u.split("?/cart/", 1)[1]
            parts = [x for x in tail.split("/") if x and "=" not in x]
            if parts:
                slug = parts[0]
        else:
            p = urlparse(url)
            if p.query.startswith("/cart/"):
                tail = p.query.split("/cart/", 1)[1]
                tail = tail.split("&", 1)[0]
                parts = [x for x in tail.split("/") if x]
                if parts:
                    slug = parts[0]
            path = p.path.lower()
            for marker in ("/cart/", "/store/", "/products/"):
                if marker in path:
                    after = path.split(marker, 1)[1]
                    parts = [x for x in after.split("/") if x]
                    if parts:
                        slug = parts[0]
                        break

        if not slug:
            return None

        pretty = slug.replace("--", " ").replace("-", " ").replace("_", " ")
        pretty = compact_ws(pretty)
        if not pretty:
            return None
        return pretty.title()

    @staticmethod
    def _location_from_category(category: str) -> str | None:
        if not category:
            return None
        tokens = [t for t in re.split(r"[\s\-_/]+", category) if t]
        if not tokens:
            return None
        stop = {
            "vps", "vds", "kvm", "cloud", "server", "servers", "dedicated", "shared", "hosting",
            "performance", "special", "offer", "nat", "simplecloud", "colocation", "ssl", "domain",
            "reseller", "storage", "edge", "global", "intel", "amd", "ryzen9", "ryzen", "epyc",
        }
        loc_tokens: list[str] = []
        for tok in tokens:
            tl = tok.lower()
            if tl in stop:
                break
            loc_tokens.append(tok)
        if not loc_tokens:
            return None
        return compact_ws(" ".join(loc_tokens)).title()
