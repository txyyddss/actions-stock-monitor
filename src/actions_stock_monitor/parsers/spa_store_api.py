from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

from bs4 import BeautifulSoup

from ..models import Product
from .common import compact_ws, normalize_url_for_id


def _extract_json_payload(text: str) -> Any | None:
    raw = (text or "").strip()
    if not raw:
        return None

    # Some APIs are returned as HTML with a single <pre> holding JSON.
    if "<pre" in raw.lower():
        try:
            soup = BeautifulSoup(raw, "lxml")
            pre = soup.find("pre")
            raw = (pre.get_text() if pre else raw).strip()
        except Exception:
            pass

    # Some pages return a full SPA shell even on API paths; try to recover JSON.
    start = None
    for ch in ("{", "["):
        i = raw.find(ch)
        if i != -1:
            start = i if start is None else min(start, i)
    if start is not None and start > 0:
        raw = raw[start:].strip()

    try:
        return json.loads(raw)
    except Exception:
        return None


def _best_monthly_price(price_datas: Any) -> float | None:
    if not isinstance(price_datas, list):
        return None

    # Prefer cycle==1 (monthly) when present; otherwise choose the lowest per-month cost.
    monthly: list[float] = []
    per_month: list[float] = []
    for item in price_datas:
        if not isinstance(item, dict):
            continue
        price = item.get("price")
        cycle = item.get("cycle") or 1
        if not isinstance(price, (int, float)) or not isinstance(cycle, int) or cycle <= 0:
            continue
        if cycle == 1:
            monthly.append(float(price))
        per_month.append(float(price) / float(cycle))

    if monthly:
        return min(monthly)
    if per_month:
        return min(per_month)
    return None


def _fmt_money_cents(value: float | None, *, currency: str) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    amount = float(value) / 100.0
    return f"{amount:.2f} {currency}", currency


def _cycle_months_to_label(cycle: Any) -> str | None:
    if not isinstance(cycle, int) or cycle <= 0:
        return None
    mapping = {
        1: "Monthly",
        3: "Quarterly",
        6: "Semiannual",
        12: "Yearly",
        24: "Biennial",
        36: "Triennial",
    }
    if cycle in mapping:
        return mapping[cycle]
    return f"{cycle} Months"


def _extract_cycles(price_datas: Any) -> list[str] | None:
    if not isinstance(price_datas, list):
        return None
    out: list[str] = []
    for item in price_datas:
        if not isinstance(item, dict):
            continue
        label = _cycle_months_to_label(item.get("cycle"))
        if label and label not in out:
            out.append(label)
    return out or None


def _extract_cycle_prices(price_datas: Any, *, currency: str) -> dict[str, str] | None:
    if not isinstance(price_datas, list):
        return None
    out: dict[str, str] = {}
    for item in price_datas:
        if not isinstance(item, dict):
            continue
        cycle_label = _cycle_months_to_label(item.get("cycle"))
        price = item.get("price")
        if not cycle_label or not isinstance(price, (int, float)):
            continue
        price_text, _ = _fmt_money_cents(float(price), currency=currency)
        if price_text:
            out[cycle_label] = price_text
    return out or None


def _mb_to_gb_str(mb: Any) -> str | None:
    if not isinstance(mb, (int, float)):
        return None
    gb = float(mb) / 1024.0
    if gb >= 1 and abs(gb - round(gb)) < 0.01:
        return f"{int(round(gb))}GB"
    if gb >= 1:
        return f"{gb:.1f}GB"
    return f"{int(mb)}MB"


@dataclass(frozen=True)
class SpaStoreApiConfig:
    domain: str
    currency: str = "CNY"
    shop_path: str = "/shop/server"
    shop_query: dict[str, str] | None = None


