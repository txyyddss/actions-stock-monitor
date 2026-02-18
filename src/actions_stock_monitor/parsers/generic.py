from __future__ import annotations

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
        cards = list(self._iter_cards(soup))
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

            url = self._extract_buy_url(card, base_url=base_url)
            if not url or self._is_non_product_url(url):
                continue

            name = self._extract_name(card) or self.domain
            if self._looks_like_action_label(name):
                name = self._name_from_url(url) or name
            if compact_ws(name).lower() in {self.domain.lower(), urlparse(url).netloc.lower()}:
                name = self._name_from_url(url) or name
            price, currency = extract_price(text)
            available = extract_availability(text)
            specs = self._extract_specs(card) or self._extract_specs_from_text(text) or extract_specs(text)
            description = text[:400] if text else None

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

    def _iter_cards(self, soup: BeautifulSoup) -> Iterable:
        # High-signal "card" selectors used by common hosting storefronts.
        for sel in [".package", ".product", ".plan", ".pricing", ".card"]:
            for tag in soup.select(sel):
                yield tag

        # Generic class-substring fallback for unknown templates.
        for hint in self._cfg.card_class_hints:
            for tag in soup.select(f"[class*='{hint}']"):
                yield tag

        for tag in soup.find_all(["section", "article", "div", "li"]):
            cls = " ".join(tag.get("class", [])) if hasattr(tag, "get") else ""
            if any(h in cls.lower() for h in self._cfg.card_class_hints):
                yield tag

    def _extract_name(self, tag) -> str | None:
        for sel in ["h1", "h2", "h3", ".title", ".name", "[class*='title']"]:
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

    def _extract_buy_url(self, tag, *, base_url: str) -> str | None:
        anchors = list(tag.find_all("a"))
        candidates: list[tuple[int, str]] = []
        for a in anchors:
            href = a.get("href")
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            abs_url = urljoin(base_url, href)
            label = compact_ws(a.get_text(" ", strip=True)).lower()
            if self._is_cart_view_url(abs_url):
                continue

            score = 0
            url_l = abs_url.lower()
            if "/store/" in url_l or "rp=/store/" in url_l:
                score += 3
            if "cart.php" in url_l and ("a=add" in url_l or "pid=" in url_l):
                score += 2
            if any(h in url_l for h in self._cfg.link_hints) or any(h in label for h in self._cfg.link_hints):
                score += 1
            # Prefer links that look like a specific product (not a store index/category).
            if not self._is_non_product_url(abs_url):
                score += 1
            candidates.append((score, abs_url))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_url = candidates[0]
        if best_score <= 0:
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
        if isinstance(rp, str) and rp.startswith("/store/"):
            # /store/<category> is not a product; /store/<category>/<product> is.
            parts = [x for x in rp.strip("/").split("/") if x]
            return len(parts) <= 2
        path = p.path.lower()
        if "/products/" in path:
            after = path.split("/products/", 1)[1]
            parts = [x for x in after.split("/") if x]
            return len(parts) <= 1
        if "/store/" in path:
            after = path.split("/store/", 1)[1]
            parts = [x for x in after.split("/") if x]
            return len(parts) <= 1
        if path.endswith("/cart.php"):
            # Group listing pages like cart.php?gid=37 are categories.
            return "gid" in qs and "pid" not in qs
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
        path_parts = [x for x in p.path.split("/") if x]
        if path_parts:
            return path_parts[-1]
        return None

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
        for _ in range(5):
            cur = getattr(cur, "parent", None)
            if not cur or not hasattr(cur, "get_text"):
                break
            score = self._card_score(cur, base_url=base_url)
            if score > best_score:
                best, best_score = cur, score
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
        if url and not self._is_cart_view_url(url):
            score += 2
        anchor_count = len(tag.find_all("a"))
        if anchor_count <= 8:
            score += 1
        elif anchor_count >= 20:
            score -= 1
        return score

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
