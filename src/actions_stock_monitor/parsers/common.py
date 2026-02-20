from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup


_WS_RE = re.compile(r"\s+")


def compact_ws(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip())


_AMOUNT_RE = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{1,6}(?:[.,]\d{1,2})?"
_CURRENCY_TOKEN_LIST = [
    "HK$",
    "US$",
    "NT$",
    "$",
    "\u20ac",  # EUR symbol
    "\u00a3",  # GBP symbol
    "\u00a5",  # JPY/CNY symbol
    "\uffe5",  # Full-width yuan sign
    "\u5143",  # Yuan character
    "USD",
    "EUR",
    "GBP",
    "HKD",
    "CNY",
    "RMB",
    "JPY",
    "TWD",
    # Legacy mojibake tokens seen in some pages/tests.
    "\u9227?",
    "\u62e2",
    "\u697c",
    "\u951f?",
    "\u934f?",
]
_CURRENCY_TOKENS = "|".join(sorted((re.escape(t) for t in _CURRENCY_TOKEN_LIST), key=len, reverse=True))

PRICE_RE = re.compile(
    rf"(?P<currency>{_CURRENCY_TOKENS})\s*(?P<amount>{_AMOUNT_RE})",
    re.IGNORECASE,
)
PRICE_RE_2 = re.compile(
    rf"(?P<amount>{_AMOUNT_RE})\s*(?P<currency>{_CURRENCY_TOKENS})",
    re.IGNORECASE,
)


def _normalize_amount(amount: str) -> str:
    s = compact_ws(amount).replace(" ", "").replace("\u00a0", "")
    if "," in s and "." in s:
        return s.replace(",", "")
    if s.count(",") > 1 and "." not in s:
        return s.replace(",", "")
    if s.count(",") == 1 and "." not in s:
        left, right = s.split(",", 1)
        if len(right) <= 2:
            return f"{left}.{right}"
        return f"{left}{right}"
    return s


def extract_price(text: str) -> tuple[str | None, str | None]:
    t = compact_ws(text)
    m = PRICE_RE.search(t) or PRICE_RE_2.search(t)
    if not m:
        return None, None

    currency = m.group("currency")
    amount = _normalize_amount(m.group("amount"))
    cur_u = currency.upper()

    if cur_u in {"$", "US$"}:
        cur_u = "USD"
    elif cur_u in {"\u20ac", "\u9227?"}:
        cur_u = "EUR"
    elif cur_u in {"\u00a3", "\u62e2"}:
        cur_u = "GBP"
    elif cur_u in {"\u00a5", "\uffe5", "\u5143", "\u697c", "\u951f?", "\u934f?", "RMB"}:
        cur_u = "CNY"
    elif cur_u == "HK$":
        cur_u = "HKD"
    elif cur_u == "NT$":
        cur_u = "TWD"

    return f"{amount} {cur_u}", cur_u


OOS_WORDS = [
    "out of stock",
    "sold out",
    "unavailable",
    "no stock",
    "stockout",
    "out-of-stock",
    "sold-out",
    "not available",
    "unavailable for order",
    "\u5df2\u552e\u7f44",
    "\u552e\u7f44",
    "\u7f3a\u8d27",
    "\u7121\u5eab\u5b58",
    "\u65e0\u5e93\u5b58",
    "\u6682\u65f6\u7f3a\u8d27",
    "\u66ab\u6642\u7f3a\u8ca8",
    "\u4e0d\u53ef\u7528",
    "\u4e0d\u53ef\u8d2d\u4e70",
    "\u4e0d\u53ef\u8cfc\u8cb7",
    "\u5e93\u5b58\u4e0d\u8db3",
    "\u5eab\u5b58\u4e0d\u8db3",
]
IN_STOCK_WORDS = [
    "in stock",
    "instock",
    "available now",
    "add to cart",
    "order now",
    "buy now",
    "\u6709\u5e93\u5b58",
    "\u6709\u5eab\u5b58",
    "\u5e93\u5b58\u5145\u8db3",
    "\u5eab\u5b58\u5145\u8db3",
    "\u52a0\u5165\u8d2d\u7269\u8f66",
    "\u52a0\u5165\u8cfc\u7269\u8eca",
    "\u7acb\u5373\u8d2d\u4e70",
    "\u7acb\u5373\u8cfc\u8cb7",
    "\u53ef\u8d2d\u4e70",
    "\u53ef\u8cfc\u8cb7",
]

_AVAIL_COUNT_RE = re.compile(
    r"(?P<count>\d+)\s*(?:available|left|in\s*stock|\u5e93\u5b58|\u5eab\u5b58|\u53ef\u7528)\b",
    re.IGNORECASE,
)


def extract_availability(text: str) -> bool | None:
    t = compact_ws(text).lower()
    m = _AVAIL_COUNT_RE.search(t)
    if m:
        try:
            count = int(m.group("count"))
            return count > 0
        except Exception:
            pass

    has_oos = any(w in t for w in OOS_WORDS)
    has_in = any(w in t for w in IN_STOCK_WORDS)
    if has_oos and has_in:
        return None
    if has_oos:
        return False
    if has_in:
        return True
    return None


