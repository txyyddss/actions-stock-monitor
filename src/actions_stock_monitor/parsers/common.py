from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup


_WS_RE = re.compile(r"\s+")


def compact_ws(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip())


_AMOUNT_RE = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{1,6}(?:[.,]\d{1,2})?"

PRICE_RE = re.compile(
    rf"(?P<currency>HK\$|US\$|\$|€|£|¥|￥|CN¥|USD|EUR|GBP|HKD|CNY|RMB)\s*(?P<amount>{_AMOUNT_RE})",
    re.IGNORECASE,
)
PRICE_RE_2 = re.compile(
    rf"(?P<amount>{_AMOUNT_RE})\s*(?P<currency>USD|EUR|GBP|CNY|RMB|HKD)",
    re.IGNORECASE,
)


def _normalize_amount(amount: str) -> str:
    s = compact_ws(amount).replace(" ", "").replace("\u00a0", "")
    if "," in s and "." in s:
        # Common thousands separator format: 1,999.00
        return s.replace(",", "")
    if s.count(",") > 1 and "." not in s:
        # 1,999,000
        return s.replace(",", "")
    if s.count(",") == 1 and "." not in s:
        left, right = s.split(",", 1)
        # Ambiguous: treat 1,99 as decimal; 1,999 as thousands.
        if len(right) <= 2:
            return f"{left}.{right}"
        return f"{left}{right}"
    return s


def extract_price(text: str) -> tuple[str | None, str | None]:
    t = compact_ws(text)
    m = PRICE_RE.search(t) or PRICE_RE_2.search(t)
    if not m:
        return None, None
    currency = m.group("currency").upper()
    amount = _normalize_amount(m.group("amount"))

    if currency in {"$", "US$"}:
        currency = "USD"
    if currency == "€":
        currency = "EUR"
    if currency == "£":
        currency = "GBP"
    if currency in {"¥", "￥", "CN¥"}:
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
    "add to cart",
    "加入购物车",
    "加入購物車",
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


_CYCLE_LABELS: dict[str, str] = {
    "monthly": "Monthly",
    "quarterly": "Quarterly",
    "semiannually": "Semiannual",
    "annually": "Yearly",
    "biennially": "Biennial",
    "triennially": "Triennial",
    "onetime": "One-Time",
    "one time": "One-Time",
    "one-time": "One-Time",
}


def extract_billing_cycles(html: str) -> list[str] | None:
    """
    Best-effort billing cycle extractor for WHMCS-like product config pages.
    Returns user-facing cycle labels (e.g. Monthly, Quarterly, Yearly).
    """
    raw = html or ""
    if not raw:
        return None
    try:
        soup = BeautifulSoup(raw, "lxml")
    except Exception:
        return None

    cycles: list[str] = []

    def add_cycle(val: str) -> None:
        v = compact_ws(val).lower()
        if not v:
            return
        label = _CYCLE_LABELS.get(v)
        if not label:
            # Try to normalize common option text.
            for k, lbl in _CYCLE_LABELS.items():
                if k in v:
                    label = lbl
                    break
        if not label:
            return
        if label not in cycles:
            cycles.append(label)

    for sel in soup.select("select[name='billingcycle'], select[name*='billingcycle']"):
        for opt in sel.find_all("option"):
            val = opt.get("value") or ""
            add_cycle(str(val))
            add_cycle(opt.get_text(" ", strip=True) or "")

    for inp in soup.select("input[name='billingcycle'], input[name*='billingcycle']"):
        val = inp.get("value")
        if isinstance(val, str):
            add_cycle(val)

    return cycles or None


SPEC_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("RAM", re.compile(r"\b(\d{1,4})\s*(?:GB|G)\s*(?:RAM|vRAM)\b", re.IGNORECASE)),
    ("CPU", re.compile(r"\b(\d{1,2})\s*(?:vCPU|vCore|CPU|Core|Cores)\b", re.IGNORECASE)),
    ("Disk", re.compile(r"(\d{1,5})\s*(GB|TB)\s*(?:SSD|NVME|HDD)", re.IGNORECASE)),
    ("Bandwidth", re.compile(r"(\d{1,5})\s*(TB|GB)\s*(?:bandwidth|transfer)\b", re.IGNORECASE)),
    ("Traffic", re.compile(r"(\d{1,5})\s*(TB|GB)\s*(?:bandwidth|transfer|traffic)\b", re.IGNORECASE)),
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
