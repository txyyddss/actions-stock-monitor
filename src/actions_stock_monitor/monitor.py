from __future__ import annotations

import hashlib
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from html import unescape
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .http_client import HttpClient
from .models import DomainRun, Product, RunSummary
from .parsers.common import (
    compact_ws,
    extract_availability,
    extract_billing_cycles_from_soup,
    extract_cycle_prices_from_soup,
    extract_location_variants_from_soup,
    extract_price,
    extract_specs,
    looks_like_purchase_action,
    looks_like_special_offer,
    normalize_url_for_id,
)
from .parsers.registry import get_parser_for_domain
from .targets import DEFAULT_TARGETS
from .telegram import h, load_telegram_config, send_telegram_html
from .timeutil import utc_now_iso

_MISSING = object()


def _domain_from_url(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc


def _slugify_fragment(value: str) -> str:
    v = compact_ws(value).lower()
    v = re.sub(r"[^a-z0-9]+", "-", v).strip("-")
    return v or "x"


def _normalize_name_key(value: str | None) -> str:
    text = compact_ws(value or "").lower()
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _product_locations(product: Product) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in (product.locations or []):
        if not isinstance(raw, str):
            continue
        loc = compact_ws(raw)
        if not loc:
            continue
        key = loc.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(loc)
    if product.location:
        loc = compact_ws(product.location)
        if loc and loc.lower() not in seen:
            out.append(loc)
    return out


def _location_links_for_product(product: Product) -> dict[str, str]:
    out: dict[str, str] = {}
    for loc, link in (product.location_links or {}).items():
        if not isinstance(loc, str) or not isinstance(link, str):
            continue
        loc_n = compact_ws(loc)
        if not loc_n:
            continue
        out[loc_n] = link
    for loc in _product_locations(product):
        out.setdefault(loc, product.url)
    return out


def _canonical_product_key(product: Product) -> str:
    try:
        parsed = urlparse(product.url)
        qs_raw = parse_qs(parsed.query or "")
    except Exception:
        parsed = urlparse("")
        qs_raw = {}
    path_scope = "/".join([x for x in (parsed.path or "").lower().split("/") if x][:3])
    qs: dict[str, list[str]] = {str(k).lower(): v for k, v in qs_raw.items()}
    rp_val = ""
    try:
        rp_first = (qs.get("rp") or [None])[0]
        if isinstance(rp_first, str):
            rp_val = compact_ws(rp_first).lower()
    except Exception:
        rp_val = ""
    rp_parts = [x for x in rp_val.strip("/").split("/") if x] if rp_val else []

    for key in ("planid", "product_id", "pid", "id"):
        vals = qs.get(key) or []
        if not vals:
            continue
        raw = compact_ws(str(vals[0]))
        if raw:
            if path_scope:
                return f"{product.domain}::{path_scope}::{key}:{raw}"
            return f"{product.domain}::{key}:{raw}"

    url_norm = normalize_url_for_id(product.url)
    path_l = (parsed.path or "").strip("/").lower()
    is_rp_product_path = bool(rp_val.startswith("/store/") and len(rp_parts) >= 3)
    if is_rp_product_path:
        return f"{product.domain}::url:{url_norm}"
    if path_l and path_l not in {"cart.php", "index.php"}:
        # URL-path-backed product pages are usually stable canonical IDs; avoid
        # collapsing distinct plans that merely share a weak/generic display name.
        return f"{product.domain}::url:{url_norm}"

    variant = _normalize_name_key(product.variant_of)
    name = _normalize_name_key(product.name)
    if variant and name and path_l in {"", "cart.php", "index.php"} and not is_rp_product_path:
        return f"{product.domain}::name:{variant}:{name}"
    if name and path_l in {"", "cart.php", "index.php"} and not is_rp_product_path:
        return f"{product.domain}::name:{name}"
    return f"{product.domain}::url:{url_norm}"


def _telegram_domain_tag(domain: str) -> str:
    labels = [x for x in (domain or "").lower().split(".") if x]
    if not labels:
        return "site"
    if len(labels) >= 3 and labels[-2] in {"co", "com", "net", "org", "gov", "edu"} and len(labels[-1]) == 2:
        # e.g. example.co.uk, example.com.au, example.com.cn
        candidate = labels[-3]
    elif len(labels) >= 2:
        candidate = labels[-2]
    else:
        candidate = labels[0]
    return re.sub(r"[^a-z0-9]", "", candidate) or "site"


def _product_with_special_flag(product: Product) -> Product:
    is_special = bool(
        product.is_special
        or looks_like_special_offer(name=product.name, url=product.url, description=product.description)
    )
    if is_special == product.is_special:
        return product
    return Product(
        id=product.id,
        domain=product.domain,
        url=product.url,
        name=product.name,
        price=product.price,
        currency=product.currency,
        description=product.description,
        specs=product.specs,
        available=product.available,
        raw=product.raw,
        variant_of=product.variant_of,
        location=product.location,
        locations=product.locations,
        location_links=product.location_links,
        billing_cycles=product.billing_cycles,
        cycle_prices=product.cycle_prices,
        is_special=is_special,
    )


def _clone_product(
    product: Product,
    *,
    id: str | None = None,
    name: str | None = None,
    price: str | None = None,
    description: str | None = None,
    specs: dict[str, str] | None = None,
    available: bool | None | object = _MISSING,
    variant_of: str | None = None,
    location: str | None = None,
    locations: list[str] | None = None,
    location_links: dict[str, str] | None = None,
    billing_cycles: list[str] | None = None,
    cycle_prices: dict[str, str] | None = None,
    is_special: bool | None = None,
) -> Product:
    return Product(
        id=id or product.id,
        domain=product.domain,
        url=product.url,
        name=name if name is not None else product.name,
        price=price if price is not None else product.price,
        currency=product.currency,
        description=description if description is not None else product.description,
        specs=specs if specs is not None else product.specs,
        available=product.available if available is _MISSING else available,
        raw=product.raw,
        variant_of=variant_of if variant_of is not None else product.variant_of,
        location=location if location is not None else product.location,
        locations=locations if locations is not None else product.locations,
        location_links=location_links if location_links is not None else product.location_links,
        billing_cycles=billing_cycles if billing_cycles is not None else product.billing_cycles,
        cycle_prices=cycle_prices if cycle_prices is not None else product.cycle_prices,
        is_special=product.is_special if is_special is None else bool(is_special),
    )


def _fetch_text(client: HttpClient, url: str, *, allow_flaresolverr: bool = True):
    try:
        return client.fetch_text(url, allow_flaresolverr=allow_flaresolverr)
    except TypeError as exc:
        # Keep compatibility with simple test doubles that only expose fetch_text(url).
        if "allow_flaresolverr" not in str(exc):
            raise
        return client.fetch_text(url)


def _is_blocked_fetch(fetch_res) -> bool:
    err = (getattr(fetch_res, "error", None) or "").lower()
    if "blocked" in err or "cloudflare" in err or "challenge" in err:
        return True
    status = getattr(fetch_res, "status_code", None)
    # Only treat Cloudflare/edge statuses as blocked by default; generic 403/503
    # without challenge markers should not force a FlareSolverr retry.
    return status in {429, 520, 521, 522, 523, 525, 526}


_NON_PRODUCT_URL_FRAGMENTS = (
    "clientarea.php",
    "register",
    "login",
    "ticket",
    "tickets",
    "submitticket.php",
    "announcements",
    "knowledgebase",
    "downloads",
    "serverstatus",
    "contact",
    "about",
    "privacy",
    "terms",
    "tos",
    "protocol",
    "refund",
    "changelog",
    "status",
    "faq",
    "blog",
    "vps-hosting.php",
)


def _looks_like_non_product_page(url: str) -> bool:
    try:
        p = urlparse(url)
    except Exception:
        return False
    u = (p.path or "").lower()
    q = (p.query or "").lower()
    # Explicit add/configure links with product IDs are product pages.
    if ("action=add" in q or "a=add" in q or "a=configure" in q) and any(
        x in q for x in ("pid=", "id=", "product=", "product_id=")
    ):
        return False
    if "cart.php" in u and ("pid=" in q or "product_id=" in q):
        return False
    if u.rstrip("/") in ("/cart", "/products", "/store"):
        if not any(x in q for x in ("a=add", "pid=", "id=", "product=", "gid=", "fid=")):
            return True
    if "/products/cart/" in u and not any(x in q for x in ("a=add", "action=add", "pid=", "id=", "product=")):
        return True
    if "?/cart/" in url.lower():
        tail = url.lower().split("?/cart/", 1)[1]
        tail = tail.split("&", 1)[0].strip("/")
        if tail.count("/") <= 0 and not any(x in q for x in ("a=add", "pid=", "id=", "product=")):
            return True
    if "index.php?/products/" in url.lower() and not any(x in q for x in ("a=add", "action=add", "pid=", "id=", "product=")):
        return True
    if "a=view" in q and "cart.php" in u:
        return True
    if "cart.php" in u and "a=add" in q and not any(x in q for x in ("pid=", "id=", "product=", "product_id=")):
        return True
    if "domain=register" in q or "domain=transfer" in q:
        return True
    if any(x in u for x in _NON_PRODUCT_URL_FRAGMENTS):
        return True
    # Common WHMCS informational pages.
    if "rp=/announcements" in q or "rp=/knowledgebase" in q:
        return True
    return False


def _availability_rank(v: bool | None) -> int:
    if v is True:
        return 0
    if v is False:
        return 1
    return 2


def _name_quality(name: str | None) -> int:
    n = compact_ws(name or "")
    if not n:
        return 0
    score = 0
    if len(n) >= 6:
        score += 1
    if re.search(r"[a-zA-Z].*\d|\d.*[a-zA-Z]", n):
        score += 1
    if "|" in n or "-" in n:
        score += 1
    if len(n.split()) >= 2:
        score += 1
    return score


def _merge_products_by_canonical_plan(products: list[Product]) -> list[Product]:
    if not products:
        return []
    groups: dict[str, list[Product]] = {}
    for product in products:
        key = _canonical_product_key(product)
        groups.setdefault(key, []).append(product)

    merged: list[Product] = []
    for _key, items in groups.items():
        if len(items) == 1:
            one = items[0]
            locs = _product_locations(one)
            links = _location_links_for_product(one)
            merged.append(
                _clone_product(
                    one,
                    location=(locs[0] if locs else one.location),
                    locations=(locs or None),
                    location_links=(links or None),
                )
            )
            continue

        items_sorted = sorted(
            items,
            key=lambda p: (
                _availability_rank(p.available),
                -_name_quality(p.name),
                -(len(p.specs or {})),
                -(len(p.cycle_prices or {})),
                p.id,
            ),
        )
        base = items_sorted[0]

        avail_values = [p.available for p in items]
        if any(v is True for v in avail_values):
            merged_available = True
        elif avail_values and all(v is False for v in avail_values):
            merged_available = False
        else:
            merged_available = None

        merged_cycles: list[str] = []
        seen_cycles: set[str] = set()
        for p in items:
            for cycle in (p.billing_cycles or []):
                if cycle not in seen_cycles:
                    seen_cycles.add(cycle)
                    merged_cycles.append(cycle)
        merged_cycles_or_none = merged_cycles or None

        merged_cycle_prices: dict[str, str] = {}
        for p in items:
            for cycle, price in (p.cycle_prices or {}).items():
                merged_cycle_prices.setdefault(cycle, price)
        merged_cycle_prices_or_none = merged_cycle_prices or None

        locs: list[str] = []
        seen_locs: set[str] = set()
        for p in items:
            for loc in _product_locations(p):
                k = loc.lower()
                if k in seen_locs:
                    continue
                seen_locs.add(k)
                locs.append(loc)

        location_links: dict[str, str] = {}
        for p in items:
            for loc, link in _location_links_for_product(p).items():
                location_links.setdefault(loc, link)

        variants: list[str] = []
        for p in items:
            variant = compact_ws(p.variant_of or "")
            if variant:
                variants.append(variant)
        variant_counter = Counter(variants)
        merged_variant = variant_counter.most_common(1)[0][0] if variant_counter else base.variant_of

        merged_specs = dict(base.specs or {})
        if locs and "Location" not in merged_specs:
            merged_specs["Location"] = ", ".join(locs[:4])
        merged_specs_or_none = merged_specs or None

        merged_price = base.price
        if not merged_price and merged_cycle_prices_or_none:
            for preferred in ("Monthly", "Quarterly", "Yearly"):
                if preferred in merged_cycle_prices_or_none:
                    merged_price = merged_cycle_prices_or_none[preferred]
                    break
            if not merged_price:
                try:
                    merged_price = next(iter(merged_cycle_prices_or_none.values()))
                except Exception:
                    merged_price = base.price

        merged.append(
            _clone_product(
                base,
                available=merged_available,
                price=merged_price,
                variant_of=merged_variant,
                location=(locs[0] if locs else base.location),
                locations=(locs or None),
                location_links=(location_links or None),
                billing_cycles=merged_cycles_or_none,
                cycle_prices=merged_cycle_prices_or_none,
                specs=merged_specs_or_none,
                is_special=any(p.is_special for p in items),
            )
        )

    merged.sort(key=lambda p: (p.domain, _availability_rank(p.available), compact_ws(p.name).lower(), p.id))
    return merged


def _fill_cycle_price_defaults(products: list[Product]) -> list[Product]:
    out: list[Product] = []
    for p in products:
        cycles = list(p.billing_cycles or [])
        cycle_prices = dict(p.cycle_prices or {})
        if cycle_prices and not cycles:
            cycles = list(cycle_prices.keys())
        if p.price and not cycles:
            cycles = ["Monthly"]
        if (not cycle_prices) and p.price and cycles:
            preferred = "Monthly" if "Monthly" in cycles else cycles[0]
            cycle_prices = {preferred: p.price}
        out.append(
            _clone_product(
                p,
                billing_cycles=(cycles or None),
                cycle_prices=(cycle_prices or None),
            )
        )
    return out


def _apply_domain_availability_fallbacks(domain: str, products: list[Product]) -> list[Product]:
    domain_l = (domain or "").lower()
    if domain_l not in {"clients.zgovps.com", "clientarea.gigsgigscloud.com"}:
        return products
    out: list[Product] = []
    for p in products:
        if p.available is None and p.price:
            ul = (p.url or "").lower()
            if "action=add" in ul and any(k in ul for k in ("id=", "pid=", "product_id=")):
                out.append(_clone_product(p, available=True))
                continue
        out.append(p)
    return out


def _spec_value_key(value: str | None) -> str:
    text = compact_ws(value or "").lower()
    text = text.replace(" ", "")
    for token in ("bandwidth", "traffic", "transfer", "bandwidthtraffic", "/month", "/mo", "/monthly", "/月"):
        text = text.replace(token, "")
    return text.strip(":：-")


def _clean_specs_dict(specs: dict[str, str] | None) -> dict[str, str] | None:
    if not specs:
        return None
    out: dict[str, str] = {}
    for key, value in specs.items():
        if not key:
            continue
        k = compact_ws(str(key))
        if not k:
            continue
        if k.lower() == "cycles":
            continue
        out[k] = compact_ws(str(value))

    bw = out.get("Bandwidth")
    tr = out.get("Traffic")
    if bw and tr and _spec_value_key(bw) == _spec_value_key(tr):
        out.pop("Traffic", None)

    bwt = out.get("BandwidthTraffic")
    if bw and bwt and _spec_value_key(bw) == _spec_value_key(bwt):
        out.pop("BandwidthTraffic", None)
    if tr and bwt and _spec_value_key(tr) == _spec_value_key(bwt):
        out.pop("BandwidthTraffic", None)

    return out or None


def _is_generic_tier_name(name: str | None) -> bool:
    n = compact_ws(name or "").lower()
    return n in {"starter", "standard", "pro", "premium"}


_DMIT_CODE_RE = re.compile(r"\b([A-Za-z0-9]+(?:\.[A-Za-z0-9]+){2,})\b")

# DMIT dotted name pattern: Location.Network.Tier.Plan (e.g., LAX.AN5.Pro.STARTER)
_DMIT_DOTTED_NAME_RE = re.compile(
    r"(?:LAX|HKG|TYO|SJC|NRT|SGP|ICN|CDG|FRA|LHR|AMS|MIA|ORD|SEA|DFW|ATL|IAD)"
    r"[.](?:[A-Za-z0-9]+[.]){1,3}[A-Za-z0-9]+(?:v\d+)?",
    re.IGNORECASE,
)


def _build_dmit_pid_map(html: str) -> dict[int, dict]:
    """Parse DMIT's custom WHMCS cart.php listing page to extract PID → full name + stock mapping.

    DMIT uses cart-products-item divs with:
      - cart-products-box[pid=N] (with optional 'none-stock' class for OOS)
      - cart-products-title containing the full dotted name
      - cart-products-price containing the price
    """
    result: dict[int, dict] = {}
    if not html:
        return result
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return result

    for item in soup.select(".cart-products-item"):
        box = item.select_one(".cart-products-box[pid]")
        if not box:
            continue
        pid_raw = box.get("pid", "").strip()
        if not pid_raw.isdigit():
            continue
        pid = int(pid_raw)

        title_el = item.select_one(".cart-products-title")
        full_name = compact_ws(title_el.get_text(strip=True)) if title_el else ""
        if not full_name:
            continue

        box_classes = " ".join(box.get("class", []))
        is_oos = "none-stock" in box_classes

        # Extract price
        price_el = item.select_one(".cart-products-price")
        price_text = compact_ws(price_el.get_text(" ", strip=True)) if price_el else ""
        price, currency = extract_price(price_text)

        # Extract specs from product features list
        specs: dict[str, str] = {}
        for feature in item.select(".cart-products-feature, .cart-products-features li, .product-feature"):
            feat_text = compact_ws(feature.get_text(strip=True))
            if feat_text:
                s = extract_specs(feat_text)
                if s:
                    specs.update(s)
        # If no structured specs, try the entire item text
        if not specs:
            item_text = compact_ws(item.get_text(" ", strip=True))
            specs = extract_specs(item_text) or {}

        result[pid] = {
            "name": full_name,
            "available": not is_oos,
            "price": price,
            "currency": currency,
            "specs": specs or None,
        }
    return result


def _dmit_map_to_products(pid_map: dict[int, dict], *, base_url: str, domain: str) -> list[Product]:
    """Convert DMIT PID mapping into Product objects."""
    products: list[Product] = []
    p = urlparse(base_url)
    root = f"{p.scheme}://{p.netloc}"
    for pid, entry in sorted(pid_map.items()):
        url = f"{root}/cart.php?a=add&pid={pid}"
        norm = normalize_url_for_id(url)
        product_id = f"{domain}::{norm}"
        products.append(
            Product(
                id=product_id,
                domain=domain,
                url=url,
                name=entry["name"],
                price=entry.get("price"),
                currency=entry.get("currency"),
                description=None,
                specs=entry.get("specs"),
                available=entry.get("available"),
            )
        )
    return products


# Module-level cache so the mapping is built once per scrape run.
_dmit_pid_cache: dict[str, dict[int, dict]] = {}


def _get_dmit_pid_map(client: HttpClient | None, base_url: str) -> dict[int, dict]:
    """Retrieve (and cache) the DMIT PID mapping."""
    cache_key = "dmit"
    if cache_key in _dmit_pid_cache:
        return _dmit_pid_cache[cache_key]
    if client is None:
        return {}
    cart_url = urljoin(base_url, "/cart.php")
    fetch = _fetch_text(client, cart_url, allow_flaresolverr=True)
    if not fetch.ok or not fetch.text:
        _dmit_pid_cache[cache_key] = {}
        return {}
    mapping = _build_dmit_pid_map(fetch.text)
    _dmit_pid_cache[cache_key] = mapping
    return mapping


def _extract_dmit_full_code(product: Product) -> str | None:
    candidates: list[str] = []
    for m in _DMIT_CODE_RE.finditer(product.url or ""):
        candidates.append(m.group(1))
    for m in _DMIT_CODE_RE.finditer(product.description or ""):
        candidates.append(m.group(1))
    if not candidates:
        return None
    cur_name = compact_ws(product.name)
    cur_name_l = cur_name.lower()
    for candidate in candidates:
        tail = candidate.split(".")[-1].lower()
        if cur_name_l == tail:
            return candidate
    return candidates[0]


def _apply_domain_product_cleanup(domain: str, products: list[Product]) -> tuple[list[Product], dict[str, int]]:
    domain_l = (domain or "").lower()
    out: list[Product] = []
    dropped_noise = 0
    renamed = 0

    for p in products:
        specs = _clean_specs_dict(p.specs)
        name = p.name
        location = p.location
        locations = list(p.locations or [])
        location_links = dict(p.location_links or {})
        available = p.available

        if domain_l == "cloud.boil.network":
            url_l = (p.url or "").lower()
            path_l = (urlparse(p.url).path or "").lower() if p.url else ""
            if "/store/" in url_l and "diy-" in path_l:
                dropped_noise += 1
                continue

        if domain_l == "cloud.colocrossing.com":
            if location and "special" in location.lower():
                location = None
            if locations:
                locations = [x for x in locations if "special" not in x.lower()]
            if location_links:
                location_links = {k: v for k, v in location_links.items() if "special" not in k.lower()}

        if domain_l == "clients.zgovps.com" and _is_generic_tier_name(name) and p.variant_of:
            candidate = f"{p.variant_of} - {name}"
            if compact_ws(candidate).lower() != compact_ws(name).lower():
                name = candidate
                renamed += 1

        if domain_l == "www.dmit.io":
            # Try PID-based mapping first (most reliable)
            pid_val = _query_param_int(p.url, "pid")
            dmit_map = _dmit_pid_cache.get("dmit", {})
            if pid_val is not None and pid_val in dmit_map:
                entry = dmit_map[pid_val]
                full_name = entry["name"]
                if full_name and compact_ws(name).lower() != full_name.lower():
                    name = full_name
                    renamed += 1
                # Use listing-page stock status as authoritative for DMIT
                listing_avail = entry.get("available")
                if listing_avail is not None:
                    available = listing_avail
            else:
                # Fallback: extract from URL/description
                dotted = _extract_dmit_full_code(p)
                if dotted and compact_ws(name).lower() != dotted.lower():
                    if compact_ws(name).lower() == dotted.split(".")[-1].lower() or len(compact_ws(name)) <= 10:
                        name = dotted
                        renamed += 1

        out.append(
            Product(
                id=p.id,
                domain=p.domain,
                url=p.url,
                name=name,
                price=p.price,
                currency=p.currency,
                description=p.description,
                specs=specs,
                available=available,
                raw=p.raw,
                variant_of=p.variant_of,
                location=location,
                locations=(locations or None),
                location_links=(location_links or None),
                billing_cycles=p.billing_cycles,
                cycle_prices=p.cycle_prices,
                is_special=p.is_special,
            )
        )

    # app.vmiss.com: remove duplicate semantic traffic tags after full normalization.
    if domain_l == "app.vmiss.com":
        normalized: list[Product] = []
        for p in out:
            normalized.append(
                Product(
                    id=p.id,
                    domain=p.domain,
                    url=p.url,
                    name=p.name,
                    price=p.price,
                    currency=p.currency,
                    description=p.description,
                    specs=_clean_specs_dict(p.specs),
                    available=p.available,
                    raw=p.raw,
                    variant_of=p.variant_of,
                    location=p.location,
                    locations=p.locations,
                    location_links=p.location_links,
                    billing_cycles=p.billing_cycles,
                    cycle_prices=p.cycle_prices,
                    is_special=p.is_special,
                )
            )
        out = normalized

    special_count = sum(1 for p in out if p.is_special)
    return out, {"dropped_noise": dropped_noise, "renamed": renamed, "special": special_count}


def _product_to_state_record(product: Product, now: str, *, first_seen: str | None = None) -> dict:
    return {
        "domain": product.domain,
        "url": product.url,
        "name": product.name,
        "price": product.price,
        "currency": product.currency,
        "description": product.description,
        "specs": product.specs,
        "variant_of": product.variant_of,
        "location": product.location,
        "locations": (product.locations or _product_locations(product) or None),
        "location_links": (product.location_links or _location_links_for_product(product) or None),
        "billing_cycles": product.billing_cycles,
        "cycle_prices": product.cycle_prices,
        "is_special": bool(product.is_special),
        "available": product.available,
        "first_seen": first_seen or now,
        "last_seen": now,
        "last_change": now,
        "last_notified_new": None,
        "last_notified_restock": None,
        "last_notified_new_location": None,
    }


def _update_state_from_runs(
    previous_state: dict,
    runs: list[DomainRun],
    *,
    dry_run: bool,
    timeout_seconds: float,
    prune_missing_products: bool,
    prune_removed_domains: bool = False,
    active_domains: set[str] | None = None,
) -> tuple[dict, RunSummary]:
    state = deepcopy(previous_state)
    state.setdefault("products", {})
    state.setdefault("domains", {})

    started_at = state.get("last_run", {}).get("started_at") or utc_now_iso()
    now = utc_now_iso()

    telegram_cfg = None if dry_run else load_telegram_config()

    restocks = 0
    new_products = 0
    domains_ok = 0
    domains_error = 0

    existing_variant_keys: set[tuple[str, str]] = set()
    for rec in (state.get("products") or {}).values():
        if not isinstance(rec, dict):
            continue
        d = rec.get("domain")
        v = rec.get("variant_of")
        if isinstance(d, str) and isinstance(v, str) and d and v:
            existing_variant_keys.add((d, v))

    if prune_removed_domains and active_domains:
        active = {d.lower() for d in active_domains if isinstance(d, str) and d}
        for domain in list((state.get("domains") or {}).keys()):
            if domain.lower() not in active:
                state["domains"].pop(domain, None)
        for pid, rec in list((state.get("products") or {}).items()):
            if not isinstance(rec, dict):
                continue
            rec_domain = str(rec.get("domain") or "").lower()
            if rec_domain and rec_domain not in active:
                state["products"].pop(pid, None)

    for run in runs:
        domain = run.domain
        if run.ok:
            domains_ok += 1
            state["domains"][domain] = {
                "last_status": "ok",
                "last_ok": now,
                "last_error": None,
                "last_duration_ms": run.duration_ms,
            }
        else:
            domains_error += 1
            prev_domain = state["domains"].get(domain, {})
            state["domains"][domain] = {
                "last_status": "error",
                "last_ok": prev_domain.get("last_ok"),
                "last_error": run.error,
                "last_duration_ms": run.duration_ms,
            }
            continue

        if prune_missing_products:
            # Full mode prunes products that disappeared from a successful domain crawl.
            seen_ids = {p.id for p in run.products}
            for pid, rec in list((state.get("products") or {}).items()):
                if not isinstance(rec, dict):
                    continue
                if rec.get("domain") != domain:
                    continue
                if pid not in seen_ids:
                    state["products"].pop(pid, None)

        for product in run.products:
            product = _product_with_special_flag(product)
            prev = state["products"].get(product.id)
            if not prev:
                state["products"][product.id] = _product_to_state_record(product, now)
                new_products += 1
                had_variant = bool(product.variant_of and (product.domain, product.variant_of) in existing_variant_keys)
                is_new_location = bool(product.location and had_variant)
                if product.variant_of:
                    existing_variant_keys.add((product.domain, product.variant_of))
                if telegram_cfg:
                    if is_new_location and product.available is not False:
                        if _notify_new_location(telegram_cfg, product, now, timeout_seconds=timeout_seconds):
                            state["products"][product.id]["last_notified_new_location"] = now
                    elif product.available is True:
                        if _notify_new_product(telegram_cfg, product, now, timeout_seconds=timeout_seconds):
                            state["products"][product.id]["last_notified_new"] = now
                continue

            prev_available = prev.get("available")
            next_available = product.available

            changed = (
                prev.get("name") != product.name
                or prev.get("price") != product.price
                or prev_available != next_available
                or prev.get("url") != product.url
                or prev.get("specs") != product.specs
                or prev.get("variant_of") != product.variant_of
                or (prev.get("location") or prev.get("option")) != product.location
                or (prev.get("locations") or []) != (product.locations or _product_locations(product) or [])
                or (prev.get("location_links") or {}) != (product.location_links or _location_links_for_product(product) or {})
                or prev.get("billing_cycles") != product.billing_cycles
                or prev.get("cycle_prices") != product.cycle_prices
                or bool(prev.get("is_special")) != bool(product.is_special)
            )
            if changed:
                prev["last_change"] = now

            prev.update(
                {
                    "domain": product.domain,
                    "url": product.url,
                    "name": product.name,
                    "price": product.price,
                    "currency": product.currency,
                    "description": product.description,
                    "specs": product.specs,
                    "variant_of": product.variant_of,
                    "location": product.location,
                    "locations": (product.locations or _product_locations(product) or None),
                    "location_links": (product.location_links or _location_links_for_product(product) or None),
                    "billing_cycles": product.billing_cycles,
                    "cycle_prices": product.cycle_prices,
                    "is_special": bool(product.is_special),
                    "available": next_available,
                    "last_seen": now,
                }
            )

            is_restock = (prev_available is False) and (next_available is True)
            if is_restock:
                restocks += 1
                if telegram_cfg and prev.get("last_notified_restock") != now:
                    if _notify_restock(telegram_cfg, product, now, timeout_seconds=timeout_seconds):
                        prev["last_notified_restock"] = now

    finished_at = utc_now_iso()
    state["last_run"] = {"started_at": started_at, "finished_at": finished_at}
    return state, RunSummary(
        started_at=started_at,
        finished_at=finished_at,
        restocks=restocks,
        new_products=new_products,
        domains_ok=domains_ok,
        domains_error=domains_error,
    )


def _notify_restock(cfg, product: Product, now: str, *, timeout_seconds: float) -> bool:
    msg = _format_message("RESTOCK ALERT", "RESTOCK", product, now)
    return send_telegram_html(cfg=cfg, message_html=msg, timeout_seconds=min(15.0, timeout_seconds))


def _notify_new_product(cfg, product: Product, now: str, *, timeout_seconds: float) -> bool:
    msg = _format_message("NEW PRODUCT", "NEW", product, now)
    return send_telegram_html(cfg=cfg, message_html=msg, timeout_seconds=min(15.0, timeout_seconds))


def _notify_new_location(cfg, product: Product, now: str, *, timeout_seconds: float) -> bool:
    msg = _format_message("NEW LOCATION", "LOCATION", product, now)
    return send_telegram_html(cfg=cfg, message_html=msg, timeout_seconds=min(15.0, timeout_seconds))


def _compose_message_name(product: Product) -> str:
    variant = compact_ws(product.variant_of or "")
    base_name = compact_ws(product.name or "")
    loc = compact_ws(product.location or "")

    pieces: list[str] = []
    if _is_generic_tier_name(base_name) and variant:
        pieces.extend([variant, base_name])
    elif variant and base_name and variant.lower() not in base_name.lower():
        pieces.extend([variant, base_name])
    elif base_name:
        pieces.append(base_name)
    elif variant:
        pieces.append(variant)
    else:
        pieces.append(product.domain)

    deduped: list[str] = []
    seen: set[str] = set()
    for part in pieces:
        key = _normalize_name_key(part)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(part)

    display = " - ".join(deduped) if deduped else product.domain
    if loc and loc.lower() not in display.lower():
        display = f"{display} ({loc})"
    if product.is_special and not display.startswith("⭐ "):
        display = f"⭐ {display}"
    return display


def _format_message(kind: str, icon: str, product: Product, now: str) -> str:
    _ICONS = {
        "RESTOCK": "🔄",
        "NEW": "🆕",
        "LOCATION": "📍",
    }
    _STATUS_ICONS = {
        True: "🟢",
        False: "🔴",
        None: "🟡",
    }
    emoji = _ICONS.get(icon, "📢")
    domain_tag = _telegram_domain_tag(product.domain)

    # Use original product name parsed from the raw data if available, as fallback use composed name.
    raw_data = product.raw or {}
    original_name = str(raw_data.get("name") or product.name or "").strip()
    if not original_name or original_name == "None":
        original_name = _compose_message_name(product)
    elif product.variant_of and compact_ws(product.variant_of).lower() not in original_name.lower():
        original_name = f"{product.variant_of} - {original_name}"

    if product.is_special and not original_name.startswith("⭐ "):
        original_name = f"⭐ {original_name}"

    parts: list[str] = [f"{emoji} <b>{h(kind)}</b>  ·  <b>#{h(domain_tag)}</b>"]
    parts.append(f"<b>{h(original_name)}</b>")

    info_parts: list[str] = []
    status_icon = _STATUS_ICONS.get(product.available, "🟡")
    if product.available is True:
        info_parts.append(f"{status_icon} In Stock")
    elif product.available is False:
        info_parts.append(f"{status_icon} Out of Stock")
    else:
        info_parts.append(f"{status_icon} Unknown")
    if product.price:
        info_parts.append(f"💵 {h(product.price)}")
    msg_location = product.location
    if product.locations and len(product.locations) > 1:
        msg_location = f"{product.locations[0]} +{len(product.locations) - 1} more"
    if msg_location:
        info_parts.append(f"📍 {h(msg_location)}")
    parts.append("  ·  ".join(info_parts))

    if product.cycle_prices:
        order = ["Monthly", "Quarterly", "Semiannual", "Yearly", "Biennial", "Triennial", "Quadrennial", "Quinquennial", "One-Time"]
        items = sorted(product.cycle_prices.items(), key=lambda kv: (order.index(kv[0]) if kv[0] in order else 999, kv[0]))
        cp_lines = [f"{k}: {v}" for k, v in items]
        parts.append(f"<pre>{h(chr(10).join(cp_lines))}</pre>")
    elif product.billing_cycles:
        parts.append(f"🔁 {h(', '.join(product.billing_cycles))}")

    if product.specs:
        prio = ["CPU", "RAM", "Disk", "Storage", "Transfer", "Traffic", "Bandwidth", "Port", "IPv4", "IPv6", "Location", "Data Center"]
        filtered_specs = [(k, v) for k, v in product.specs.items() if compact_ws(k).lower() != "cycles"]
        items = sorted(filtered_specs, key=lambda kv: (prio.index(kv[0]) if kv[0] in prio else 999, str(kv[0])))
        spec_lines = [f"{k}: {v}" for k, v in items[:15] if k and v]
        if spec_lines:
            parts.append(f"<b>Specs:</b>\n<pre>{h(chr(10).join(spec_lines))}</pre>")

    if product.description and product.description.strip():
        desc = product.description.strip()
        if len(desc) > 300:
            desc = desc[:300] + "..."
        parts.append(f"<i>{h(desc)}</i>")

    parts.append(f'🔗 <a href="{h(product.url)}">Open Product Page</a>')
    parts.append(f"<code>{h(now)}</code>")

    message = "\n".join(parts)
    return message[:3900]

def run_monitor(
    *,
    previous_state: dict,
    targets: list[str],
    timeout_seconds: float,
    max_workers: int,
    dry_run: bool,
    mode: str = "full",
) -> tuple[dict, RunSummary]:
    mode = (mode or "full").strip().lower()
    if mode not in {"full", "lite"}:
        mode = "full"

    configured_targets = targets or DEFAULT_TARGETS
    explicit_targets = bool(targets)

    proxy_url = os.getenv("PROXY_URL", "").strip() or None
    flaresolverr_url = os.getenv("FLARESOLVERR_URL", "").strip() or None
    client = HttpClient(
        timeout_seconds=timeout_seconds,
        proxy_url=proxy_url,
        flaresolverr_url=flaresolverr_url,
    )

    started_at = utc_now_iso()
    previous_state = deepcopy(previous_state)
    previous_state.setdefault("last_run", {})["started_at"] = started_at

    effective_targets = configured_targets
    allow_expansion = True
    prune_missing_products = not explicit_targets
    prune_removed_domains = not explicit_targets
    if mode == "lite":
        effective_targets = _select_lite_targets(previous_state=previous_state, fallback_targets=configured_targets)
        allow_expansion = False
        prune_missing_products = not explicit_targets
        prune_removed_domains = not explicit_targets

    raw_runs: list[DomainRun] = []
    log_enabled = os.getenv("MONITOR_LOG", "1").strip() != "0"
    total_targets = len(effective_targets)
    if log_enabled:
        print(
            (
                f"[monitor] start mode={mode} configured_targets={len(configured_targets)} "
                f"selected_targets={total_targets} max_workers={max_workers} "
                f"timeout_seconds={timeout_seconds} allow_expansion={allow_expansion} "
                f"prune_missing_products={prune_missing_products} "
                f"prune_removed_domains={prune_removed_domains}"
            ),
            flush=True,
        )
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(_scrape_target, client, target, allow_expansion=allow_expansion): target
            for target in effective_targets
        }
        for fut in as_completed(futures):
            target = futures[fut]
            run = fut.result()
            raw_runs.append(run)
            if log_enabled:
                completed += 1
                in_stock = sum(1 for p in run.products if p.available is True)
                out_of_stock = sum(1 for p in run.products if p.available is False)
                unknown = len(run.products) - in_stock - out_of_stock
                special = sum(1 for p in run.products if p.is_special)
                if run.ok:
                    print(
                        (
                            f"[monitor] progress={completed}/{total_targets} target={target} domain={run.domain} "
                            f"status=ok products={len(run.products)} in_stock={in_stock} out_of_stock={out_of_stock} "
                            f"unknown={unknown} special={special} duration_ms={run.duration_ms}"
                        ),
                        flush=True,
                    )
                else:
                    print(
                        (
                            f"[monitor] progress={completed}/{total_targets} target={target} domain={run.domain} "
                            f"status=error products=0 duration_ms={run.duration_ms} error={run.error or 'fetch failed'}"
                        ),
                        flush=True,
                    )

    # Normalize per-domain runs before updating state. This prevents mode-dependent
    # behavior when multiple targets map to the same domain.
    domain_target_counts: dict[str, int] = {}
    for run in raw_runs:
        domain_target_counts[run.domain] = domain_target_counts.get(run.domain, 0) + 1
    runs = _merge_runs_by_domain(raw_runs)
    if log_enabled:
        print(
            (
                f"[monitor] merge mode={mode} target_runs={len(raw_runs)} raw_domains={len(domain_target_counts)} "
                f"merged_domains={len(runs)}"
            ),
            flush=True,
        )
        for run in runs:
            in_stock = sum(1 for p in run.products if p.available is True)
            out_of_stock = sum(1 for p in run.products if p.available is False)
            unknown = len(run.products) - in_stock - out_of_stock
            special = sum(1 for p in run.products if p.is_special)
            merged_targets = domain_target_counts.get(run.domain, 0)
            if run.ok:
                print(
                    (
                        f"[{run.domain}] status=ok merged_targets={merged_targets} products={len(run.products)} "
                        f"in_stock={in_stock} out_of_stock={out_of_stock} unknown={unknown} special={special} "
                        f"duration_ms={run.duration_ms}"
                    ),
                    flush=True,
                )
            else:
                print(
                    (
                        f"[{run.domain}] status=error merged_targets={merged_targets} products=0 "
                        f"duration_ms={run.duration_ms} error={run.error or 'fetch failed'}"
                    ),
                    flush=True,
                )

    next_state, summary = _update_state_from_runs(
        previous_state,
        runs,
        dry_run=dry_run,
        timeout_seconds=timeout_seconds,
        prune_missing_products=prune_missing_products,
        prune_removed_domains=prune_removed_domains,
        active_domains={_domain_from_url(t) for t in configured_targets if _is_http_url(t)},
    )
    if log_enabled:
        tracked_products = len((next_state.get("products") or {}))
        print(
            (
                f"[monitor] done mode={mode} domains_ok={summary.domains_ok} domains_error={summary.domains_error} "
                f"new_products={summary.new_products} restocks={summary.restocks} tracked_products={tracked_products} "
                f"started_at={summary.started_at} finished_at={summary.finished_at}"
            ),
            flush=True,
        )
    return next_state, summary


