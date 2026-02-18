from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
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
        for card in cards:
            text = compact_ws(card.get_text(" ", strip=True))
            if not text or len(text) < 8:
                continue

            name = self._extract_name(card) or self._extract_name(soup) or self.domain
            price, currency = extract_price(text)
            available = extract_availability(text)
            specs = extract_specs(text)
            description = text[:400] if text else None

            url = self._extract_buy_url(card, base_url=base_url)
            if not url:
                continue
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
        return None

    def _extract_buy_url(self, tag, *, base_url: str) -> str | None:
        anchors = list(tag.find_all("a"))
        candidates: list[str] = []
        for a in anchors:
            href = a.get("href")
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            abs_url = urljoin(base_url, href)
            label = compact_ws(a.get_text(" ", strip=True)).lower()
            if any(h in abs_url.lower() for h in self._cfg.link_hints) or any(h in label for h in self._cfg.link_hints):
                candidates.append(abs_url)
        if candidates:
            return candidates[0]
        for a in anchors:
            href = a.get("href")
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            return urljoin(base_url, href)
        return None