class SpaStoreApiParser:
    """
    Parser for SPA storefronts that fetch product inventory from an API endpoint and return JSON.

    Supported payload shapes:
    - {"status_code": 0, "data": [{"area_name": ..., "nodes": [{"node_name": ..., "plans": [...] }]}]}
    - {"status_code": 0, "data": {"areas": [{"area_name": ..., "nodes": [{"group_name": ..., "plans": [...] }]}]}}
    """

    def __init__(self, cfg: SpaStoreApiConfig) -> None:
        self._cfg = cfg

    @property
    def domain(self) -> str:
        return self._cfg.domain

    def parse(self, text: str, *, base_url: str) -> list[Product]:
        payload = _extract_json_payload(text)
        if not isinstance(payload, dict):
            return []

        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("areas"), list):
            areas = data.get("areas") or []
        elif isinstance(data, list):
            areas = data
        else:
            return []

        products: list[Product] = []
        for area in areas:
            if not isinstance(area, dict):
                continue
            area_id = area.get("id")
            area_name = compact_ws(str(area.get("area_name") or "")) or None
            nodes = area.get("nodes") or []
            if not isinstance(nodes, list):
                continue
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node_id = node.get("id")
                node_name = compact_ws(str(node.get("node_name") or node.get("group_name") or "")) or None
                plans = node.get("plans") or []
                if not isinstance(plans, list):
                    continue

                for plan in plans:
                    if not isinstance(plan, dict):
                        continue
                    plan_id = plan.get("id")
                    plan_name = compact_ws(str(plan.get("plan_name") or "")) or None
                    if not plan_name:
                        continue

                    # Build a stable, user-meaningful link back to the storefront.
                    base_query: dict[str, str] = {}
                    try:
                        parsed_shop = urlparse(self._cfg.shop_path)
                        for k, v in parse_qsl(parsed_shop.query, keep_blank_values=True):
                            if k and v:
                                base_query[k] = v
                    except Exception:
                        base_query = {}
                    for k, v in (self._cfg.shop_query or {}).items():
                        if isinstance(k, str) and isinstance(v, str) and k and v:
                            base_query[k] = v

                    query = dict(base_query)
                    # Prefer API-provided plan tag so storefront links land in the correct tab.
                    plan_tag = compact_ws(str(plan.get("tag") or "")).lower()
                    if plan_tag in {"traffic", "bandwidth"}:
                        query["type"] = plan_tag
                    if isinstance(area_id, int):
                        query["areaId"] = str(area_id)
                    if isinstance(node_id, int):
                        query["nodeId"] = str(node_id)
                    if isinstance(plan_id, int):
                        query["planId"] = str(plan_id)
                    shop_url = self._cfg.shop_path
                    if "?" in shop_url:
                        shop_url = shop_url.split("?", 1)[0]
                    url = f"https://{self.domain}{shop_url}"
                    if query:
                        url += "?" + urlencode(query)

                    stock = plan.get("stock")
                    available = None
                    if isinstance(stock, int):
                        available = stock > 0

                    cpu = plan.get("cpu")
                    memory = plan.get("memory")
                    disk = plan.get("disk")
                    flow = plan.get("flow")
                    bandwidth = plan.get("bandwidth")
                    ipv4 = plan.get("ipv4_num")
                    ipv6 = plan.get("ipv6_num")

                    specs: dict[str, str] = {}
                    if area_name:
                        specs["Location"] = area_name
                    if node_name:
                        specs["Node"] = node_name
                    if isinstance(cpu, (int, float)):
                        specs["CPU"] = f"{int(cpu)} vCPU"
                    ram = _mb_to_gb_str(memory)
                    if ram:
                        specs["RAM"] = ram
                    if isinstance(disk, (int, float)):
                        specs["Disk"] = f"{int(disk)}GB"
                    if isinstance(flow, (int, float)):
                        specs["Transfer"] = f"{int(flow)}GB"
                    if isinstance(bandwidth, (int, float)):
                        specs["Port"] = f"{int(bandwidth)}Mbps"
                    if isinstance(ipv4, (int, float)):
                        specs["IPv4"] = str(int(ipv4))
                    if isinstance(ipv6, (int, float)):
                        specs["IPv6"] = str(int(ipv6))

                    best_price_cents = _best_monthly_price(plan.get("price_datas"))
                    price, currency = _fmt_money_cents(best_price_cents, currency=self._cfg.currency)
                    billing_cycles = _extract_cycles(plan.get("price_datas"))
                    cycle_prices = _extract_cycle_prices(plan.get("price_datas"), currency=self._cfg.currency)
                    if billing_cycles:
                        specs["Cycles"] = ", ".join(billing_cycles)

                    description_parts = [p for p in [area_name, node_name] if p]
                    description = " / ".join(description_parts) if description_parts else None
                    location = area_name or node_name
                    variant_of = node_name if node_name and node_name.lower() not in plan_name.lower() else None
                    if not variant_of and area_name and area_name.lower() not in plan_name.lower():
                        variant_of = area_name

                    pid = f"{self.domain}::{normalize_url_for_id(url)}"
                    products.append(
                        Product(
                            id=pid,
                            domain=self.domain,
                            url=url,
                            name=plan_name,
                            price=price,
                            currency=currency,
                            description=description,
                            specs=specs or None,
                            available=available,
                            raw=None,
                            variant_of=variant_of,
                            location=location,
                            billing_cycles=billing_cycles,
                            cycle_prices=cycle_prices,
                            is_special=False,
                        )
                    )

        return products