def _is_http_url(value: str) -> bool:
    v = (value or "").strip().lower()
    return v.startswith("http://") or v.startswith("https://")


def _select_lite_targets(*, previous_state: dict, fallback_targets: list[str]) -> list[str]:
    default_targets = fallback_targets or DEFAULT_TARGETS
    by_domain: dict[str, str] = {}
    for target in default_targets:
        if not _is_http_url(target):
            continue
        by_domain[_domain_from_url(target)] = target

    state_domains: list[str] = []
    seen_domains: set[str] = set()

    for domain in (previous_state.get("domains") or {}).keys():
        if isinstance(domain, str) and domain and domain not in seen_domains:
            seen_domains.add(domain)
            state_domains.append(domain)

    for rec in (previous_state.get("products") or {}).values():
        if not isinstance(rec, dict):
            continue
        domain = rec.get("domain")
        if not isinstance(domain, str) or not domain:
            continue
        if domain not in seen_domains:
            seen_domains.add(domain)
            state_domains.append(domain)

    out: list[str] = []
    seen_targets: set[str] = set()
    for domain in state_domains:
        target = by_domain.get(domain)
        if not target or target in seen_targets:
            continue
        seen_targets.add(target)
        out.append(target)

    return out or default_targets


def _merge_runs_by_domain(runs: list[DomainRun]) -> list[DomainRun]:
    merged: dict[str, dict] = {}
    for run in runs:
        rec = merged.setdefault(run.domain, {"duration_ms": 0, "ok": False, "errors": [], "products": {}})
        rec["duration_ms"] += int(run.duration_ms or 0)
        if run.ok:
            rec["ok"] = True
            for product in run.products:
                rec["products"][product.id] = product
        elif run.error:
            rec["errors"].append(str(run.error))

    out: list[DomainRun] = []
    for domain in sorted(merged.keys()):
        rec = merged[domain]
        if rec["ok"]:
            merged_products = _merge_products_by_canonical_plan(list(rec["products"].values()))
            out.append(
                DomainRun(
                    domain=domain,
                    ok=True,
                    error=None,
                    duration_ms=rec["duration_ms"],
                    products=merged_products,
                )
            )
            continue
        errors = rec["errors"][:3]
        error_msg = "; ".join(errors) if errors else "fetch failed"
        out.append(DomainRun(domain=domain, ok=False, error=error_msg, duration_ms=rec["duration_ms"], products=[]))
    return out


