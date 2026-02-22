from __future__ import annotations

import re
from functools import lru_cache
from typing import Any
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
    "缂鸿揣",
]
IN_STOCK_WORDS_STRONG = [
    "in stock",
    "instock",
    "available now",
    "\u6709\u5e93\u5b58",
    "\u6709\u5eab\u5b58",
    "\u5e93\u5b58\u5145\u8db3",
    "\u5eab\u5b58\u5145\u8db3",
]

# Purchase/action labels are common on both in-stock and out-of-stock pages (often disabled).
# Treat these as weak hints in plain-text extraction; HTML-aware callers should evaluate disabled state.
IN_STOCK_WORDS_WEAK = [
    "add to cart",
    "order now",
    "buy now",
    "\u52a0\u5165\u8d2d\u7269\u8f66",
    "\u52a0\u5165\u8cfc\u7269\u8eca",
    "\u7acb\u5373\u8d2d\u4e70",
    "\u7acb\u5373\u8cfc\u8cb7",
    "\u7acb\u5373\u8ba2\u8d2d",
    "\u7acb\u5373\u8a02\u8cfc",
    "\u53ef\u8d2d\u4e70",
    "\u53ef\u8cfc\u8cb7",
    "绔嬪嵆璁㈣喘",
    "绔嬪嵆璐拱",
]

_AVAIL_COUNT_RE = re.compile(
    r"(?P<count>\d+)\s*(?:available|left|in\s*stock|\u5e93\u5b58|\u5eab\u5b58|\u53ef\u7528|鍙敤)\b",
    re.IGNORECASE,
)
_AVAIL_KV_RE = re.compile(
    r"(?:stock|inventory|available|left|\u5e93\u5b58|\u5eab\u5b58|\u53ef\u7528|鍙敤)\s*[:\uff1a]?\s*(?P<count>-?\d+)\b",
    re.IGNORECASE,
)


def extract_availability(text: str) -> bool | None:
    t = compact_ws(text).lower()
    counts: list[int] = []
    for m in _AVAIL_KV_RE.finditer(t):
        try:
            counts.append(int(m.group("count")))
        except Exception:
            pass
    for m in _AVAIL_COUNT_RE.finditer(t):
        try:
            counts.append(int(m.group("count")))
        except Exception:
            pass
    if counts:
        has_pos = any(c > 0 for c in counts)
        has_zero_or_neg = any(c <= 0 for c in counts)
        if has_pos and not has_zero_or_neg:
            return True
        if has_zero_or_neg and not has_pos:
            return False

    has_oos = any(w in t for w in OOS_WORDS)
    has_in_strong = any(w in t for w in IN_STOCK_WORDS_STRONG)
    has_in_weak = any(w in t for w in IN_STOCK_WORDS_WEAK)

    # If a page says "Out of Stock" anywhere, prefer False unless there is also a strong positive marker.
    if has_oos and has_in_strong:
        return None
    if has_oos:
        return False
    if has_in_strong:
        return True
    # Weak purchase-action labels are too ambiguous in plain text; let HTML-aware logic decide.
    if has_in_weak:
        return None
    return None


def looks_like_purchase_action(text: str) -> bool:
    t = compact_ws(text).lower()
    if not t:
        return False
    return any(w in t for w in IN_STOCK_WORDS_WEAK)


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
    if "姣忔湀" in v:
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
        r"(monthly|quarterly|semi-annual(?:ly)?|semiannually|annually|yearly|biennially|triennially|quadrennially|quinquennially|one-?time|onetime|\u6708\u4ed8|\u6708\u7e73|\u5b63\u4ed8|\u5b63\u7e73|\u534a\u5e74|\u5e74\u4ed8|\u5e74\u7e73|\u4e00\u6b21\u6027|\u4e00\u6b21|姣忔湀)",
        re.IGNORECASE,
    )
    for m in token_re.finditer(t):
        add_cycle(m.group(0))

    for m in re.finditer(r"\bcycle-([a-z]+)\b", t, flags=re.IGNORECASE):
        add_cycle(m.group(1))

    for m in re.finditer(r"billingcycle=([a-z]+)", t, flags=re.IGNORECASE):
        add_cycle(m.group(1))

    return cycles or None


