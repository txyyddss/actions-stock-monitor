from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


_WS_RE = re.compile(r"\s+")


def compact_ws(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip())


PRICE_RE = re.compile(
    r"(?P<currency>HK\$|US\$|\$|€|£|¥|USD|EUR|GBP|HKD|CNY|RMB)\s*(?P<amount>\d{1,6}(?:[.,]\d{1,2})?)",
    re.IGNORECASE,
)
PRICE_RE_2 = re.compile(
    r"(?P<amount>\d{1,6}(?:[.,]\d{1,2})?)\s*(?P<currency>USD|EUR|GBP|CNY|RMB)",
    re.IGNORECASE,
)


def extract_price(text: str) -> tuple[str | None, str | None]:
    t = compact_ws(text)
    m = PRICE_RE.search(t) or PRICE_RE_2.search(t)
    if not m:
        return None, None
    currency = m.group("currency").upper()
    amount = m.group("amount").replace(",", ".")
    if currency == "$":
        currency = "USD"
    if currency == "US$":
        currency = "USD"
    if currency == "€":
        currency = "EUR"
    if currency == "£":
        currency = "GBP"
    if currency == "¥":
        currency = "CNY"
    if currency == "HK$":
        currency = "HKD"
    if currency == "RMB":
        currency = "CNY"
    return f"{amount} {currency}", currency


OOS_WORDS = [
    "out of stock",
    "sold out",
    "unavailable",
    "no stock",
    "stockout",
    "out-of-stock",
    "sold-out",
    "not available",
    "缺货",
    "缺貨",
    "无库存",
    "無庫存",
    "不可用",
    "售罄",
    "已售罄",
]
IN_STOCK_WORDS = [
    "in stock",
    "instock",
    "available now",
    "有库存",
    "有庫存",
    "現貨",
]

_AVAIL_COUNT_RE = re.compile(r"(?P<count>\d+)\s*(?:available|left|in\s*stock|可用)\b", re.IGNORECASE)


def extract_availability(text: str) -> bool | None:
    t = compact_ws(text).lower()
    m = _AVAIL_COUNT_RE.search(t)
    if m:
        try:
            count = int(m.group("count"))
            return count > 0
        except Exception:
            pass
    # Treat OOS markers as stronger than generic "available" text.
    if any(w in t for w in OOS_WORDS):
        return False
    if any(w in t for w in IN_STOCK_WORDS):
        return True
    return None


SPEC_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("RAM", re.compile(r"(\d{1,4})\s*GB\s*RAM", re.IGNORECASE)),
    ("CPU", re.compile(r"(\d{1,3})\s*(?:vCPU|CPU)", re.IGNORECASE)),
    ("Disk", re.compile(r"(\d{1,5})\s*(GB|TB)\s*(?:SSD|NVME|HDD)", re.IGNORECASE)),
    ("Bandwidth", re.compile(r"(\d{1,5})\s*(TB|GB)\s*(?:bandwidth|transfer)", re.IGNORECASE)),
    ("Port", re.compile(r"(\d{1,5})\s*(?:Mbps|Gbps)", re.IGNORECASE)),
]


def extract_specs(text: str) -> dict[str, str] | None:
    t = compact_ws(text)
    specs: dict[str, str] = {}
    for key, pattern in SPEC_PATTERNS:
        m = pattern.search(t)
        if not m:
            continue
        specs[key] = compact_ws(m.group(0))
    return specs or None


_TRACKING_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
}


def normalize_url_for_id(url: str) -> str:
    p = urlparse(url)
    query_pairs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in _TRACKING_KEYS]
    query = urlencode(sorted(query_pairs))
    return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", query, ""))