def _scrape_target(client: HttpClient, target: str, *, allow_expansion: bool = True) -> DomainRun:
    domain = _domain_from_url(target)
    log_enabled = os.getenv("MONITOR_LOG", "1").strip() != "0"
    hidden_scan_denylist = {"cloud.tizz.yt"}
    started = time.perf_counter()
    raw_target_budget = os.getenv("TARGET_MAX_DURATION_SECONDS", "210").strip()
    try:
        target_budget_seconds = float(raw_target_budget) if raw_target_budget else 210.0
    except Exception:
        target_budget_seconds = 210.0
    deadline = (started + target_budget_seconds) if target_budget_seconds > 0 else None

    def _time_left_seconds() -> float | None:
        if deadline is None:
            return None
        return deadline - time.perf_counter()

    def _deadline_exceeded() -> bool:
        rem = _time_left_seconds()
        return rem is not None and rem <= 0

    fetch = _fetch_text(client, target, allow_flaresolverr=True)
    if (not fetch.ok or not fetch.text) and ("flaresolverr" in (fetch.error or "").lower() or "timed out" in (fetch.error or "").lower()):
        # If the solver is temporarily overloaded, retry once with direct fetch only.
        retry = _fetch_text(client, target, allow_flaresolverr=False)
        if retry.ok and retry.text:
            fetch = retry
    if not fetch.ok or not fetch.text:
        # Some domains return unstable landing pages; try known entry points before failing the run.
        fallback_pages = _dedupe_keep_order(_domain_extra_pages(domain) + _default_entrypoint_pages(target))
        for page in fallback_pages:
            alt = _fetch_text(client, page, allow_flaresolverr=True)
            if (not alt.ok or not alt.text) and ("flaresolverr" in (alt.error or "").lower() or "timed out" in (alt.error or "").lower()):
                alt_retry = _fetch_text(client, page, allow_flaresolverr=False)
                if alt_retry.ok and alt_retry.text:
                    alt = alt_retry
            if alt.ok and alt.text:
                fetch = alt
                break
    if not fetch.ok or not fetch.text:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return DomainRun(domain=domain, ok=False, error=fetch.error or "fetch failed", duration_ms=duration_ms, products=[])

    is_hostbill = _is_hostbill_domain(domain, fetch.text)
    is_whmcs = _is_whmcs_domain(domain, fetch.text)
    scan_platform = "hostbill" if is_hostbill else ("whmcs" if is_whmcs else "")
    parser = get_parser_for_domain(domain)

    # Pre-build DMIT PID mapping from the cart.php listing page.
    # This allows _apply_domain_product_cleanup to rename products correctly.
    if domain == "www.dmit.io":
        # If the initial fetch was from cart.php, use it; otherwise build from the page.
        if "cart.php" in (fetch.url or "").lower() and not parse_qs(urlparse(fetch.url).query).get("a"):
            mapping = _build_dmit_pid_map(fetch.text)
            _dmit_pid_cache["dmit"] = mapping
        else:
            _get_dmit_pid_map(client, fetch.url)

    try:
        if log_enabled:
            print(f"[scrape:{domain}] stage=simple_parse start", flush=True)
        products = [_product_with_special_flag(p) for p in parser.parse(fetch.text, base_url=fetch.url)]
        products = [p for p in products if not _looks_like_noise_product(p)]
        deduped: dict[str, Product] = {p.id: p for p in products}

        # DMIT: inject products directly from the cart.php listing PID map.
        # The generic parser can't pick up DMIT's hidden-div custom template.
        if domain == "www.dmit.io":
            dmit_map = _dmit_pid_cache.get("dmit", {})
            if dmit_map:
                dmit_products = _dmit_map_to_products(dmit_map, base_url=fetch.url, domain=domain)
                for dp in dmit_products:
                    dp = _product_with_special_flag(dp)
                    if dp.id not in deduped:
                        deduped[dp.id] = dp
                    elif not deduped[dp.id].price and dp.price:
                        # Prefer the listing entry if it has more info
                        deduped[dp.id] = dp

        initial_product_count = len(deduped)
        initial_ids = set(deduped.keys())
        hidden_allowed = bool(scan_platform and domain not in hidden_scan_denylist)
        parallel_simple_hidden = os.getenv("PARALLEL_SIMPLE_HIDDEN", "1").strip() != "0"
        hidden_future = None
        hidden_executor = None

        # Pre-compute candidate pages once 鈥?avoids calling _discover_candidate_pages twice
        # (once inside _should_force_discovery and again below).
        initial_candidates = _discover_candidate_pages(fetch.text, base_url=fetch.url, domain=domain) if allow_expansion else []
        if allow_expansion and hidden_allowed and parallel_simple_hidden and (not _deadline_exceeded()):
            raw_hidden_budget = os.getenv("WHMCS_HIDDEN_MAX_DURATION_SECONDS", "180").strip()
            try:
                hidden_budget_seconds = float(raw_hidden_budget) if raw_hidden_budget else 60.0
            except Exception:
                hidden_budget_seconds = 60.0
            hidden_deadline = deadline
            if hidden_budget_seconds > 0:
                now_ts = time.perf_counter()
                local_deadline = now_ts + hidden_budget_seconds
                hidden_deadline = local_deadline if hidden_deadline is None else min(hidden_deadline, local_deadline)

            seed_urls = []
            seed_urls.extend(_domain_extra_pages(domain))
            seed_urls.extend(initial_candidates)
            seed_urls.extend(_default_entrypoint_pages(fetch.url))
            seed_urls = _dedupe_keep_order(seed_urls)
            if log_enabled:
                print(
                    f"[scrape:{domain}] stage=hidden_scan start mode=parallel seeds={len(seed_urls)} base_products={len(deduped)}",
                    flush=True,
                )
            hidden_executor = ThreadPoolExecutor(max_workers=1)
            _dmit_seed_pids = sorted(_dmit_pid_cache.get("dmit", {}).keys()) if domain == "www.dmit.io" else None
            hidden_future = hidden_executor.submit(
                _scan_whmcs_hidden_products,
                client,
                parser,
                base_url=fetch.url,
                existing_ids=set(deduped.keys()),
                seed_urls=seed_urls,
                seed_pids=_dmit_seed_pids,
                deadline=hidden_deadline,
                platform=scan_platform,
                skip_gid=(domain == "www.dmit.io"),
            )

        if allow_expansion and (not _deadline_exceeded()) and (
            _needs_discovery(products, base_url=fetch.url)
            or _should_force_discovery_with_candidates(initial_candidates, product_count=len(deduped), base_url=fetch.url)
        ):
            raw_page_limit = os.environ.get("DISCOVERY_MAX_PAGES_PER_DOMAIN")
            max_pages_limit = int(raw_page_limit) if raw_page_limit and raw_page_limit.strip() else 40
            if max_pages_limit <= 0:
                # Treat 0/negative as "disable discovery" to avoid useless crawl loops.
                max_pages_limit = 0
            # Only apply higher defaults when the user hasn't explicitly configured a limit.
            if raw_page_limit is None:
                if domain == "greencloudvps.com":
                    max_pages_limit = max(max_pages_limit, 200)
                if is_whmcs:
                    max_pages_limit = max(max_pages_limit, 128)
                if is_hostbill:
                    # HostBill-style carts often require an extra discovery hop from category -> products.
                    max_pages_limit = max(max_pages_limit, 96)

            if max_pages_limit > 0:
                discovered = []
                # Try domain-specific extra pages (including SPA API endpoints) first so we don't
                # abort discovery after a streak of 404/blocked default entry points.
                discovered.extend(_domain_extra_pages(domain))
                # Avoid brute-enumerating gid pages here; it is expensive on Cloudflare sites.
                # Hidden scanning and normal link discovery handle unlinked/sparse product groups.
                discovered.extend(initial_candidates)
                discovered.extend(_default_entrypoint_pages(fetch.url))
                discovered = _dedupe_keep_order([u for u in discovered if u and u != fetch.url])
                discovered_seen = set(discovered)

                max_products = int(os.getenv("DISCOVERY_MAX_PRODUCTS_PER_DOMAIN", "2000"))
                # WHMCS category pages may be sparse/non-contiguous; avoid stopping too early.
                default_stop = "0" if (is_whmcs or is_hostbill) else "12"
                stop_after_no_new = int(os.getenv("DISCOVERY_STOP_AFTER_NO_NEW_PAGES", default_stop))
                fetch_error_default = "0" if (is_whmcs or is_hostbill) else "12"
                stop_after_fetch_errors = int(os.getenv("DISCOVERY_STOP_AFTER_FETCH_ERRORS", fetch_error_default))
                raw_strict_stop = os.getenv("DISCOVERY_STRICT_FETCH_ERROR_STOP", "").strip().lower()
                if raw_strict_stop in {"1", "true", "yes", "on"}:
                    strict_fetch_error_stop = True
                elif raw_strict_stop in {"0", "false", "no", "off"}:
                    strict_fetch_error_stop = False
                else:
                    strict_fetch_error_stop = not (is_whmcs or is_hostbill)
                if is_hostbill:
                    # HostBill sites often discover real product pages only after several category hops.
                    stop_after_no_new = 0
                no_new_streak = 0
                fetch_error_streak = 0
                pages_visited = 0
                new_pages_discovered = 0
                discovery_stop_reason = "queue_exhausted"
                discovery_workers = int(os.getenv("DISCOVERY_WORKERS", "6"))
                discovery_workers = max(1, min(discovery_workers, 16))
                discovery_batch = int(os.getenv("DISCOVERY_BATCH", "10"))
                discovery_batch = max(1, min(discovery_batch, 20))

                queue_idx = 0
                if log_enabled:
                    print(
                        (
                            f"[scrape:{domain}] stage=discovery start queued={len(discovered)} "
                            f"max_pages={max_pages_limit} stop_no_new={stop_after_no_new} "
                            f"stop_fetch_errors={stop_after_fetch_errors} strict_fetch_stop={strict_fetch_error_stop}"
                        ),
                        flush=True,
                    )

                def fetch_one(page_url: str):
                    allow_solver = _should_use_flaresolverr_for_discovery_page(page_url)
                    page_fetch = _fetch_text(client, page_url, allow_flaresolverr=allow_solver)
                    if (not page_fetch.ok or not page_fetch.text) and (not allow_solver) and _is_blocked_fetch(page_fetch):
                        # Retry blocked pages with FlareSolverr only when needed.
                        page_fetch = _fetch_text(client, page_url, allow_flaresolverr=True)
                    return page_fetch

                with ThreadPoolExecutor(max_workers=discovery_workers) as ex:
                    while queue_idx < len(discovered):
                        if _deadline_exceeded():
                            discovery_stop_reason = "deadline"
                            break
                        if pages_visited >= max_pages_limit:
                            discovery_stop_reason = "max_pages"
                            break
                        remaining = max_pages_limit - pages_visited
                        batch_n = min(discovery_batch, remaining, len(discovered) - queue_idx)
                        batch_urls = discovered[queue_idx : queue_idx + batch_n]
                        queue_idx += batch_n
                        pages_visited += batch_n

                        futs = {u: ex.submit(fetch_one, u) for u in batch_urls}
                        fetched: dict[str, object] = {}
                        for u in batch_urls:
                            try:
                                fetched[u] = futs[u].result()
                            except Exception:
                                fetched[u] = None

                        for page_url in batch_urls:
                            if _deadline_exceeded():
                                queue_idx = len(discovered)
                                break
                            page_fetch = fetched.get(page_url)
                            if not page_fetch or not getattr(page_fetch, "ok", False) or not getattr(page_fetch, "text", None):
                                fetch_error_streak += 1
                                if stop_after_fetch_errors > 0 and fetch_error_streak >= stop_after_fetch_errors and _is_primary_listing_page(page_url):
                                    queue_has_pending = queue_idx < len(discovered)
                                    if strict_fetch_error_stop or (not queue_has_pending):
                                        discovery_stop_reason = "fetch_error_streak"
                                        queue_idx = len(discovered)
                                        break
                                continue
                            fetch_error_streak = 0

                            page_products = [_product_with_special_flag(p) for p in parser.parse(page_fetch.text, base_url=page_fetch.url)]
                            page_products = [p for p in page_products if not _looks_like_noise_product(p)]
                            new_count = 0
                            for p in page_products:
                                if p.id not in deduped:
                                    new_count += 1
                                    deduped[p.id] = p
                                else:
                                    existing = deduped[p.id]
                                    if existing.available is False or p.available is False:
                                        merged_avail = False
                                    elif existing.available is True or p.available is True:
                                        merged_avail = True
                                    else:
                                        merged_avail = None
                                    
                                    p = _clone_product(p, available=merged_avail)
                                    if not p.name and existing.name:
                                        p = _clone_product(p, name=existing.name)
                                    deduped[p.id] = p

                            # Also discover more pages from this page's links.
                            more_pages = _discover_candidate_pages(page_fetch.text, base_url=page_fetch.url, domain=domain)
                            for mp in more_pages:
                                if mp and mp not in discovered_seen and mp != fetch.url:
                                    discovered_seen.add(mp)
                                    discovered.append(mp)
                                    new_pages_discovered += 1

                            if new_count == 0:
                                no_new_streak += 1
                            else:
                                no_new_streak = 0

                            if len(deduped) >= max_products:
                                discovery_stop_reason = "max_products"
                                queue_idx = len(discovered)
                                break
                            if stop_after_no_new > 0 and no_new_streak >= stop_after_no_new and _is_primary_listing_page(page_fetch.url):
                                discovery_stop_reason = "no_new_pages_streak"
                                queue_idx = len(discovered)
                                break

                products = list(deduped.values())
                if log_enabled:
                    print(
                        (
                            f"[scrape:{domain}] stage=discovery done queued={len(discovered)} visited={pages_visited} "
                            f"new_pages={new_pages_discovered} added_products={len(deduped) - initial_product_count} "
                            f"stop_reason={discovery_stop_reason}"
                        ),
                        flush=True,
                    )
        if log_enabled:
            print(f"[scrape:{domain}] stage=simple_parse done products={len(deduped)}", flush=True)

        # Hidden products (WHMCS/HostBill): brute-scan id/group pages and keep all discovered hits.
        hidden: list[Product] = []
        if hidden_future is not None:
            try:
                hidden = hidden_future.result()
            finally:
                if hidden_executor is not None:
                    hidden_executor.shutdown(wait=False)
            for hp in hidden:
                hp = _product_with_special_flag(hp)
                existing = deduped.get(hp.id)
                if existing is not None:
                    if existing.available is False or hp.available is False:
                        merged_avail = False
                    elif existing.available is True or hp.available is True:
                        merged_avail = True
                    else:
                        merged_avail = None
                    
                    hp = _clone_product(hp, available=merged_avail)
                    if not hp.name and existing.name:
                        hp = _clone_product(hp, name=existing.name)
                deduped[hp.id] = hp
            products = list(deduped.values())
            if log_enabled:
                print(f"[scrape:{domain}] stage=hidden_scan done mode=parallel hidden_products={len(hidden)} total={len(deduped)}", flush=True)
        elif allow_expansion and hidden_allowed and (not _deadline_exceeded()):
            if log_enabled:
                print(f"[scrape:{domain}] stage=hidden_scan start mode=sequential", flush=True)
            raw_hidden_budget = os.getenv("WHMCS_HIDDEN_MAX_DURATION_SECONDS", "180").strip()
            try:
                hidden_budget_seconds = float(raw_hidden_budget) if raw_hidden_budget else 60.0
            except Exception:
                hidden_budget_seconds = 60.0
            hidden_deadline = deadline
            if hidden_budget_seconds > 0:
                now_ts = time.perf_counter()
                local_deadline = now_ts + hidden_budget_seconds
                hidden_deadline = local_deadline if hidden_deadline is None else min(hidden_deadline, local_deadline)
            seed_urls = []
            seed_urls.extend(_domain_extra_pages(domain))
            seed_urls.extend(initial_candidates)
            seed_urls.extend(_default_entrypoint_pages(fetch.url))
            seed_urls = _dedupe_keep_order(seed_urls)
            _dmit_seed_pids2 = sorted(_dmit_pid_cache.get("dmit", {}).keys()) if domain == "www.dmit.io" else None
            hidden = _scan_whmcs_hidden_products(
                client,
                parser,
                base_url=fetch.url,
                existing_ids=set(deduped.keys()),
                seed_urls=seed_urls,
                seed_pids=_dmit_seed_pids2,
                deadline=hidden_deadline,
                platform=scan_platform,
                skip_gid=(domain == "www.dmit.io"),
            )
            for hp in hidden:
                hp = _product_with_special_flag(hp)
                existing = deduped.get(hp.id)
                if existing is not None:
                    if existing.available is False or hp.available is False:
                        merged_avail = False
                    elif existing.available is True or hp.available is True:
                        merged_avail = True
                    else:
                        merged_avail = None
                    
                    hp = _clone_product(hp, available=merged_avail)
                    if not hp.name and existing.name:
                        hp = _clone_product(hp, name=existing.name)
                deduped[hp.id] = hp
            products = list(deduped.values())
            if log_enabled:
                print(f"[scrape:{domain}] stage=hidden_scan done mode=sequential hidden_products={len(hidden)} total={len(deduped)}", flush=True)

        # Some providers only reveal stock state on the product detail page (or render it client-side on listings).
        # Enrich all products with unknown or False availability via detail page fetches.
        _ENRICH_DOMAINS = {
            "backwaves.net",
            "app.vmiss.com",
            "clients.zgovps.com",
            "clientarea.gigsgigscloud.com",
            "www.dmit.io",
            "greencloudvps.com",
        }
        _CYCLE_ENRICH_DOMAINS = {
            "clients.zgovps.com",
            "clientarea.gigsgigscloud.com",
            "www.dmit.io",
        }
        _TRUE_RECHECK_DOMAINS = {
            "clientarea.gigsgigscloud.com",
            "www.dmit.io",
        }
        if allow_expansion and (domain in _ENRICH_DOMAINS or is_whmcs or is_hostbill) and (not _deadline_exceeded()):
            false_only = all(p.available is False for p in products) if products else False
            include_missing_cycles = is_whmcs or is_hostbill or (domain in _CYCLE_ENRICH_DOMAINS)
            enrich_pages = 80 if include_missing_cycles and domain in {"clientarea.gigsgigscloud.com"} else 40
            if is_whmcs:
                enrich_pages = max(enrich_pages, 60)
            if domain in {"bgp.gd", "cloud.colocrossing.com", "clients.zgovps.com"}:
                enrich_pages = max(enrich_pages, 140)
            remaining = _time_left_seconds()
            if remaining is None or remaining >= 20:
                if remaining is not None and remaining < 60:
                    enrich_pages = min(enrich_pages, 12)
                products = _enrich_availability_via_product_pages(
                    client,
                    products,
                    domain=domain,
                    max_pages=enrich_pages,
                    include_false=(false_only or domain in {"backwaves.net"}),
                    include_true=(domain in _TRUE_RECHECK_DOMAINS),
                    include_missing_cycles=include_missing_cycles,
                )

        # Final dedup and noise filter.
        before_post_merge = len(products)
        products = [p for p in {p.id: p for p in products}.values() if not _looks_like_noise_product(p)]
        before_cleanup = len(products)
        products, cleanup_diag = _apply_domain_product_cleanup(domain, products)
        products = _merge_products_by_canonical_plan(products)
        products = _fill_cycle_price_defaults(products)
        products = _apply_domain_availability_fallbacks(domain, products)
        products = [p for p in products if not _looks_like_noise_product(p)]
        products, _ = _apply_domain_product_cleanup(domain, products)
        if log_enabled:
            print(
                (
                    f"[scrape:{domain}] stage=post_merge before={before_post_merge} deduped={before_cleanup} "
                    f"final={len(products)} dropped_noise={cleanup_diag.get('dropped_noise', 0)} "
                    f"renamed={cleanup_diag.get('renamed', 0)} special={cleanup_diag.get('special', 0)}"
                ),
                flush=True,
            )

        final_products_dict = {p.id: p for p in products}
        for pid, orig_p in deduped.items():
            if pid not in initial_ids:
                final_p = final_products_dict.get(pid)
                if final_p is None:
                    print(f"[scrape:{domain}] discover/hidden product dropped: {orig_p.name} - {orig_p.url}", flush=True)
                elif not final_p.available:
                    print(f"[scrape:{domain}] discover/hidden product out of stock: {final_p.name} - {final_p.url}", flush=True)

        duration_ms = int((time.perf_counter() - started) * 1000)
        return DomainRun(domain=domain, ok=True, error=None, duration_ms=duration_ms, products=products)
    except Exception as e:
        try:
            if "hidden_executor" in locals() and hidden_executor is not None:
                hidden_executor.shutdown(wait=False)
        except Exception:
            pass
        duration_ms = int((time.perf_counter() - started) * 1000)
        return DomainRun(domain=domain, ok=False, error=f"{type(e).__name__}: {e}", duration_ms=duration_ms, products=[])


