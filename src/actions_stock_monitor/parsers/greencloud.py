from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Product
from .common import compact_ws, extract_price, looks_like_special_offer, normalize_url_for_id
from .generic import GenericDomainParser, GenericParserConfig


@dataclass(frozen=True)
class GreenCloudVpsConfig:
    domain: str = "greencloudvps.com"


class GreenCloudVpsParser:
    """
    GreenCloud storefront pages are a mix of:
    - category/landing pages (often not real products), and
    - plan tables where each row opens a modal with per-location purchase links.

    We parse those tables and emit one Product per (plan, location) link.
    """

    def __init__(self, cfg: GreenCloudVpsConfig) -> None:
        self._cfg = cfg
        self._generic = GenericDomainParser(GenericParserConfig(domain=cfg.domain))

    @property
    def domain(self) -> str:
        return self._cfg.domain

    def parse(self, html: str, *, base_url: str) -> list[Product]:
        soup = BeautifulSoup(html, "lxml")
        rows = soup.select("tr.table-row")
        if not rows:
            # Most top-level *.php pages are category/marketing pages (not actual purchasable SKUs).
            # Avoid emitting "fake products" from those; discovery will still follow their links.
            if base_url.lower().startswith(("http://", "https://")) and "/billing/" not in base_url.lower():
                try:
                    from urllib.parse import urlparse

                    if urlparse(base_url).path.lower().endswith(".php"):
                        return []
                except Exception:
                    pass

            # Fall back to generic parsing for WHMCS /billing/store/* pages and other shapes.
            return self._generic.parse(html, base_url=base_url)

        products: list[Product] = []
        seen: set[str] = set()

        for row in rows:
            cells = [compact_ws(td.get_text(" ", strip=True)) for td in row.find_all(["td", "th"])]
            if len(cells) < 4:
                continue

            plan_name = cells[0] or self.domain

            price_text = ""
            for c in reversed(cells):
                if any(x in c for x in ["$", "€", "£", "USD", "EUR", "GBP", "CNY", "HK$", "¥", "￥", "CN¥"]):
                    price_text = c
                    break
            price, currency = extract_price(price_text or " ".join(cells))

            base_specs: dict[str, str] = {}
            if len(cells) >= 2 and cells[1]:
                base_specs["Disk"] = cells[1]
            if len(cells) >= 3 and cells[2]:
                base_specs["CPU"] = cells[2]
            if len(cells) >= 4 and cells[3]:
                base_specs["RAM"] = cells[3]
            if len(cells) >= 5 and cells[4]:
                base_specs["Traffic"] = cells[4]
            if len(cells) >= 6 and cells[5]:
                base_specs["Port"] = cells[5]
            if len(cells) >= 7 and cells[6]:
                base_specs["OS"] = cells[6]

            # Primary path: Order button opens a modal with per-location store links.
            order = row.select_one("[data-bs-target]")
            modal_target = (order.get("data-bs-target") if order and hasattr(order, "get") else None) if order else None
            modal_id = (str(modal_target).lstrip("#").strip() if modal_target else "") if modal_target else ""

            location_links: list[tuple[str, str]] = []
            if modal_id:
                modal = soup.find(id=modal_id)
                if modal:
                    for a in modal.find_all("a"):
                        href = a.get("href")
                        if not href:
                            continue
                        label = compact_ws(a.get_text(" ", strip=True))
                        abs_url = urljoin(base_url, str(href))
                        if "/billing/store/" not in abs_url.lower():
                            continue
                        location_links.append((label, abs_url))

            # Fallback: sometimes the row itself contains a direct WHMCS store link.
            if not location_links:
                for a in row.find_all("a"):
                    href = a.get("href")
                    if not href:
                        continue
                    abs_url = urljoin(base_url, str(href))
                    if "/billing/store/" in abs_url.lower():
                        location_links.append((compact_ws(a.get_text(" ", strip=True)), abs_url))

            for location_label, href in location_links:
                name = plan_name
                specs = dict(base_specs)
                ll = (location_label or "").strip().lower()
                if ll in {"order", "buy", "buy now", "checkout", "select"}:
                    location_label = ""
                if location_label:
                    name = f"{plan_name} / {location_label}"
                    specs["Location"] = location_label

                norm = normalize_url_for_id(href)
                pid = f"{self.domain}::{norm}"
                if pid in seen:
                    continue
                seen.add(pid)
                products.append(
                    Product(
                        id=pid,
                        domain=self.domain,
                        url=href,
                        name=name,
                        price=price,
                        currency=currency,
                        description=None,
                        specs=specs or None,
                        available=None,
                        raw=None,
                        variant_of=plan_name,
                        location=location_label or None,
                        billing_cycles=None,
                        cycle_prices=None,
                        is_special=looks_like_special_offer(name=name, url=href, description=None),
                    )
                )

        if products:
            return products
        return self._generic.parse(html, base_url=base_url)