def _extract_billing_cycles_from_tag_or_soup(tag_or_soup, raw: str) -> list[str] | None:
    """Core implementation that works on an already-parsed BS4 tag or soup."""
    cycles: list[str] = []

    def add_cycle(raw_value: str) -> None:
        label = _normalize_cycle_label(raw_value)
        if label and label not in cycles:
            cycles.append(label)

    for sel in tag_or_soup.select("select[name='billingcycle'], select[name*='billingcycle'], select[name='cycle'], select[name*='cycle']"):
        for opt in sel.find_all("option"):
            val = opt.get("value") or ""
            add_cycle(str(val))
            add_cycle(opt.get_text(" ", strip=True) or "")

    for inp in tag_or_soup.select("input[name='billingcycle'], input[name*='billingcycle'], input[name='cycle'], input[name*='cycle']"):
        val = inp.get("value")
        if isinstance(val, str):
            add_cycle(val)

    for m in re.finditer(r"billingcycle=([a-z]+)", raw, flags=re.IGNORECASE):
        add_cycle(m.group(1))

    for c in extract_billing_cycles_from_text(raw) or []:
        if c not in cycles:
            cycles.append(c)

    return cycles or None


def extract_billing_cycles(html: str) -> list[str] | None:
    raw = html or ""
    if not raw:
        return None

    try:
        soup = BeautifulSoup(raw, "lxml")
    except Exception:
        return None

    return _extract_billing_cycles_from_tag_or_soup(soup, raw)


def extract_billing_cycles_from_soup(soup: Any, *, raw: str) -> list[str] | None:
    if soup is None:
        return None
    return _extract_billing_cycles_from_tag_or_soup(soup, raw or "")


def extract_billing_cycles_from_tag(tag) -> list[str] | None:
    """Like extract_billing_cycles but accepts a BS4 tag, avoiding str→re-parse overhead."""
    if tag is None:
        return None
    raw = tag.decode_contents() if hasattr(tag, "decode_contents") else str(tag)
    return _extract_billing_cycles_from_tag_or_soup(tag, raw)


def _extract_cycle_prices_from_tag_or_soup(tag_or_soup) -> dict[str, str] | None:
    """Core implementation that works on an already-parsed BS4 tag or soup."""
    out: dict[str, str] = {}

    def add(raw_cycle: str | None, raw_text: str | None) -> None:
        cycle = _normalize_cycle_label(raw_cycle or "")
        if not cycle and raw_text:
            cycle = _normalize_cycle_label(raw_text)
        if not cycle:
            return
        price, _currency = extract_price(raw_text or "")
        if not price:
            return
        out.setdefault(cycle, price)

    for sel in tag_or_soup.select("select[name='billingcycle'], select[name*='billingcycle'], select[name='cycle'], select[name*='cycle']"):
        for opt in sel.find_all("option"):
            add(str(opt.get("value") or ""), opt.get_text(" ", strip=True))

    for span in tag_or_soup.select(".product-price[class*='cycle-']"):
        classes = " ".join(span.get("class", []))
        code = None
        m = re.search(r"\bcycle-([a-z0-9]+)\b", classes, flags=re.IGNORECASE)
        if m:
            code = m.group(1)
        add(code, span.get_text(" ", strip=True))

    return out or None


def extract_cycle_prices(html: str) -> dict[str, str] | None:
    raw = html or ""
    if not raw:
        return None

    try:
        soup = BeautifulSoup(raw, "lxml")
    except Exception:
        return None

    return _extract_cycle_prices_from_tag_or_soup(soup)


def extract_cycle_prices_from_soup(soup: Any) -> dict[str, str] | None:
    if soup is None:
        return None
    return _extract_cycle_prices_from_tag_or_soup(soup)


def extract_cycle_prices_from_tag(tag) -> dict[str, str] | None:
    """Like extract_cycle_prices but accepts a BS4 tag, avoiding str→re-parse overhead."""
    if tag is None:
        return None
    return _extract_cycle_prices_from_tag_or_soup(tag)


_LOCATION_LABEL_HINTS = (
    "location",
    "datacenter",
    "data center",
    "zone",
    "region",
    "node",
    "pop",
    "facility",
    "dc",
    "\u6a5f\u623f",
    "\u673a\u623f",
    "\u8cc7\u6599\u4e2d\u5fc3",
    "\u6570\u636e\u4e2d\u5fc3",
    "\u5730\u533a",
    "\u5730\u5340",
)
_LOCATION_LABEL_BLOCKLIST = (
    "os",
    "template",
    "hostname",
    "ssh",
    "password",
    "backup",
    "billing",
    "cycle",
    "period",
    "ipv4",
    "ipv6",
    "bandwidth",
    "traffic",
    "transfer",
    "license",
    "control panel",
    "kernel",
    "rescue",
)
_VALUE_BLOCKLIST = {
    "",
    "none",
    "n/a",
    "no",
    "no thanks",
    "default",
    "please choose",
    "select",
    "--",
}