def _dedupe_keep_order(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if not u:
            continue
        key = u
        if u.startswith(("http://", "https://")):
            try:
                key = normalize_url_for_id(u)
            except Exception:
                key = u
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out


def _is_whmcs_domain(domain: str, html: str) -> bool:
    """Detect WHMCS-based storefronts where product groups are separate pages."""
    t = (html or "").lower()
    if "whmcs" in t or "cart.php" in t or "rp=/store" in t:
        return True
    whmcs_domains = {
        "my.rfchost.com", "my.frantech.ca",
        "nmcloud.cc", "bgp.gd", "wap.ac", "www.bagevm.com", "backwaves.net",
        "cloud.ggvision.net", "cloud.colocrossing.com",
        "clients.zgovps.com", "my.racknerd.com",
        "cloud.boil.network",
        "bestvm.cloud", "www.mkcloud.net", "alphavps.com",
    }
    return domain.lower() in whmcs_domains


def _is_hostbill_domain(domain: str, html: str) -> bool:
    domain_l = (domain or "").lower()
    if domain_l in {"clientarea.gigsgigscloud.com", "clients.zgovps.com"}:
        return True
    t = (html or "").lower()
    if any(
        marker in t
        for marker in (
            "index.php?/cart/",
            "?/cart/",
            "action=add&id=",
            "name=\"id\"",
            "name='id'",
            "/cart/&step=",
        )
    ):
        return True
    return False


def _should_force_discovery(html: str, *, base_url: str, domain: str, product_count: int) -> bool:
    """
    Some storefront landing pages only show a small teaser (e.g., one product per category).
    If we can see multiple likely listing pages, force a discovery pass.
    """
    if not html:
        return False
    candidates = _discover_candidate_pages(html, base_url=base_url, domain=domain)
    return _should_force_discovery_with_candidates(candidates, product_count=product_count, base_url=base_url)


def _should_force_discovery_with_candidates(
    candidates: list[str], *, product_count: int, base_url: str
) -> bool:
    """Check if discovery should be forced given pre-computed candidate pages."""
    if len(candidates) < 2:
        return False

    threshold_small = int(os.getenv("DISCOVERY_FORCE_IF_PRODUCTS_LEQ", "6"))
    if product_count <= threshold_small:
        return True

    if _is_primary_listing_page(base_url):
        threshold_listing = int(os.getenv("DISCOVERY_FORCE_IF_PRIMARY_LISTING_PRODUCTS_LEQ", "40"))
        return product_count <= threshold_listing

    return False


def _infer_availability_from_detail_html(
    html: str,
    *,
    domain: str | None = None,
    soup: BeautifulSoup | None = None,
) -> bool | None:
    domain_l = (domain or "").lower()
    page_level_avail = extract_availability(html)
    # Whole-page availability text can be noisy on some storefront themes that include
    # mixed product snippets; it may match "in stock" or "add to cart" from navbars,
    # JS templates, or footer links.  Defer page-level True to the DOM-level checks
    # below which inspect actual buttons / OOS class markers.  Only trust page-level
    # False or numeric counts immediately.
    if page_level_avail is True:
        # Don't return immediately — let DOM-level OOS / button checks run first.
        pass
    if soup is None:
        try:
            soup = BeautifulSoup(html or "", "lxml")
        except Exception:
            return None

    tl = compact_ws(html or "").lower()
    has_order_form_markers = any(k in tl for k in ("billingcycle", "configoption[", "custom["))

    for el in soup.select(".outofstock, .out-of-stock, .soldout, [class*='outofstock'], [class*='soldout'], [class*='unavailable']"):
        txt = compact_ws(getattr(el, "get_text", lambda *a, **k: "")(" ", strip=True))
        marker = extract_availability(txt)
        if marker is False:
            return False
        if marker is None and not txt:
            return False

    enabled_buy_button = False
    for el in soup.select("a, button, input[type='submit'], input[type='button']"):
        cls = " ".join(el.get("class", [])) if hasattr(el, "get") else ""
        cls_l = cls.lower()
        disabled = "disabled" in cls_l or getattr(el, "has_attr", lambda *_: False)("disabled")
        label = compact_ws(getattr(el, "get_text", lambda *a, **k: "")(" ", strip=True))
        if not label and hasattr(el, "get"):
            label = compact_ws(str(el.get("value") or ""))
        marker = extract_availability(label)
        if marker is False:
            return False
        if marker is True and not disabled:
            return True
        if marker is None and not disabled and looks_like_purchase_action(label):
            enabled_buy_button = True
            if has_order_form_markers or domain_l in {"cloud.colocrossing.com"}:
                return True
            # Without form markers, purchase labels alone are weaker; keep evaluating.
            continue

    if page_level_avail is False:
        return False

    # If we found an enabled purchase button and no OOS markers on the page, assume In Stock.
    if enabled_buy_button:
        return True
    # If the raw-text extraction found a strong in-stock signal (numeric stock counts,
    # explicit "in stock" text) and DOM inspection found no contradicting OOS markers,
    # honour the page-level signal.
    if page_level_avail is True:
        return True
    return None


def _enrich_availability_via_product_pages(
    client: HttpClient,
    products: list[Product],
    *,
    domain: str | None = None,
    max_pages: int,
    include_false: bool = False,
    include_true: bool = False,
    include_missing_cycles: bool = False,
) -> list[Product]:
    # Group by URL so we fetch each detail page once.
    # Priority order: availability resolution > missing cycles > location enrichment.
    candidate_meta: dict[str, dict] = {}
    for idx, p in enumerate(products):
        if not p.url.startswith(("http://", "https://")):
            continue
        needs_availability = p.available is None or (include_false and p.available is False) or (include_true and p.available is True)
        needs_cycles = include_missing_cycles and (not p.billing_cycles or not p.cycle_prices)
        needs_location = not p.location
        if not (needs_availability or needs_cycles or needs_location):
            continue
        priority = 2
        if needs_availability:
            priority = 0
        elif needs_cycles:
            priority = 1

        meta = candidate_meta.get(p.url)
        if not meta:
            candidate_meta[p.url] = {"indices": [idx], "priority": priority, "first_idx": idx}
            continue
        meta["indices"].append(idx)
        if priority < meta["priority"]:
            meta["priority"] = priority

    ordered_urls = sorted(
        candidate_meta.keys(),
        key=lambda u: (int(candidate_meta[u]["priority"]), int(candidate_meta[u]["first_idx"])),
    )
    selected_urls = ordered_urls[: max(0, max_pages)]
    candidates_by_url: dict[str, list[int]] = {u: list(candidate_meta[u]["indices"]) for u in selected_urls}

    if not candidates_by_url:
        return products

    shared_non_product_urls = {
        url for url, indices in candidates_by_url.items() if len(indices) > 1 and _looks_like_non_product_page(url)
    }

    enriched = list(products)
    max_workers = int(os.getenv("ENRICH_WORKERS", "6"))
    max_workers = max(1, min(max_workers, len(candidates_by_url)))

    def fetch_one(url: str) -> dict | None:
        allow_solver = _should_use_flaresolverr_for_discovery_page(url)
        fetch = _fetch_text(client, url, allow_flaresolverr=allow_solver)
        if (not fetch.ok or not fetch.text) and (not allow_solver) and _is_blocked_fetch(fetch):
            fetch = _fetch_text(client, url, allow_flaresolverr=True)
        if not fetch.ok or not fetch.text:
            return None
        html = fetch.text
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = None
        return {
            "availability": _infer_availability_from_detail_html(
                html,
                domain=(domain or urlparse(url).netloc.lower()),
                soup=soup,
            ),
            "billing_cycles": extract_billing_cycles_from_soup(soup, raw=html),
            "cycle_prices": extract_cycle_prices_from_soup(soup),
            "location_variants": extract_location_variants_from_soup(soup),
        }

    fetched: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(fetch_one, url): url for url in candidates_by_url.keys()}
        for fut in as_completed(futs):
            url = futs[fut]
            try:
                data = fut.result()
            except Exception:
                data = None
            if data:
                fetched[url] = data

    if not fetched:
        return products

    seen_ids = {p.id for p in enriched}
    generated: list[Product] = []

    for url, indices in candidates_by_url.items():
        data = fetched.get(url)
        if not data:
            continue
        shared_non_product = url in shared_non_product_urls
        avail = data.get("availability")
        cycles = None if shared_non_product else data.get("billing_cycles")
        cycle_prices = None if shared_non_product else data.get("cycle_prices")
        location_variants: list[tuple[str, bool | None]] = [] if shared_non_product else (data.get("location_variants") or [])

        for idx in indices:
            p = enriched[idx]

            next_available = p.available if avail is None else avail
            next_cycles = p.billing_cycles if not cycles else cycles
            next_cycle_prices = dict(p.cycle_prices or {})
            if cycle_prices:
                next_cycle_prices.update(cycle_prices)
            if not next_cycle_prices:
                next_cycle_prices = None

            next_specs: dict[str, str] | None = _clean_specs_dict(dict(p.specs or {}))
            if not next_specs:
                next_specs = None

            resolved_location = p.location
            if location_variants:
                if p.location:
                    for loc, loc_avail in location_variants:
                        if loc.lower() == p.location.lower():
                            if loc_avail is not None:
                                next_available = loc_avail
                            resolved_location = loc
                            break
                elif len(location_variants) == 1:
                    loc, loc_avail = location_variants[0]
                    resolved_location = loc
                    if loc_avail is not None:
                        next_available = loc_avail
                else:
                    # Keep the first location on the base product and emit extra variants.
                    base_loc, base_avail = location_variants[0]
                    resolved_location = base_loc
                    if base_avail is not None:
                        next_available = base_avail
                    variant_base_name = p.variant_of or p.name
                    for loc, loc_avail in location_variants[1:]:
                        variant_id = f"{p.id}::loc-{_slugify_fragment(loc)}"
                        if variant_id in seen_ids:
                            continue
                        seen_ids.add(variant_id)
                        generated.append(
                            _clone_product(
                                p,
                                id=variant_id,
                                name=p.name,
                                available=(next_available if loc_avail is None else loc_avail),
                                variant_of=variant_base_name,
                                location=loc,
                                locations=[loc],
                                location_links={loc: p.url},
                                billing_cycles=next_cycles,
                                cycle_prices=next_cycle_prices,
                                specs=next_specs,
                            )
                        )

            # Prefer monthly price for the canonical price field when absent.
            next_price = p.price
            if not next_price and next_cycle_prices:
                for preferred in ("Monthly", "Quarterly", "Yearly"):
                    if preferred in next_cycle_prices:
                        next_price = next_cycle_prices[preferred]
                        break
                if not next_price:
                    try:
                        next_price = next(iter(next_cycle_prices.values()))
                    except Exception:
                        next_price = p.price

            enriched[idx] = _product_with_special_flag(
                _clone_product(
                    p,
                    price=next_price,
                    available=next_available,
                    location=resolved_location,
                    locations=([resolved_location] if resolved_location else p.locations),
                    location_links=({resolved_location: p.url} if resolved_location else p.location_links),
                    billing_cycles=next_cycles,
                    cycle_prices=next_cycle_prices,
                    specs=next_specs,
                )
            )

    if generated:
        enriched.extend(generated)
    return enriched