_CYCLE_LABELS: dict[str, str] = {
    "m": "Monthly",
    "month": "Monthly",
    "monthly": "Monthly",
    "q": "Quarterly",
    "quarter": "Quarterly",
    "quarterly": "Quarterly",
    "s": "Semiannual",
    "semi-annual": "Semiannual",
    "semiannually": "Semiannual",
    "semi-annually": "Semiannual",
    "a": "Yearly",
    "annual": "Yearly",
    "annually": "Yearly",
    "yearly": "Yearly",
    "b": "Biennial",
    "biennially": "Biennial",
    "t": "Triennial",
    "triennially": "Triennial",
    "quadrennially": "Quadrennial",
    "quinquennially": "Quinquennial",
    "onetime": "One-Time",
    "one time": "One-Time",
    "one-time": "One-Time",
}


def _normalize_cycle_label(value: str) -> str | None:
    v = compact_ws(value).lower()
    if not v:
        return None

    label = _CYCLE_LABELS.get(v)
    if label:
        return label

    for key, mapped in _CYCLE_LABELS.items():
        if key in v:
            return mapped

    if "\u6708" in v:
        return "Monthly"
    if "\u5b63" in v:
        return "Quarterly"
    if "\u534a\u5e74" in v:
        return "Semiannual"
    if "\u4e09\u5e74" in v:
        return "Triennial"
    if "\u5169\u5e74" in v or "\u4e24\u5e74" in v or "\u4e8c\u5e74" in v:
        return "Biennial"
    if "\u5e74" in v:
        return "Yearly"
    if "\u4e00\u6b21" in v:
        return "One-Time"
    return None


def extract_billing_cycles_from_text(text: str) -> list[str] | None:
    t = compact_ws(text)
    if not t:
        return None

    cycles: list[str] = []

    def add_cycle(raw_value: str) -> None:
        label = _normalize_cycle_label(raw_value)
        if label and label not in cycles:
            cycles.append(label)

    token_re = re.compile(
        r"(monthly|quarterly|semi-annual(?:ly)?|semiannually|annually|yearly|biennially|triennially|quadrennially|quinquennially|one-?time|onetime|\u6708\u4ed8|\u6708\u7e73|\u5b63\u4ed8|\u5b63\u7e73|\u534a\u5e74|\u5e74\u4ed8|\u5e74\u7e73|\u4e00\u6b21\u6027|\u4e00\u6b21)",
        re.IGNORECASE,
    )
    for m in token_re.finditer(t):
        add_cycle(m.group(0))

    for m in re.finditer(r"\bcycle-([a-z]+)\b", t, flags=re.IGNORECASE):
        add_cycle(m.group(1))

    for m in re.finditer(r"billingcycle=([a-z]+)", t, flags=re.IGNORECASE):
        add_cycle(m.group(1))

    return cycles or None


def extract_billing_cycles(html: str) -> list[str] | None:
    raw = html or ""
    if not raw:
        return None

    try:
        soup = BeautifulSoup(raw, "lxml")
    except Exception:
        return None

    cycles: list[str] = []

    def add_cycle(raw_value: str) -> None:
        label = _normalize_cycle_label(raw_value)
        if label and label not in cycles:
            cycles.append(label)

    for sel in soup.select("select[name='billingcycle'], select[name*='billingcycle'], select[name='cycle'], select[name*='cycle']"):
        for opt in sel.find_all("option"):
            val = opt.get("value") or ""
            add_cycle(str(val))
            add_cycle(opt.get_text(" ", strip=True) or "")

    for inp in soup.select("input[name='billingcycle'], input[name*='billingcycle'], input[name='cycle'], input[name*='cycle']"):
        val = inp.get("value")
        if isinstance(val, str):
            add_cycle(val)

    for m in re.finditer(r"billingcycle=([a-z]+)", raw, flags=re.IGNORECASE):
        add_cycle(m.group(1))

    for c in extract_billing_cycles_from_text(raw) or []:
        if c not in cycles:
            cycles.append(c)

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
    "utm_id",
    "gclid",
    "fbclid",
    "systpl",
    "languagechange",
}
_PRODUCT_ID_KEYS = {"pid", "id", "product", "product_id", "planid"}
_DROP_IF_PRODUCT_KEYS = {"gid", "fid", "step", "billingcycle", "cycle"}


def normalize_url_for_id(url: str) -> str:
    p = urlparse(url)
    raw_query = p.query or ""
    route_prefix = ""
    tail_query = raw_query
    if raw_query.startswith("/"):
        if "&" in raw_query:
            route_prefix, tail_query = raw_query.split("&", 1)
        else:
            route_prefix, tail_query = raw_query, ""

    query_pairs = parse_qsl(tail_query, keep_blank_values=True)
    lower_keys = {k.lower() for k, _ in query_pairs}
    has_product_id = any(k in lower_keys for k in _PRODUCT_ID_KEYS)

    cleaned_pairs: list[tuple[str, str]] = []
    for key, value in query_pairs:
        key_l = key.lower()
        if key_l in _TRACKING_KEYS:
            continue
        if key.startswith("/") and not value:
            continue
        if has_product_id and key_l in _DROP_IF_PRODUCT_KEYS:
            continue
        cleaned_pairs.append((key, value))

    query = urlencode(sorted(cleaned_pairs))
    if route_prefix and not has_product_id:
        query = f"{route_prefix}&{query}" if query else route_prefix
    return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", query, ""))