def _looks_like_location_label(label: str) -> bool:
    l = compact_ws(label).lower()
    if not l:
        return False
    if any(x in l for x in _LOCATION_LABEL_BLOCKLIST):
        return False
    return any(x in l for x in _LOCATION_LABEL_HINTS)


def _clean_location_value(raw_value: str) -> str:
    v = compact_ws(raw_value)
    if not v:
        return ""
    v = re.sub(r"\(\s*test\s*ip[^)]*\)", "", v, flags=re.IGNORECASE)
    v = re.sub(r"\s*-\s*(?:in\s*stock|out\s*of\s*stock|sold\s*out)\s*$", "", v, flags=re.IGNORECASE)
    v = re.sub(r"\s+", " ", v).strip(" -")
    return v


def _iter_location_variants_from_group(group) -> list[tuple[str, bool | None]]:
    out: list[tuple[str, bool | None]] = []

    def is_location_control(el) -> bool:
        if not hasattr(el, "get"):
            return False
        name = str(el.get("name") or "").strip().lower()
        # WHMCS location/datacenter selectors commonly use these namespaces.
        if "configoption[" in name or name.startswith("configoption"):
            return True
        if "custom[" in name or name.startswith("custom"):
            return True
        if "location" in name or "datacenter" in name or "data_center" in name or "data center" in name:
            return True
        return False

    for select in group.select("select"):
        if not is_location_control(select):
            continue
        for opt in select.find_all("option"):
            label = compact_ws(opt.get_text(" ", strip=True))
            if not label:
                continue
            avail = extract_availability(label)
            cleaned = _clean_location_value(label)
            if cleaned and cleaned.lower() not in _VALUE_BLOCKLIST:
                out.append((cleaned, avail))

    for inp in group.select("input[type='radio'], input[type='checkbox']"):
        if not is_location_control(inp):
            continue
        text_parts: list[str] = []
        sib = inp.next_sibling
        while sib is not None:
            if getattr(sib, "name", None) in {"br", "input", "script"}:
                break
            piece = compact_ws(getattr(sib, "get_text", lambda *a, **k: str(sib))(" ", strip=True))
            if piece:
                text_parts.append(piece)
            sib = getattr(sib, "next_sibling", None)
        if not text_parts:
            value_attr = inp.get("value")
            if isinstance(value_attr, str):
                text_parts.append(value_attr)
        raw_label = compact_ws(" ".join(text_parts))
        if not raw_label:
            continue
        avail = extract_availability(raw_label)
        cleaned = _clean_location_value(raw_label)
        if cleaned and cleaned.lower() not in _VALUE_BLOCKLIST:
            out.append((cleaned, avail))
    return out


def extract_location_variants(html: str) -> list[tuple[str, bool | None]]:
    raw = html or ""
    if not raw:
        return []

    try:
        soup = BeautifulSoup(raw, "lxml")
    except Exception:
        return []

    return extract_location_variants_from_soup(soup)


def extract_location_variants_from_soup(soup: Any) -> list[tuple[str, bool | None]]:
    if soup is None:
        return []

    variants: list[tuple[str, bool | None]] = []
    seen: set[str] = set()

    groups = soup.select(
        "div.form-group, div.cart-item, div.option-val, fieldset, .configoptions, .product-config, .order-config, div.section"
    )
    for g in groups:
        labels: list[str] = []
        for lbl in g.select(
            "label, h3, h4, .control-label, .font-weight-bold, .section-title, .section-header h2, .section-header h3"
        ):
            txt = compact_ws(lbl.get_text(" ", strip=True))
            if txt:
                labels.append(txt)
        group_label = compact_ws(" ".join(labels))
        if not _looks_like_location_label(group_label):
            continue

        for value, avail in _iter_location_variants_from_group(g):
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            variants.append((value, avail))

    return variants


def extract_locations(html: str) -> list[str]:
    return [name for name, _avail in extract_location_variants(html)]


def looks_like_special_offer(*, name: str | None, url: str | None, description: str | None = None) -> bool:
    blob = " ".join([name or "", url or "", description or ""]).lower()
    if not blob:
        return False
    hints = (
        "special",
        "specials",
        "promo",
        "promotion",
        "deal",
        "flash sale",
        "black friday",
        "cyber monday",
        "limited offer",
        "特供",
        "專供",
        "专供",
        "限时",
        "限時",
    )
    return any(h in blob for h in hints)