def _needs_discovery(products: list[Product], *, base_url: str) -> bool:
    if not products:
        return True

    useful = sum(1 for p in products if p.price or p.specs)
    if useful == 0:
        return True

    suspicious = sum(1 for p in products if _looks_like_non_product_page(p.url))
    if len(products) <= 5 and suspicious >= max(1, len(products) - 1):
        return True

    # If we only extracted a single "product" that points back to the landing page, it's likely the site intro.
    if len(products) == 1:
        try:
            if urlparse(products[0].url).path.rstrip("/") in ("", "/") and urlparse(base_url).netloc == urlparse(products[0].url).netloc:
                return True
        except Exception:
            pass

    return False


def _looks_like_noise_product(product: Product) -> bool:
    name_l = compact_ws(product.name).lower()
    if not name_l:
        return True
    if _looks_like_non_product_page(product.url):
        return True
    url_l = (product.url or "").lower()
    if any(x in url_l for x in ("/ticket", "/tickets", "submitticket", "support")):
        return True
    if name_l in {"new", "item", "product"} and "cart" in url_l and product.available is None:
        return True
    noise_name_fragments = (
        "make payment",
        "transfer domains",
        "buy a domain",
        "order hosting",
        "browse all",
        "cart is empty",
        "introduction",
        "service introduction",
        "about us",
        "product category",
        "site introduction",
        "pricing and plans",
        "pricing table",
        "产品介绍",
        "產品介紹",
        "站点介绍",
        "網站介紹",
        "pricing only",
        "proceed to cart",
    )
    if any(x in name_l for x in noise_name_fragments):
        return True
    if not product.price and not product.specs and product.available is None:
        return True
    return False


def _is_primary_listing_page(url: str) -> bool:
    try:
        p = urlparse(url)
    except Exception:
        return False
    path = (p.path or "").lower()
    q = (p.query or "").lower()
    if "rp=/store" in q and ("rp=/store/" not in q):
        return True
    if path.endswith("/cart.php") and not any(x in q for x in ["pid=", "a=add"]):
        return True
    if path.endswith("/store") and path.count("/") <= 1:
        return True
    if "/billing/" in path and ("rp=/store" in q or path.endswith("/cart.php") or path.endswith("/store")):
        return True
    return False


def _should_use_flaresolverr_for_discovery_page(url: str) -> bool:
    ul = (url or "").lower()
    if _is_primary_listing_page(url):
        return True
    if "/api/" in ul or ul.startswith("https://api.") or "getvpsstore" in ul:
        return True
    if "/pages/pricing" in ul or ul.endswith("/pricing"):
        return True
    try:
        p = urlparse(url)
    except Exception:
        return False
    path = (p.path or "").lower()
    if "?/cart/" in ul:
        tail = ul.split("?/cart/", 1)[1]
        tail = tail.split("&", 1)[0].strip("/")
        return (not tail) or (tail.count("/") <= 1)
    if path.rstrip("/") in ("/cart", "/products", "/store"):
        return True
    if path.endswith("/cart.php"):
        q = (p.query or "").lower()
        if "a=add" in q or "pid=" in q:
            return False
        if "gid=" in q or "fid=" in q:
            return False
        return True
    return False


def _default_entrypoint_pages(base_url: str) -> list[str]:
    # Common product listing entry points across WHMCS installs and similar billing setups.
    pages = [
        urljoin(base_url, "/cart.php"),
        urljoin(base_url, "/index.php?rp=/store"),
        urljoin(base_url, "/store"),
        urljoin(base_url, "/cart"),
        urljoin(base_url, "/index.php?/cart/"),
        urljoin(base_url, "/products"),
        urljoin(base_url, "/billing/cart.php"),
        urljoin(base_url, "/billing/index.php?rp=/store"),
        urljoin(base_url, "/billing/store"),
    ]
    try:
        path_l = (urlparse(base_url).path or "").lower()
    except Exception:
        path_l = ""
    if "/clients" in path_l:
        pages.extend(
            [
                urljoin(base_url, "/clients/cart.php"),
                urljoin(base_url, "/clients/index.php?rp=/store"),
                urljoin(base_url, "/clients/store"),
            ]
        )
    return pages


def _domain_extra_pages(domain: str) -> list[str]:
    """Extra pages to crawl for specific domains, including API endpoints for SPAs
    and explicit WHMCS product group pages for sites that were missing products."""
    if domain == "acck.io":
        return ["https://api.acck.io/api/v1/store/GetVpsStore"]
    if domain == "akile.io":
        return ["https://api.akile.io/api/v1/store/GetVpsStoreV3"]

    # WHMCS sites: keep this list small. Large gid enumerations are expensive on Cloudflare sites
    # and can be handled via discovery + hidden scanners.
    if domain == "my.rfchost.com":
        return ["https://my.rfchost.com/cart.php", "https://my.rfchost.com/index.php?rp=/store"]
    if domain == "app.vmiss.com":
        return ["https://app.vmiss.com/cart.php", "https://app.vmiss.com/index.php?rp=/store"]
    if domain == "my.racknerd.com":
        return ["https://my.racknerd.com/cart.php", "https://my.racknerd.com/index.php?rp=/store"]
    if domain == "clients.zgovps.com":
        return ["https://clients.zgovps.com/index.php?/cart/"]
    if domain == "clientarea.gigsgigscloud.com":
        return ["https://clientarea.gigsgigscloud.com/cart/"]
    if domain == "www.dmit.io":
        return [
            "https://www.dmit.io/cart.php",
            "https://www.dmit.io/pages/pricing",
            "https://www.dmit.io/pages/tier1",
            "https://www.dmit.io/index.php?rp=/store",
        ]
    if domain == "cloud.colocrossing.com":
        return [
            "https://cloud.colocrossing.com/index.php?rp=/store/specials",
            "https://cloud.colocrossing.com/cart.php",
            "https://cloud.colocrossing.com/index.php?rp=/store",
        ]
    if domain == "bestvm.cloud":
        return ["https://bestvm.cloud/cart.php", "https://bestvm.cloud/index.php?rp=/store"]
    if domain == "www.mkcloud.net":
        return ["https://www.mkcloud.net/cart.php", "https://www.mkcloud.net/index.php?rp=/store"]
    if domain == "alphavps.com":
        return ["https://alphavps.com/clients/cart.php", "https://alphavps.com/clients/index.php?rp=/store"]

    return []


def _whmcs_gid_pages(base_url: str) -> list[str]:
    max_gid = int(os.getenv("WHMCS_MAX_GID_SCAN", "80"))
    p = urlparse(base_url)
    root = f"{p.scheme}://{p.netloc}"
    prefixes = _scan_prefixes(base_url)

    pages: list[str] = []
    for pref in prefixes:
        for gid in range(0, max_gid + 1):
            pages.append(f"{root}{pref}/cart.php?gid={gid}")
    return pages


def _scan_prefixes(base_url: str) -> list[str]:
    p = urlparse(base_url)
    path_l = (p.path or "").lower()
    out = [""]
    if "/billing" in path_l:
        out.append("/billing")
    if "/clients" in path_l:
        out.append("/clients")
    return list(dict.fromkeys(out))


def _hostbill_route_bases(base_url: str, seed_urls: list[str] | None = None) -> list[str]:
    try:
        base = urlparse(base_url)
    except Exception:
        return []
    root = f"{base.scheme}://{base.netloc}"
    seed_candidates = [base_url]
    for raw in (seed_urls or []):
        try:
            seed_candidates.append(urljoin(base_url, raw))
        except Exception:
            continue

    out: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        key = compact_ws(u).lower()
        if not key or key in seen:
            return
        seen.add(key)
        out.append(u)

    for pref in _scan_prefixes(base_url):
        add(f"{root}{pref}/index.php?/cart/")
        add(f"{root}{pref}/cart/")

    for u in seed_candidates:
        try:
            p = urlparse(u)
        except Exception:
            continue
        if p.netloc.lower() != base.netloc.lower():
            continue
        q = p.query or ""
        if q.startswith("/cart/"):
            route_prefix = q.split("&", 1)[0]
            add(urlunparse((p.scheme, p.netloc, p.path, p.params, route_prefix, p.fragment)))
        path_l = (p.path or "").lower()
        if "/cart/" in path_l:
            add(urlunparse((p.scheme, p.netloc, p.path, p.params, "", p.fragment)))
    return out


def _pid_cart_endpoints(
    base_url: str,
    *,
    platform: str = "whmcs",
    seed_urls: list[str] | None = None,
) -> list[str]:
    platform_l = (platform or "whmcs").strip().lower()
    if platform_l == "hostbill":
        return _hostbill_product_endpoints(base_url, seed_urls=seed_urls)

    p = urlparse(base_url)
    root = f"{p.scheme}://{p.netloc}"
    prefixes = _scan_prefixes(base_url)
    return [f"{root}{pref}/cart.php?a=add&pid={{pid}}" for pref in prefixes]


def _hostbill_product_endpoints(base_url: str, *, seed_urls: list[str] | None = None) -> list[str]:
    p = urlparse(base_url)
    root = f"{p.scheme}://{p.netloc}"
    out: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        if not u:
            return
        key = compact_ws(u).lower()
        if key in seen:
            return
        seen.add(key)
        out.append(u)

    for pref in _scan_prefixes(base_url):
        add(f"{root}{pref}/cart.php?action=add&id={{id}}")
        add(f"{root}{pref}/cart?action=add&id={{id}}")
        add(f"{root}{pref}/index.php?/cart/&action=add&id={{id}}")

    for route in _hostbill_route_bases(base_url, seed_urls=seed_urls):
        rp = urlparse(route)
        if (rp.query or "").startswith("/cart/"):
            add(f"{route}&action=add&id={{id}}")
        else:
            sep = "&" if rp.query else "?"
            add(f"{route}{sep}action=add&id={{id}}")

    for raw in seed_urls or []:
        abs_url = urljoin(base_url, raw)
        if "action=add" not in abs_url.lower():
            continue
        if _query_param_int(abs_url, "id") is None:
            continue
        templ = re.sub(r"([?&]id=)\d+", r"\1{id}", abs_url, flags=re.IGNORECASE)
        add(templ)

    return out


def _hostbill_group_endpoints(base_url: str, *, seed_urls: list[str] | None = None) -> list[str]:
    p = urlparse(base_url)
    root = f"{p.scheme}://{p.netloc}"
    out: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        if not u:
            return
        key = compact_ws(u).lower()
        if key in seen:
            return
        seen.add(key)
        out.append(u)

    for pref in _scan_prefixes(base_url):
        add(f"{root}{pref}/cart.php?fid={{fid}}")
        add(f"{root}{pref}/cart?fid={{fid}}")
        add(f"{root}{pref}/index.php?/cart/&fid={{fid}}")

    for route in _hostbill_route_bases(base_url, seed_urls=seed_urls):
        rp = urlparse(route)
        if (rp.query or "").startswith("/cart/"):
            add(f"{route}&fid={{fid}}")
        else:
            sep = "&" if rp.query else "?"
            add(f"{route}{sep}fid={{fid}}")

    return out


def _product_matches_probe_id(product: Product, probe_id: int, *, id_keys: tuple[str, ...]) -> bool:
    try:
        parsed = urlparse(product.url)
        qs = parse_qs(parsed.query or "")
        for key in id_keys:
            val = (qs.get(key) or [None])[0]
            if isinstance(val, str) and val.strip().isdigit() and int(val.strip()) == probe_id:
                return True
    except Exception:
        return False
    return False


def _product_matches_pid(product: Product, pid: int) -> bool:
    return _product_matches_probe_id(product, pid, id_keys=("pid", "id", "product_id", "planid"))


def _query_param_int(url: str, key: str) -> int | None:
    try:
        qs = parse_qs(urlparse(url).query or "")
    except Exception:
        return None
    raw = (qs.get(key) or [None])[0]
    if isinstance(raw, str) and raw.strip().isdigit():
        try:
            return int(raw.strip())
        except Exception:
            return None
    return None


_HIDDEN_SCAN_ID_RE = re.compile(r"(?:[?&]|&amp;)(pid|id|product_id|gid|fid)=(\d+)\b", re.IGNORECASE)


def _extract_id_candidates_from_text(text: str, *, keys: set[str]) -> set[int]:
    out: set[int] = set()
    if not text:
        return out
    for m in _HIDDEN_SCAN_ID_RE.finditer(text):
        key = (m.group(1) or "").strip().lower()
        if key not in keys:
            continue
        try:
            out.add(int(m.group(2)))
        except Exception:
            pass
    return out


def _extract_candidate_ids_from_html(
    html: str,
    *,
    base_url: str,
    keys: tuple[str, ...],
) -> set[int]:
    key_set = {k.lower() for k in keys}
    out: set[int] = set()
    if not html:
        return out

    def add_val(key: str, raw: str | None) -> None:
        if key.lower() not in key_set:
            return
        if not isinstance(raw, str):
            return
        val = raw.strip()
        if not val.isdigit():
            return
        out.add(int(val))

    for blob in (html, unescape(html)):
        out.update(_extract_id_candidates_from_text(blob or "", keys=key_set))

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = None
    if soup is None:
        return out

    for tag_name, attr_name in (("a", "href"), ("form", "action")):
        for tag in soup.find_all(tag_name):
            raw_attr = str(tag.get(attr_name) or "").strip()
            if not raw_attr:
                continue
            for value in (raw_attr, unescape(raw_attr), urljoin(base_url, unescape(raw_attr))):
                try:
                    qs = parse_qs(urlparse(value).query or "")
                except Exception:
                    continue
                for key, vals in qs.items():
                    if not vals:
                        continue
                    add_val(str(key), str(vals[0]))
                out.update(_extract_id_candidates_from_text(value, keys=key_set))

    for inp in soup.find_all("input"):
        name = compact_ws(str(inp.get("name") or "")).lower()
        value = inp.get("value")
        add_val(name, str(value) if value is not None else None)

    return out