SPEC_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "RAM": [
        re.compile(
            r"\b(\d{1,5}(?:\.\d+)?)\s*(?:TB|T|GB|G|MB|M)\s*(?:DDR\d\s*)?(?:RAM|vRAM|Memory|Mem|內存|内存|記憶體|记忆体)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:ram|memory|mem|內存|内存|記憶體|记忆体)\s*(?:[:：-]|\s)\s*(\d{1,5}(?:\.\d+)?)\s*(?:TB|T|GB|G|MB|M)\b",
            re.IGNORECASE,
        ),
    ],
    "CPU": [
        re.compile(
            r"\b(\d{1,3})\s*x?\s*(?:vCPU|vCore|CPU|Core|Cores|核心|核)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(\d{1,3})\s*x?\s*(?:[a-z0-9]+\s+){0,4}(?:core|cores)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(\d{1,3})\s*v\s*(?:dedicated\s*)?cpu\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bcpu\s*(?:[:：-]|\s)\s*(\d{1,3})\s*x?\s*(?:[a-z0-9]+\s+){0,4}(?:core|cores|vcore|vcpu)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:cpu|vcpu|vcore|core|cores|核心|核)\s*[:：]?\s*(\d{1,3})\s*x?\b",
            re.IGNORECASE,
        ),
    ],
    "Disk": [
        re.compile(
            r"\b(\d{1,5}(?:\.\d+)?)\s*(?:TB|GB|MB)\s*(?:SSD|NVME|HDD|Disk|Storage|硬盤|硬盘|磁盤|磁盘)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:disk|storage|ssd|nvme|hdd|硬盤|硬盘|磁盤|磁盘)\s*[:：]?\s*(\d{1,5}(?:\.\d+)?)\s*(?:TB|GB|MB)\b",
            re.IGNORECASE,
        ),
    ],
    "Bandwidth": [
        re.compile(
            r"\b(\d{1,6}(?:\.\d+)?)\s*(?:TB|T|GB|G|MB|M)\s*(?:bandwidth)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:bandwidth)\s*[:：]?\s*(\d{1,6}(?:\.\d+)?)\s*(?:TB|T|GB|G|MB|M)\b",
            re.IGNORECASE,
        ),
    ],
    "Traffic": [
        re.compile(
            r"\b(\d{1,6}(?:\.\d+)?)\s*(?:TB|T|GB|G|MB|M)\s*(?:/\s*(?:month|mo|monthly|月))\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(\d{1,6}(?:\.\d+)?)\s*(?:TB|T|GB|G|MB|M)\s*(?:traffic|transfer|data\s*transfer|流量)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(\d{1,6}(?:\.\d+)?)\s*(?:TB|T|GB|G|MB|M)\s*(?:monthly|month|per\s*month)?\s*(?:bandwidth|traffic|transfer)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:traffic|transfer|data\s*transfer|流量|bandwidthtraffic)\s*[:：]?\s*(\d{1,6}(?:\.\d+)?)\s*(?:TB|T|GB|G|MB|M)(?:\s*/\s*(?:month|mo|monthly|月))?\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:traffic(?:/speed)?|transfer(?:/speed)?|data\s*transfer)\s*(?:[:：-]|\s)\s*(\d{1,6}(?:\.\d+)?)\s*(?:TB|T|GB|G|MB|M)\b",
            re.IGNORECASE,
        ),
    ],
    "Port": [
        re.compile(r"\b(\d{1,5}(?:\.\d+)?)\s*(?:Mbps|Gbps)\b", re.IGNORECASE),
        re.compile(
            r"\b(?:port|network|網絡|网络)\s*[:：]?\s*(\d{1,5}(?:\.\d+)?)\s*(?:Mbps|Gbps)\b",
            re.IGNORECASE,
        ),
    ],
}


def _spec_value_norm(value: str | None) -> str:
    raw = compact_ws(value or "").lower()
    raw = raw.replace(" ", "")
    raw = raw.replace("/month", "").replace("/mo", "").replace("/monthly", "").replace("/月", "")
    raw = raw.replace("bandwidth", "").replace("traffic", "").replace("transfer", "")
    raw = raw.replace("bandwidthtraffic", "")
    raw = raw.strip(":：-")
    return raw


def extract_specs(text: str) -> dict[str, str] | None:
    t = compact_ws(text)
    specs: dict[str, str] = {}
    for key, patterns in SPEC_PATTERNS.items():
        for pattern in patterns:
            m = pattern.search(t)
            if not m:
                continue
            specs[key] = compact_ws(m.group(0))
            break

    # Avoid semantic duplicates like "Bandwidth: 1200GB" + "Traffic: 1200GB".
    if specs.get("Bandwidth") and specs.get("Traffic"):
        if _spec_value_norm(specs.get("Bandwidth")) == _spec_value_norm(specs.get("Traffic")):
            specs.pop("Traffic", None)

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
_DROP_IF_PRODUCT_KEYS = {"gid", "fid", "cat_id", "step", "billingcycle", "cycle"}


@lru_cache(maxsize=16384)
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