def _stable_page_signature(url: str, html: str) -> str:
    try:
        p = urlparse(url)
        path = (p.path or "/").rstrip("/") or "/"
        url_key = f"{p.scheme}://{p.netloc}{path}".lower()
    except Exception:
        url_key = compact_ws(url).lower()
    body = compact_ws(html or "").lower()
    if not body:
        return url_key
    # Remove obviously volatile token-like chunks so default page fingerprints stay stable.
    body = re.sub(r"[a-f0-9]{24,}", "x", body)
    body = re.sub(r"\b\d{4,}\b", "n", body)
    digest = hashlib.sha1(body.encode("utf-8", errors="ignore")).hexdigest()[:20]
    return f"{url_key}::{digest}"


def _html_mentions_probe_id(html: str, probe_id: int, *, id_keys: tuple[str, ...]) -> bool:
    if probe_id < 0:
        return False
    raw = html or ""
    probe_str = str(probe_id)
    if probe_str not in raw:
        return False
    for key in id_keys:
        k = re.escape(key)
        if re.search(rf"(?:[?&]|&amp;){k}={probe_id}\b", raw, flags=re.IGNORECASE):
            return True
        if re.search(rf"""name=['"]{k}['"][^>]*value=['"]{probe_id}['"]""", raw, flags=re.IGNORECASE):
            return True
    return False


def _html_mentions_pid(html: str, pid: int) -> bool:
    return _html_mentions_probe_id(html, pid, id_keys=("pid",))


def _looks_like_pid_stock_page(html: str) -> bool:
    text = compact_ws(html or "")
    if not text:
        return False
    tl = text.lower()
    known_miss = (
        "product does not exist",
        "not found",
        "invalid product",
        "no product selected",
    )
    if any(m in tl for m in known_miss):
        return False
    if extract_availability(text) is not None:
        return True
    if any(k in tl for k in ("add to cart", "configure", "billing cycle", "configoption", "custom[")):
        return True
    return False


def _gid_cart_endpoints(
    base_url: str,
    *,
    platform: str = "whmcs",
    seed_urls: list[str] | None = None,
) -> list[str]:
    platform_l = (platform or "whmcs").strip().lower()
    if platform_l == "hostbill":
        return _hostbill_group_endpoints(base_url, seed_urls=seed_urls)

    p = urlparse(base_url)
    root = f"{p.scheme}://{p.netloc}"
    prefixes = _scan_prefixes(base_url)
    return [f"{root}{pref}/cart.php?gid={{gid}}" for pref in prefixes]


def _looks_like_whmcs_pid_page(html: str) -> bool:
    text = compact_ws(html or "")
    if not text:
        return False
    tl = text.lower()
    known_miss = (
        "product does not exist",
        "not found",
        "invalid product",
        "no product selected",
    )
    if any(m in tl for m in known_miss):
        return False
    # Strong WHMCS markers for product configuration pages.
    if "billingcycle" in tl or "configoption[" in tl or "custom[" in tl:
        return True
    # Stock markers (some templates show OOS without a config form).
    if extract_availability(text) is not None:
        return True
    return False


def _looks_like_whmcs_gid_page(html: str) -> bool:
    text = compact_ws(html or "")
    if not text:
        return False
    tl = text.lower()
    known_miss = (
        "not found",
        "invalid",
        "no product groups found",
        "no products found",
        "no products",
    )
    if any(m in tl for m in known_miss):
        return False
    # Group/listing pages usually contain product boxes with "pid=" links.
    if "cart.php" in tl and "pid=" in tl:
        return True
    if "rp=/store" in tl and ("add to cart" in tl or "configure" in tl or "order" in tl):
        return True
    return False


def _looks_like_hostbill_id_page(html: str) -> bool:
    text = compact_ws(html or "")
    if not text:
        return False
    tl = text.lower()
    known_miss = (
        "not found",
        "invalid",
        "product does not exist",
        "no product selected",
    )
    if any(m in tl for m in known_miss):
        return False
    if extract_availability(text) is not None:
        return True
    if any(k in tl for k in ("billing cycle", "configoption", "configure", "action=add&id=", "step=3")):
        return True
    return False


def _looks_like_hostbill_group_page(html: str) -> bool:
    text = compact_ws(html or "")
    if not text:
        return False
    tl = text.lower()
    known_miss = (
        "not found",
        "invalid",
        "no products",
    )
    if any(m in tl for m in known_miss):
        return False
    if re.search(r"(?:action=add(?:&amp;|&)id=\d+)", tl, flags=re.IGNORECASE):
        return True
    if re.search(r"""name=['"]id['"][^>]*value=['"]\d+['"]""", tl, flags=re.IGNORECASE):
        return True
    if any(k in tl for k in ("?/cart/", "/cart/")) and any(k in tl for k in ("add to cart", "configure", "order")):
        return True
    return False


def _scan_whmcs_hidden_products(
    client: HttpClient,
    parser,
    *,
    base_url: str,
    existing_ids: set[str],
    seed_urls: list[str] | None = None,
    seed_pids: list[int] | None = None,
    deadline: float | None = None,
    platform: str = "whmcs",
    skip_gid: bool = False,
) -> list[Product]:
    """
    Brute-force hidden cart endpoints for WHMCS/HostBill platforms.
    - gid scan stops after N consecutive ids return the same page signature.
    - pid scan stops after N consecutive ids have no product/stock evidence.
    - both scans also stop after N consecutive ids make no new discovery progress.
    Returns all discovered products regardless of stock state.
    """
    platform_l = (platform or "whmcs").strip().lower()
    if platform_l not in {"whmcs", "hostbill"}:
        platform_l = "whmcs"

    legacy_stop_after_miss = int(os.getenv("WHMCS_HIDDEN_STOP_AFTER_MISS", "30"))
    pid_stop_after_no_info = int(os.getenv("WHMCS_HIDDEN_PID_STOP_AFTER_NO_INFO", str(legacy_stop_after_miss)))
    # Sparse PID allocations (e.g. DMIT: 56, 58, 61, 71, 81...) need a higher threshold
    # to bridge the gaps between known products. When seed_pids are provided, we know
    # the PID space is sparse, so extend the brute-force tolerance.
    if seed_pids and pid_stop_after_no_info < 100:
        pid_stop_after_no_info = 100
    gid_stop_after_same_page = int(os.getenv("WHMCS_HIDDEN_GID_STOP_AFTER_SAME_PAGE", "20"))
    pid_stop_after_no_progress = int(os.getenv("WHMCS_HIDDEN_PID_STOP_AFTER_NO_PROGRESS", "90"))
    gid_stop_after_no_progress = int(os.getenv("WHMCS_HIDDEN_GID_STOP_AFTER_NO_PROGRESS", "90"))
    pid_stop_after_duplicates = int(os.getenv("WHMCS_HIDDEN_PID_STOP_AFTER_DUPLICATES", "60"))
    gid_stop_after_duplicates = int(os.getenv("WHMCS_HIDDEN_GID_STOP_AFTER_DUPLICATES", "60"))
    redirect_sig_stop_after = int(os.getenv("WHMCS_HIDDEN_REDIRECT_SIGNATURE_STOP_AFTER", "50"))
    min_probe_before_stop = int(os.getenv("WHMCS_HIDDEN_MIN_PROBE", "0"))
    batch_size = int(os.getenv("WHMCS_HIDDEN_BATCH", "12"))
    workers = int(os.getenv("WHMCS_HIDDEN_WORKERS", "8"))
    hard_max_pid = int(os.getenv("WHMCS_HIDDEN_HARD_MAX_PID", "2000"))
    hard_max_gid = int(os.getenv("WHMCS_HIDDEN_HARD_MAX_GID", "2000"))
    candidate_pid_limit = int(os.getenv("WHMCS_HIDDEN_PID_CANDIDATES_MAX", "200"))
    pid_stop_after_no_info = max(0, pid_stop_after_no_info)
    gid_stop_after_same_page = max(0, gid_stop_after_same_page)
    pid_stop_after_no_progress = max(0, pid_stop_after_no_progress)
    gid_stop_after_no_progress = max(0, gid_stop_after_no_progress)
    pid_stop_after_duplicates = max(0, pid_stop_after_duplicates)
    gid_stop_after_duplicates = max(0, gid_stop_after_duplicates)
    redirect_sig_stop_after = max(0, redirect_sig_stop_after)

    product_kind = "id" if platform_l == "hostbill" else "pid"
    group_kind = "fid" if platform_l == "hostbill" else "gid"
    product_id_match_keys = ("id", "pid", "product_id", "planid")
    product_html_id_keys = (product_kind, "id", "pid", "product_id")
    candidate_id_keys = (product_kind, "id", "pid", "product_id")

    pid_endpoints = _pid_cart_endpoints(base_url, platform=platform_l, seed_urls=seed_urls)
    gid_endpoints = _gid_cart_endpoints(base_url, platform=platform_l, seed_urls=seed_urls)
    if not pid_endpoints and not gid_endpoints:
        return []

    domain_for_ids = urlparse(base_url).netloc.lower()
    seen_ids: set[str] = set(existing_ids or set())
    found_products: dict[str, Product] = {}
    log_hits = os.getenv("WHMCS_HIDDEN_LOG", "0").strip() == "1"
    pid_candidates: set[int] = set()
    probed_pids: set[int] = set()
    seed_gids: set[int] = set()

    for u in seed_urls or []:
        abs_u = urljoin(base_url, u)
        gid = _query_param_int(abs_u, group_kind)
        if isinstance(gid, int) and gid >= 0:
            seed_gids.add(gid)

    def _deadline_exceeded() -> bool:
        return deadline is not None and time.perf_counter() >= deadline

    def _known_ids() -> set[str]:
        return seen_ids | set(found_products.keys())

    def _pid_id_candidates(pid: int) -> set[str]:
        out: set[str] = set()
        for tmpl in pid_endpoints:
            u = tmpl.format(**{product_kind: pid})
            try:
                out.add(f"{domain_for_ids}::{normalize_url_for_id(u)}")
            except Exception:
                out.add(f"{domain_for_ids}::{u}")
        return out

    def scan_ids(*, kind: str, ids: list[int] | None = None) -> None:
        nonlocal seen_ids, found_products

        if kind == product_kind:
            endpoints = pid_endpoints
            hard_max = hard_max_pid
        else:
            endpoints = gid_endpoints
            hard_max = hard_max_gid

        if not endpoints:
            return
        if _deadline_exceeded():
            return

        cur = 0
        pid_no_info_streak = 0
        pid_no_progress_streak = 0
        pid_dup_streak = 0
        gid_same_page_streak = 0
        gid_no_progress_streak = 0
        gid_dup_streak = 0
        last_gid_signature: str | None = None
        last_redirect_signature: str | None = None
        redirect_signature_streak = 0

        def probe_one(cur_id: int) -> tuple[int, bool, bool, list[Product], set[int], str | None, str | None]:
            """
            Returns: (id, has_evidence, is_duplicate, parsed_products, extra_pids, page_signature, redirect_signature)
            """
            fallback_signature: str | None = None
            fallback_redirect_signature: str | None = None

            def _redirect_signature(u: str) -> str:
                try:
                    p = urlparse(u)
                    path = (p.path or "/").rstrip("/") or "/"
                    return f"{p.scheme}://{p.netloc}{path}".lower()
                except Exception:
                    return compact_ws(u).lower()

            for tmpl in endpoints:
                if _deadline_exceeded():
                    return cur_id, False, False, [], set(), fallback_signature, fallback_redirect_signature
                url = tmpl.format(**{kind: cur_id})
                fetch = _fetch_text(client, url, allow_flaresolverr=False)
                if (not fetch.ok or not fetch.text) and _is_blocked_fetch(fetch):
                    fetch = _fetch_text(client, url, allow_flaresolverr=True)
                if not fetch.ok or not fetch.text:
                    continue
                html = fetch.text
                page_signature = _stable_page_signature(fetch.url, html) if kind == group_kind else None
                if kind == group_kind and fallback_signature is None and page_signature:
                    fallback_signature = page_signature

                id_mentioned = _html_mentions_probe_id(html, cur_id, id_keys=product_html_id_keys) if kind == product_kind else True
                query_key = product_kind if kind == product_kind else group_kind
                got = _query_param_int(fetch.url, query_key)
                if got is not None and got != cur_id:
                    if fallback_redirect_signature is None:
                        fallback_redirect_signature = _redirect_signature(fetch.url)
                    continue
                if got is None and fallback_redirect_signature is None:
                    fallback_redirect_signature = _redirect_signature(fetch.url)
                if kind == product_kind and platform_l == "whmcs" and got is None and not id_mentioned:
                    continue

                if kind == product_kind:
                    evidence = _looks_like_whmcs_pid_page(html) if platform_l == "whmcs" else _looks_like_hostbill_id_page(html)
                else:
                    evidence = _looks_like_whmcs_gid_page(html) if platform_l == "whmcs" else _looks_like_hostbill_group_page(html)
                if kind == product_kind and platform_l == "whmcs" and evidence and not id_mentioned:
                    # Some sites serve a generic default/cart page for any pid; don't treat that as evidence.
                    evidence = False
                extra_pids: set[int] = set()
                if kind == group_kind:
                    extra_pids = _extract_candidate_ids_from_html(
                        html,
                        base_url=fetch.url,
                        keys=candidate_id_keys,
                    )
                    if extra_pids:
                        evidence = True

                # Some valid carts redirect to a flow URL that drops pid/gid from the final URL.
                # Keep parser context anchored to the probed endpoint in that case.
                parse_base_url = fetch.url
                if kind == product_kind and got is None:
                    parse_base_url = url

                parsed = parser.parse(html, base_url=parse_base_url)
                parsed = [_product_with_special_flag(p) for p in parsed]

                if kind == product_kind and parsed:
                    matched = [p for p in parsed if _product_matches_probe_id(p, cur_id, id_keys=product_id_match_keys)]
                    if not matched and len(parsed) == 1:
                        single_p = parsed[0]
                        matched = [_clone_product(single_p, url=url, id=f"{domain_for_ids}::{normalize_url_for_id(url)}")]
                    parsed = matched

                normalized: list[Product] = []
                for p in parsed:
                    page_avail = _infer_availability_from_detail_html(html, domain=domain_for_ids)
                    # Don't default unknown availability to True just because the page
                    # has cart form elements; OOS pages also contain "configure" /
                    # "billing cycle" strings.  Preserve the parser's own availability
                    # when the detail-page inference is uncertain.
                    if page_avail is not None:
                        normalized.append(_clone_product(p, available=page_avail))
                    else:
                        normalized.append(p)

                if normalized:
                    known_ids = _known_ids()
                    is_dup = all(p.id in known_ids for p in normalized)
                    return cur_id, True, is_dup, normalized, extra_pids, page_signature, None

                # Some WHMCS themes require JS rendering or hide details; fall back to heuristics.
                if evidence:
                    is_dup = False
                    if kind == group_kind and extra_pids:
                        known_ids = _known_ids()
                        is_dup = not any((cid not in known_ids) for pid in extra_pids for cid in _pid_id_candidates(pid))
                    
                    if kind == product_kind and not normalized:
                        try:
                            from bs4 import BeautifulSoup
                            soup = BeautifulSoup(html, "lxml")
                            fallback_name = None
                            if hasattr(parser, "_extract_name"):
                                fallback_name = parser._extract_name(soup)
                            if not fallback_name:
                                for sel in ["h1", "h2", ".product-title", ".page-title"]:
                                    el = soup.select_one(sel)
                                    if el:
                                        t = compact_ws(el.get_text(" ", strip=True))
                                        if 2 <= len(t) <= 120 and "cart" not in t.lower() and "store" not in t.lower():
                                            fallback_name = t
                                            break
                            if not fallback_name:
                                title_cand = []
                                for string in soup.stripped_strings:
                                    s = compact_ws(str(string))
                                    if s and "cart" not in s.lower() and "store" not in s.lower() and 2 < len(s) < 60:
                                        title_cand.append(s)
                                        if len(title_cand) > 3:
                                            break
                                if title_cand:
                                    fallback_name = title_cand[0]

                            if fallback_name:
                                page_avail = _infer_availability_from_detail_html(html, domain=domain_for_ids, soup=soup)
                                if page_avail is None:
                                    page_avail = False
                                fallback_p = Product(
                                    id=f"{domain_for_ids}::{normalize_url_for_id(url)}",
                                    domain=domain_for_ids,
                                    url=url,
                                    name=fallback_name,
                                    price=None,
                                    description=None,
                                    specs=None,
                                    available=page_avail,
                                )
                                fallback_p = _product_with_special_flag(fallback_p)
                                normalized.append(fallback_p)
                                known_ids = _known_ids()
                                is_dup = fallback_p.id in known_ids
                                return cur_id, True, is_dup, normalized, extra_pids, page_signature, None
                        except Exception:
                            pass

                    return cur_id, True, is_dup, [], extra_pids, page_signature, None

            return cur_id, False, False, [], set(), fallback_signature, fallback_redirect_signature

        max_workers = max(1, min(max(1, workers), 16))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            if ids is not None:
                # Explicit id list: probe all (useful for sparse/non-consecutive ids discovered from gid pages).
                id_list = [i for i in ids if isinstance(i, int) and i >= 0]
                if not id_list:
                    return
                id_list = sorted(set(id_list))
                idx = 0
                while idx < len(id_list):
                    if _deadline_exceeded():
                        break
                    batch = id_list[idx : idx + batch_size]
                    idx += len(batch)
                    futs = {ex.submit(probe_one, cid): cid for cid in batch}
                    batch_results = [f.result() for f in as_completed(futs)]
                    batch_results.sort(key=lambda x: x[0])
                    for _id, has_evidence, is_dup, products, extra_pids, _page_sig, _redirect_sig in batch_results:
                        if kind == group_kind and extra_pids:
                            pid_candidates.update(extra_pids)
                        if not has_evidence or is_dup:
                            continue
                        for p in products:
                            known_before = (p.id in found_products) or (p.id in seen_ids)
                            found_products[p.id] = p
                            if kind == product_kind:
                                seen_ids.add(p.id)
                            if (not known_before) and log_hits:
                                print(f"[hidden:{kind}] discovered {p.domain} :: {p.name} :: {p.url}", flush=True)
                return

            while cur <= hard_max:
                if _deadline_exceeded():
                    break
                if kind == product_kind:
                    if (
                        pid_stop_after_no_info > 0
                        and pid_no_info_streak >= pid_stop_after_no_info
                        and cur > min_probe_before_stop
                    ):
                        break
                    if (
                        pid_stop_after_no_progress > 0
                        and pid_no_progress_streak >= pid_stop_after_no_progress
                        and cur > min_probe_before_stop
                    ):
                        break
                    if (
                        pid_stop_after_duplicates > 0
                        and pid_dup_streak >= pid_stop_after_duplicates
                        and cur > min_probe_before_stop
                    ):
                        if log_hits:
                            print(f"[hidden:{kind}] stop reason=duplicate_streak streak={pid_dup_streak}", flush=True)
                        break
                else:
                    if (
                        gid_stop_after_same_page > 0
                        and gid_same_page_streak >= gid_stop_after_same_page
                        and cur > min_probe_before_stop
                    ):
                        break
                    if (
                        gid_stop_after_no_progress > 0
                        and gid_no_progress_streak >= gid_stop_after_no_progress
                        and cur > min_probe_before_stop
                    ):
                        break
                    if (
                        gid_stop_after_duplicates > 0
                        and gid_dup_streak >= gid_stop_after_duplicates
                        and cur > min_probe_before_stop
                    ):
                        if log_hits:
                            print(f"[hidden:{kind}] stop reason=duplicate_streak streak={gid_dup_streak}", flush=True)
                        break
                if (
                    redirect_sig_stop_after > 0
                    and redirect_signature_streak >= redirect_sig_stop_after
                    and cur > min_probe_before_stop
                ):
                    if log_hits:
                        print(
                            f"[hidden:{kind}] stop reason=redirect_signature streak={redirect_signature_streak}",
                            flush=True,
                        )
                    break

                batch = list(range(cur, min(hard_max, cur + batch_size - 1) + 1))
                cur = batch[-1] + 1
                if kind == product_kind and probed_pids:
                    # Avoid re-fetching candidate pids we already probed via explicit candidate probing.
                    batch = [cid for cid in batch if cid not in probed_pids]
                    if not batch:
                        continue

                futs = {ex.submit(probe_one, cid): cid for cid in batch}
                batch_results = [f.result() for f in as_completed(futs)]
                batch_results.sort(key=lambda x: x[0])

                for _id, has_evidence, is_dup, products, extra_pids, page_sig, redirect_sig in batch_results:
                    if _deadline_exceeded():
                        break
                    if redirect_sig:
                        if redirect_sig == last_redirect_signature:
                            redirect_signature_streak += 1
                        else:
                            last_redirect_signature = redirect_sig
                            redirect_signature_streak = 1
                    elif has_evidence:
                        redirect_signature_streak = 0
                    else:
                        redirect_signature_streak = 0
                    new_pid_candidates = 0
                    if kind == group_kind and extra_pids:
                        before = len(pid_candidates)
                        pid_candidates.update(extra_pids)
                        new_pid_candidates = max(0, len(pid_candidates) - before)

                    if kind == product_kind:
                        if has_evidence:
                            pid_no_info_streak = 0
                        else:
                            pid_no_info_streak += 1
                    else:
                        if not page_sig:
                            # Fetch failure or no content — don't count as "same page"
                            # since it would prematurely stop the GID scan.
                            pass
                        elif page_sig == last_gid_signature:
                            gid_same_page_streak += 1
                        else:
                            last_gid_signature = page_sig
                            gid_same_page_streak = 1
                    added_products = 0
                    if has_evidence and not is_dup:
                        for p in products:
                            known_before = (p.id in found_products) or (p.id in seen_ids)
                            found_products[p.id] = p
                            if kind == product_kind:
                                seen_ids.add(p.id)
                            if not known_before:
                                added_products += 1
                                if log_hits:
                                    print(f"[hidden:{kind}] discovered {p.domain} :: {p.name} :: {p.url}", flush=True)
                    if kind == product_kind:
                        if has_evidence and is_dup:
                            pid_dup_streak += 1
                        elif has_evidence:
                            pid_dup_streak = 0
                        else:
                            pid_dup_streak = 0
                    else:
                        if has_evidence and is_dup:
                            gid_dup_streak += 1
                        elif has_evidence:
                            gid_dup_streak = 0
                        else:
                            gid_dup_streak = 0
                    made_progress = added_products > 0 or (kind == group_kind and new_pid_candidates > 0)

                    if kind == product_kind:
                        if made_progress:
                            pid_no_progress_streak = 0
                        else:
                            pid_no_progress_streak += 1
                    else:
                        if made_progress:
                            gid_no_progress_streak = 0
                        else:
                            gid_no_progress_streak += 1

    if not skip_gid:
        if seed_gids:
            scan_ids(kind=group_kind, ids=sorted(seed_gids))
        scan_ids(kind=group_kind)

        # If gid pages expose pid links, probe those pids first (handles sparse/non-consecutive pid allocations).
        if pid_candidates and pid_endpoints:
            probe_list = sorted(pid_candidates)
            if candidate_pid_limit > 0:
                probe_list = probe_list[:candidate_pid_limit]
            if probe_list:
                probed_pids.update(probe_list)
                scan_ids(kind=product_kind, ids=probe_list)

    # Pre-probe seed pids (e.g. from listing page PID maps) before brute-force.
    if seed_pids:
        seed_pid_list = sorted(set(seed_pids) - probed_pids)
        if seed_pid_list:
            probed_pids.update(seed_pid_list)
            scan_ids(kind=product_kind, ids=seed_pid_list)

    scan_ids(kind=product_kind)
    return list(found_products.values())


def _discover_candidate_pages(html: str, *, base_url: str, domain: str) -> list[str]:
    raw_page_limit = os.environ.get("DISCOVERY_MAX_PAGES_PER_DOMAIN")
    max_pages = int(raw_page_limit) if raw_page_limit and raw_page_limit.strip() else 40
    is_hostbill = _is_hostbill_domain(domain, html)
    # Only apply higher defaults when the user hasn't explicitly configured a limit.
    if raw_page_limit is None:
        if domain == "greencloudvps.com":
            # GreenCloud uses many non-WHMCS *.php listing pages; allow more crawl depth.
            max_pages = max(max_pages, 40)
        if _is_whmcs_domain(domain, html):
            max_pages = max(max_pages, 24)
        if is_hostbill:
            max_pages = max(max_pages, 32)
        if domain in {"my.racknerd.com"}:
            max_pages = max(max_pages, 80)
    soup = BeautifulSoup(html, "lxml")
    base_netloc = urlparse(base_url).netloc.lower()
    cart_depth = 2 if is_hostbill else 1

    candidates: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        if not u or u in seen or u == base_url:
            return
        seen.add(u)
        candidates.append(u)

    def absolutize(href: str) -> str:
        href = str(href or "").strip()
        if not href:
            return ""
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

    def consider_href(href: str) -> None:
        if not href:
            return
        href = str(href).strip()
        if not href or href.startswith(("#", "javascript:")):
            return
        abs_url = absolutize(href)
        p = urlparse(abs_url)
        if p.netloc.lower() != base_netloc:
            return
        u = abs_url.lower()
        path_l = (p.path or "").lower()
        if any(
            x in u
            for x in [
                "a=view",
                "/knowledgebase",
                "rp=/knowledgebase",
                "/login",
                "clientarea.php",
                "register",
                "/clientarea/",
                "/affiliates/",
                "/tickets/",
                "/chat/",
                "/userapi/",
                "/status/",
                "/signup/",
                "action=passreminder",
            ]
        ):
            return

        # WHMCS store/category pages (avoid individual product detail pages).
        if "rp=/store" in u:
            rp = (p.query or "").lower()
            try:
                rp_val = (parse_qs(p.query).get("rp") or [None])[0]
            except Exception:
                rp_val = None
            if isinstance(rp_val, str) and rp_val.startswith("/store/"):
                parts = [x for x in rp_val.strip("/").split("/") if x]
                if len(parts) <= 2:
                    add(abs_url)
            else:
                add(abs_url)
            return
        if "/store/" in path_l:
            after = path_l.split("/store/", 1)[1]
            parts = [x for x in after.split("/") if x]
            if len(parts) <= 1:
                add(abs_url)
            return

        # HostBill route style: /index.php?/cart/<category>/...
        if "?/cart/" in u:
            tail = u.split("?/cart/", 1)[1]
            tail = tail.split("&", 1)[0].strip("/")
            if tail and tail.count("/") <= cart_depth:
                add(abs_url)
            elif not tail:
                add(abs_url)
            return

        # Category pages under /cart/<category>.
        if "/cart/" in path_l:
            after = path_l.split("/cart/", 1)[1]
            parts = [x for x in after.split("/") if x]
            if len(parts) <= cart_depth:
                add(abs_url)
            return

        # Category pages under /products/<category>.
        if "/products/" in path_l:
            after = path_l.split("/products/", 1)[1]
            parts = [x for x in after.split("/") if x]
            if len(parts) <= 1:
                add(abs_url)
            return

        # WHMCS product group pages.
        if "cart.php" in u and ("gid=" in u or u.endswith("/cart.php")):
            add(abs_url)
            return

        # Pricing pages are frequently the full product list on marketing frontends.
        if "/pages/pricing" in path_l or path_l.endswith("/pricing"):
            add(abs_url)
            return

        # GreenCloud listing pages are often top-level *.php pages (not WHMCS store URLs).
        if domain == "greencloudvps.com":
            if path_l.endswith(".php") and "/billing/" not in path_l:
                if any(k in path_l for k in ["vps", "server", "cloud", "resources", "vds"]):
                    add(abs_url)
                    return

    for a in soup.find_all("a"):
        href = a.get("href")
        if href:
            consider_href(str(href))

    # Some templates expose category links via onclick handlers instead of href.
    for el in soup.find_all(attrs={"onclick": True}):
        onclick = str(el.get("onclick") or "")
        for m in re.finditer(r"""['"]([^'"]+)['"]""", onclick):
            candidate = m.group(1).strip()
            if candidate.startswith(("http://", "https://", "/")) or any(k in candidate.lower() for k in ("cart", "store", "products", "pricing", "gid=", "fid=")):
                consider_href(candidate)

    # If we didn't find any obvious listing pages but this looks like WHMCS, try common entry points.
    html_l = (html or "").lower()
    base_l = (base_url or "").lower()
    if not candidates and ("whmcs" in html_l or "rp=/login" in base_l or "/login" in base_l):
        add(urljoin(base_url, "/cart.php"))
        add(urljoin(base_url, "/index.php?rp=/store"))
        add(urljoin(base_url, "/store"))

    # Prefer store/cart pages first (these are typically product listings).
    def score(u: str) -> int:
        ul = u.lower()
        s = 0
        if "/products/cart/" in ul:
            s -= 1
        if "rp=/store" in ul or "/store/" in ul:
            s += 2
        if "?/cart/" in ul or "/cart/" in ul:
            s += 2
        if "/products/" in ul:
            s += 2
        if "cart.php?gid=" in ul:
            s += 2
        if ul.endswith("/cart.php"):
            s += 1
        if "/pages/pricing" in ul or ul.endswith("/pricing"):
            s += 1
        if domain == "greencloudvps.com" and ul.endswith(".php"):
            s += 1
        return s

    candidates.sort(key=score, reverse=True)
    return candidates[:max_pages]

