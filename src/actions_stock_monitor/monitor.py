from __future__ import annotations

import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from .http_client import HttpClient
from .models import DomainRun, Product, RunSummary
from .parsers.common import (
    compact_ws,
    extract_availability,
    extract_billing_cycles,
    extract_cycle_prices,
    extract_location_variants,
    looks_like_purchase_action,
    looks_like_special_offer,
    normalize_url_for_id,
)
from .parsers.registry import get_parser_for_domain
from .targets import DEFAULT_TARGETS
from .telegram import h, load_telegram_config, send_telegram_html
from .timeutil import utc_now_iso


def _domain_from_url(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc


def _slugify_fragment(value: str) -> str:
    v = compact_ws(value).lower()
    v = re.sub(r"[^a-z0-9]+", "-", v).strip("-")
    return v or "x"


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
    available: bool | None = None,
    variant_of: str | None = None,
    location: str | None = None,
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
        available=available if available is not None else product.available,
        raw=product.raw,
        variant_of=variant_of if variant_of is not None else product.variant_of,
        location=location if location is not None else product.location,
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
    return status in {401, 403, 429, 503, 520, 521, 522, 523, 525, 526}


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


def _format_message(kind: str, icon: str, product: Product, now: str) -> str:
    lines: list[str] = []

    domain_tag = _telegram_domain_tag(product.domain)
    lines.append(f"<b>[{h(icon)}] {h(kind)}</b>")
    lines.append(f"<b>#{h(domain_tag)}</b>")
    lines.append("")

    name = product.name
    if product.variant_of and product.location:
        name = f"{product.variant_of} - {product.location}"
    elif product.variant_of:
        name = f"{product.variant_of} - {product.name}"
    if product.is_special:
        name = f"[SPECIAL] {name}"
    lines.append(f"<b>{h(name)}</b>")
    lines.append("")

    if product.price:
        lines.append(f"<b>Price:</b> {h(product.price)}")
    if product.billing_cycles:
        lines.append(f"<b>Cycles:</b> {h(', '.join(product.billing_cycles))}")
    if product.cycle_prices:
        lines.append("<b>Cycle Prices</b>")
        order = ["Monthly", "Quarterly", "Semiannual", "Yearly", "Biennial", "Triennial", "Quadrennial", "Quinquennial", "One-Time"]
        items = list(product.cycle_prices.items())
        items.sort(key=lambda kv: (order.index(kv[0]) if kv[0] in order else 999, kv[0]))
        cp_lines = [f"{k}: {v}" for k, v in items]
        lines.append(f"<pre>{h(chr(10).join(cp_lines))}</pre>")

    if product.available is True:
        lines.append("<b>Status:</b> In Stock")
    elif product.available is False:
        lines.append("<b>Status:</b> Out of Stock")
    else:
        lines.append("<b>Status:</b> Unknown")

    if product.location:
        lines.append(f"<b>Location:</b> {h(product.location)}")
    if product.variant_of:
        lines.append(f"<b>Plan:</b> {h(product.variant_of)}")
    if product.is_special:
        lines.append("<b>Tag:</b> Special/Promo")
    lines.append("")

    if product.specs:
        prio = ["Location", "Data Center", "Node", "CPU", "RAM", "Disk", "Storage", "Transfer", "Traffic", "Bandwidth", "Port", "IPv4", "IPv6", "Cycles", "OS"]
        items = list(product.specs.items())
        items.sort(key=lambda kv: (prio.index(kv[0]) if kv[0] in prio else 999, str(kv[0])))
        spec_lines: list[str] = []
        for k, v in items[:20]:
            if not k or not v:
                continue
            spec_lines.append(f"{k}: {v}")
        if spec_lines:
            lines.append("<b>Specs</b>")
            lines.append(f"<pre>{h(chr(10).join(spec_lines))}</pre>")

    if product.description and product.description.strip():
        lines.append("<b>Details</b>")
        lines.append(f"<pre>{h(product.description.strip())}</pre>")

    lines.append(f'<a href="{h(product.url)}">Open Product Page</a>')
    lines.append(f"<code>{h(now)}</code>")

    message = "\n".join(lines)
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
    prune_missing_products = True
    if mode == "lite":
        effective_targets = _select_lite_targets(previous_state=previous_state, fallback_targets=configured_targets)
        allow_expansion = False
        prune_missing_products = False

    raw_runs: list[DomainRun] = []
    log_enabled = os.getenv("MONITOR_LOG", "1").strip() != "0"
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(_scrape_target, client, target, allow_expansion=allow_expansion): target
            for target in effective_targets
        }
        for fut in as_completed(futures):
            run = fut.result()
            raw_runs.append(run)

    runs = _merge_runs_by_domain(raw_runs) if mode == "lite" else raw_runs
    if log_enabled:
        print(f"[monitor] mode={mode} targets={len(effective_targets)} domains={len(runs)}", flush=True)
        for run in runs:
            if run.ok:
                print(f"[{run.domain}] ok products={len(run.products)} {run.duration_ms}ms", flush=True)
            else:
                print(f"[{run.domain}] error products=0 {run.duration_ms}ms :: {run.error}", flush=True)

    next_state, summary = _update_state_from_runs(
        previous_state,
        runs,
        dry_run=dry_run,
        timeout_seconds=timeout_seconds,
        prune_missing_products=prune_missing_products,
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

    first_product_url_by_domain: dict[str, str] = {}
    for rec in (previous_state.get("products") or {}).values():
        if not isinstance(rec, dict):
            continue
        domain = rec.get("domain")
        url = rec.get("url")
        if not isinstance(domain, str) or not domain:
            continue
        if not isinstance(url, str) or not _is_http_url(url):
            continue
        if domain not in seen_domains:
            seen_domains.add(domain)
            state_domains.append(domain)
        first_product_url_by_domain.setdefault(domain, url)

    out: list[str] = []
    seen_targets: set[str] = set()
    for domain in state_domains:
        target = by_domain.get(domain) or first_product_url_by_domain.get(domain)
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
            out.append(
                DomainRun(
                    domain=domain,
                    ok=True,
                    error=None,
                    duration_ms=rec["duration_ms"],
                    products=list(rec["products"].values()),
                )
            )
            continue
        errors = rec["errors"][:3]
        error_msg = "; ".join(errors) if errors else "fetch failed"
        out.append(DomainRun(domain=domain, ok=False, error=error_msg, duration_ms=rec["duration_ms"], products=[]))
    return out


def _scrape_target(client: HttpClient, target: str, *, allow_expansion: bool = True) -> DomainRun:
    domain = _domain_from_url(target)
    started = time.perf_counter()

    fetch = _fetch_text(client, target, allow_flaresolverr=True)
    if (not fetch.ok or not fetch.text) and ("flaresolverr" in (fetch.error or "").lower() or "timed out" in (fetch.error or "").lower()):
        # If the solver is temporarily overloaded, retry once with direct fetch only.
        retry = _fetch_text(client, target, allow_flaresolverr=False)
        if retry.ok and retry.text:
            fetch = retry
    if not fetch.ok or not fetch.text:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return DomainRun(domain=domain, ok=False, error=fetch.error or "fetch failed", duration_ms=duration_ms, products=[])

    is_whmcs = _is_whmcs_domain(domain, fetch.text)
    parser = get_parser_for_domain(domain)
    try:
        products = [_product_with_special_flag(p) for p in parser.parse(fetch.text, base_url=fetch.url)]
        products = [p for p in products if not _looks_like_noise_product(p)]
        deduped: dict[str, Product] = {p.id: p for p in products}

        if allow_expansion and (
            _needs_discovery(products, base_url=fetch.url)
            or _should_force_discovery(fetch.text, base_url=fetch.url, domain=domain, product_count=len(deduped))
        ):
            raw_page_limit = os.environ.get("DISCOVERY_MAX_PAGES_PER_DOMAIN")
            max_pages_limit = int(raw_page_limit) if raw_page_limit and raw_page_limit.strip() else 16
            if max_pages_limit <= 0:
                # Treat 0/negative as "disable discovery" to avoid useless crawl loops.
                max_pages_limit = 0
            # Only apply higher defaults when the user hasn't explicitly configured a limit.
            if raw_page_limit is None:
                if domain == "greencloudvps.com":
                    max_pages_limit = max(max_pages_limit, 40)
                if is_whmcs:
                    max_pages_limit = max(max_pages_limit, 64)
                if domain in {"vps.hosting", "clientarea.gigsgigscloud.com", "clients.zgovps.com"}:
                    # HostBill-style carts often require an extra discovery hop from category -> products.
                    max_pages_limit = max(max_pages_limit, 48)

            if max_pages_limit > 0:
                discovered = []
                # Try domain-specific extra pages (including SPA API endpoints) first so we don't
                # abort discovery after a streak of 404/blocked default entry points.
                discovered.extend(_domain_extra_pages(domain))
                # Avoid brute-enumerating gid pages here; it is expensive on Cloudflare sites.
                # Hidden scanning and normal link discovery handle unlinked/sparse product groups.
                discovered.extend(_discover_candidate_pages(fetch.text, base_url=fetch.url, domain=domain))
                discovered.extend(_default_entrypoint_pages(fetch.url))
                discovered = _dedupe_keep_order([u for u in discovered if u and u != fetch.url])
                discovered_seen = set(discovered)

                max_products = int(os.getenv("DISCOVERY_MAX_PRODUCTS_PER_DOMAIN", "500"))
                # WHMCS category pages may be sparse/non-contiguous; avoid stopping too early.
                default_stop = "0" if is_whmcs else "4"
                stop_after_no_new = int(os.getenv("DISCOVERY_STOP_AFTER_NO_NEW_PAGES", default_stop))
                stop_after_fetch_errors = int(os.getenv("DISCOVERY_STOP_AFTER_FETCH_ERRORS", "4"))
                if domain in {"vps.hosting", "clientarea.gigsgigscloud.com", "clients.zgovps.com"}:
                    # HostBill sites often discover real product pages only after several category hops.
                    stop_after_no_new = 0
                no_new_streak = 0
                fetch_error_streak = 0
                pages_visited = 0
                discovery_workers = int(os.getenv("DISCOVERY_WORKERS", "4"))
                discovery_workers = max(1, min(discovery_workers, 12))
                discovery_batch = int(os.getenv("DISCOVERY_BATCH", "6"))
                discovery_batch = max(1, min(discovery_batch, 20))

                queue_idx = 0

                def fetch_one(page_url: str):
                    allow_solver = _should_use_flaresolverr_for_discovery_page(page_url)
                    page_fetch = _fetch_text(client, page_url, allow_flaresolverr=allow_solver)
                    if (not page_fetch.ok or not page_fetch.text) and (not allow_solver) and _is_blocked_fetch(page_fetch):
                        # Retry blocked pages with FlareSolverr only when needed.
                        page_fetch = _fetch_text(client, page_url, allow_flaresolverr=True)
                    return page_fetch

                with ThreadPoolExecutor(max_workers=discovery_workers) as ex:
                    while queue_idx < len(discovered):
                        if pages_visited >= max_pages_limit:
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
                            page_fetch = fetched.get(page_url)
                            if not page_fetch or not getattr(page_fetch, "ok", False) or not getattr(page_fetch, "text", None):
                                fetch_error_streak += 1
                                if stop_after_fetch_errors > 0 and fetch_error_streak >= stop_after_fetch_errors and _is_primary_listing_page(page_url):
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

                            # Also discover more pages from this page's links.
                            more_pages = _discover_candidate_pages(page_fetch.text, base_url=page_fetch.url, domain=domain)
                            for mp in more_pages:
                                if mp and mp not in discovered_seen and mp != fetch.url:
                                    discovered_seen.add(mp)
                                    discovered.append(mp)

                            if new_count == 0:
                                no_new_streak += 1
                            else:
                                no_new_streak = 0

                            if len(deduped) >= max_products:
                                queue_idx = len(discovered)
                                break
                            if stop_after_no_new > 0 and no_new_streak >= stop_after_no_new and _is_primary_listing_page(page_fetch.url):
                                queue_idx = len(discovered)
                                break

                products = [p for p in deduped.values() if not _looks_like_noise_product(p)]

        # Hidden WHMCS products: brute-scan pid/gid pages and keep only in-stock hits.
        if allow_expansion and is_whmcs:
            seed_urls = []
            seed_urls.extend(_domain_extra_pages(domain))
            seed_urls.extend(_discover_candidate_pages(fetch.text, base_url=fetch.url, domain=domain))
            seed_urls.extend(_default_entrypoint_pages(fetch.url))
            seed_urls = _dedupe_keep_order(seed_urls)
            hidden = _scan_whmcs_hidden_products(
                client,
                parser,
                base_url=fetch.url,
                existing_ids=set(deduped.keys()),
                seed_urls=seed_urls,
            )
            for hp in hidden:
                hp = _product_with_special_flag(hp)
                deduped[hp.id] = hp
            products = [p for p in deduped.values() if not _looks_like_noise_product(p)]

        # Some providers only reveal stock state on the product detail page (or render it client-side on listings).
        # Enrich all products with unknown or False availability via detail page fetches.
        _ENRICH_DOMAINS = {
            "fachost.cloud",
            "backwaves.net",
            "app.vmiss.com",
            "wawo.wiki",
            "vps.hosting",
            "clients.zgovps.com",
            "clientarea.gigsgigscloud.com",
            "www.vps.soy",
            "www.dmit.io",
            "bill.hostdare.com",
        }
        _CYCLE_ENRICH_DOMAINS = {
            "fachost.cloud",
            "wawo.wiki",
            "vps.hosting",
            "clients.zgovps.com",
            "clientarea.gigsgigscloud.com",
            "www.vps.soy",
            "www.dmit.io",
        }
        _TRUE_RECHECK_DOMAINS = {
            "vps.hosting",
            "clientarea.gigsgigscloud.com",
            "www.vps.soy",
            "www.dmit.io",
        }
        if allow_expansion and (domain in _ENRICH_DOMAINS or is_whmcs):
            false_only = all(p.available is False for p in products) if products else False
            include_missing_cycles = is_whmcs or (domain in _CYCLE_ENRICH_DOMAINS)
            enrich_pages = 80 if include_missing_cycles and domain in {"wawo.wiki", "clientarea.gigsgigscloud.com"} else 40
            if is_whmcs:
                enrich_pages = max(enrich_pages, 60)
            products = _enrich_availability_via_product_pages(
                client,
                products,
                max_pages=enrich_pages,
                include_false=(false_only or domain in {"fachost.cloud", "backwaves.net"}),
                include_true=(domain in _TRUE_RECHECK_DOMAINS),
                include_missing_cycles=include_missing_cycles,
            )

        products = list({p.id: _product_with_special_flag(p) for p in products}.values())

        duration_ms = int((time.perf_counter() - started) * 1000)
        return DomainRun(domain=domain, ok=True, error=None, duration_ms=duration_ms, products=products)
    except Exception as e:
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
        "my.rfchost.com", "fachost.cloud", "my.frantech.ca", "wawo.wiki",
        "nmcloud.cc", "bgp.gd", "wap.ac", "www.bagevm.com", "backwaves.net",
        "cloud.ggvision.net", "cloud.colocrossing.com", "bill.hostdare.com",
        "clients.zgovps.com", "my.racknerd.com",
        "cloud.boil.network", "bandwagonhost.com", "www.lycheen.com",
        "cloud.tizz.yt", "bestvm.cloud", "www.mkcloud.net", "alphavps.com",
    }
    return domain.lower() in whmcs_domains


def _should_force_discovery(html: str, *, base_url: str, domain: str, product_count: int) -> bool:
    """
    Some storefront landing pages only show a small teaser (e.g., one product per category).
    If we can see multiple likely listing pages, force a discovery pass.
    """
    if not html:
        return False
    candidates = _discover_candidate_pages(html, base_url=base_url, domain=domain)
    # If there are multiple category/group pages and we only saw a handful of products, it's likely incomplete.
    if len(candidates) < 2:
        return False

    threshold_small = int(os.getenv("DISCOVERY_FORCE_IF_PRODUCTS_LEQ", "6"))
    if product_count <= threshold_small:
        return True

    if _is_primary_listing_page(base_url):
        threshold_listing = int(os.getenv("DISCOVERY_FORCE_IF_PRIMARY_LISTING_PRODUCTS_LEQ", "40"))
        return product_count <= threshold_listing

    return False


def _infer_availability_from_detail_html(html: str) -> bool | None:
    avail = extract_availability(html)
    if avail is not None:
        return avail
    try:
        soup = BeautifulSoup(html or "", "lxml")
    except Exception:
        return None

    for el in soup.select(".outofstock, .out-of-stock, .soldout, [class*='outofstock'], [class*='soldout'], [class*='unavailable']"):
        txt = compact_ws(getattr(el, "get_text", lambda *a, **k: "")(" ", strip=True))
        if extract_availability(txt) is not True:
            return False

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
            return True
    return None


def _enrich_availability_via_product_pages(
    client: HttpClient,
    products: list[Product],
    *,
    max_pages: int,
    include_false: bool = False,
    include_true: bool = False,
    include_missing_cycles: bool = False,
) -> list[Product]:
    # Group by URL so we fetch each detail page once.
    candidates_by_url: dict[str, list[int]] = {}
    for idx, p in enumerate(products):
        if len(candidates_by_url) >= max_pages:
            break
        if not p.url.startswith(("http://", "https://")):
            continue
        needs_availability = p.available is None or (include_false and p.available is False) or (include_true and p.available is True)
        needs_cycles = include_missing_cycles and (not p.billing_cycles or not p.cycle_prices)
        needs_location = not p.location
        if not (needs_availability or needs_cycles or needs_location):
            continue
        candidates_by_url.setdefault(p.url, []).append(idx)

    if not candidates_by_url:
        return products

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
        return {
            "availability": _infer_availability_from_detail_html(html),
            "billing_cycles": extract_billing_cycles(html),
            "cycle_prices": extract_cycle_prices(html),
            "location_variants": extract_location_variants(html),
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
        avail = data.get("availability")
        cycles = data.get("billing_cycles")
        cycle_prices = data.get("cycle_prices")
        location_variants: list[tuple[str, bool | None]] = data.get("location_variants") or []

        for idx in indices:
            p = enriched[idx]

            next_available = p.available if avail is None else avail
            next_cycles = p.billing_cycles if not cycles else cycles
            next_cycle_prices = dict(p.cycle_prices or {})
            if cycle_prices:
                next_cycle_prices.update(cycle_prices)
            if not next_cycle_prices:
                next_cycle_prices = None

            next_specs: dict[str, str] | None = dict(p.specs or {})
            if next_cycles and "Cycles" not in next_specs:
                next_specs["Cycles"] = ", ".join(next_cycles)
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
        "套餐与价格",
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
    if domain == "wawo.wiki":
        return ["https://wawo.wiki/cart.php", "https://wawo.wiki/index.php?rp=/store"]
    if domain == "fachost.cloud":
        base = "https://fachost.cloud"
        return [
            f"{base}/products/tw-hinet-vds",
            f"{base}/products/tw-nat",
            f"{base}/products/custom",
            f"{base}/products/hk-hkt-vds",
            f"{base}/products/tw-seednet-vds",
            f"{base}/products/tw-tbc-vds",
            f"{base}/products/hk-vds",
            f"{base}/products/specials",
        ]
    if domain == "app.vmiss.com":
        return ["https://app.vmiss.com/cart.php", "https://app.vmiss.com/index.php?rp=/store"]
    if domain == "my.racknerd.com":
        return ["https://my.racknerd.com/cart.php", "https://my.racknerd.com/index.php?rp=/store"]
    if domain == "bill.hostdare.com":
        return ["https://bill.hostdare.com/cart.php", "https://bill.hostdare.com/index.php?rp=/store"]
    if domain == "clients.zgovps.com":
        return ["https://clients.zgovps.com/index.php?/cart/"]
    if domain == "vps.hosting":
        return ["https://vps.hosting/cart/"]
    if domain == "clientarea.gigsgigscloud.com":
        return ["https://clientarea.gigsgigscloud.com/cart/"]
    if domain == "www.vps.soy":
        base = "https://www.vps.soy"
        return [f"{base}/cart?fid=1"]
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
    if domain == "bandwagonhost.com":
        return ["https://bandwagonhost.com/cart.php", "https://bandwagonhost.com/index.php?rp=/store"]
    if domain == "www.lycheen.com":
        return ["https://www.lycheen.com/cart.php", "https://www.lycheen.com/index.php?rp=/store"]
    if domain == "cloud.tizz.yt":
        return ["https://cloud.tizz.yt/cart.php", "https://cloud.tizz.yt/index.php?rp=/store"]
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
    prefixes = [""]
    path_l = (p.path or "").lower()
    if "/billing" in path_l:
        prefixes.append("/billing")
    if "/clients" in path_l:
        prefixes.append("/clients")

    pages: list[str] = []
    for pref in prefixes:
        for gid in range(1, max_gid + 1):
            pages.append(f"{root}{pref}/cart.php?gid={gid}")
    return pages


def _pid_cart_endpoints(base_url: str) -> list[str]:
    p = urlparse(base_url)
    root = f"{p.scheme}://{p.netloc}"
    prefixes = [""]
    path_l = (p.path or "").lower()
    if "/billing" in path_l:
        prefixes.append("/billing")
    if "/clients" in path_l:
        prefixes.append("/clients")
    return [f"{root}{pref}/cart.php?a=add&pid={{pid}}" for pref in prefixes]


def _product_matches_pid(product: Product, pid: int) -> bool:
    try:
        parsed = urlparse(product.url)
        qs = parse_qs(parsed.query or "")
        for key in ("pid", "id", "product_id", "planid"):
            val = (qs.get(key) or [None])[0]
            if isinstance(val, str) and val.strip().isdigit() and int(val.strip()) == pid:
                return True
    except Exception:
        return False
    return False


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


_PID_NUM_RE = re.compile(r"[?&]pid=(\d+)\b", re.IGNORECASE)


def _extract_pid_numbers(html: str) -> set[int]:
    out: set[int] = set()
    for m in _PID_NUM_RE.finditer(html or ""):
        try:
            out.add(int(m.group(1)))
        except Exception:
            pass
    return out


_PID_HIDDEN_INPUT_RE = re.compile(r"""name=['"]pid['"][^>]*value=['"](\d+)['"]""", re.IGNORECASE)


def _html_mentions_pid(html: str, pid: int) -> bool:
    if pid <= 0:
        return False
    tl = (html or "").lower()
    if f"pid={pid}" in tl:
        return True
    m = _PID_HIDDEN_INPUT_RE.search(html or "")
    if m:
        try:
            return int(m.group(1)) == pid
        except Exception:
            return False
    return False


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


def _gid_cart_endpoints(base_url: str) -> list[str]:
    p = urlparse(base_url)
    root = f"{p.scheme}://{p.netloc}"
    prefixes = [""]
    path_l = (p.path or "").lower()
    if "/billing" in path_l:
        prefixes.append("/billing")
    if "/clients" in path_l:
        prefixes.append("/clients")
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


def _scan_whmcs_hidden_products(
    client: HttpClient,
    parser,
    *,
    base_url: str,
    existing_ids: set[str],
    seed_urls: list[str] | None = None,
) -> list[Product]:
    """
    Brute-force WHMCS cart.php?a=add&pid=N and cart.php?gid=N pages.
    Stop scanning only when 10 consecutive IDs have no product/stock evidence OR are duplicates.
    Only return products that are currently in stock.
    """
    stop_after_miss = int(os.getenv("WHMCS_HIDDEN_STOP_AFTER_MISS", "10"))
    min_probe_before_stop = int(os.getenv("WHMCS_HIDDEN_MIN_PROBE", "0"))
    batch_size = int(os.getenv("WHMCS_HIDDEN_BATCH", "8"))
    workers = int(os.getenv("WHMCS_HIDDEN_WORKERS", "6"))
    hard_max_pid = int(os.getenv("WHMCS_HIDDEN_HARD_MAX_PID", "2000"))
    hard_max_gid = int(os.getenv("WHMCS_HIDDEN_HARD_MAX_GID", "2000"))
    candidate_pid_limit = int(os.getenv("WHMCS_HIDDEN_PID_CANDIDATES_MAX", "200"))

    pid_endpoints = _pid_cart_endpoints(base_url)
    gid_endpoints = _gid_cart_endpoints(base_url)
    if not pid_endpoints and not gid_endpoints:
        return []

    domain_for_ids = urlparse(base_url).netloc.lower()
    seen_ids: set[str] = set(existing_ids or set())
    found_in_stock: dict[str, Product] = {}
    log_hits = os.getenv("WHMCS_HIDDEN_LOG", "0").strip() == "1"
    pid_candidates: set[int] = set()
    probed_pids: set[int] = set()
    seed_gids: set[int] = set()

    for u in seed_urls or []:
        gid = _query_param_int(u, "gid")
        if isinstance(gid, int) and gid > 0:
            seed_gids.add(gid)

    def _pid_id_candidates(pid: int) -> set[str]:
        out: set[str] = set()
        for tmpl in pid_endpoints:
            u = tmpl.format(pid=pid)
            try:
                out.add(f"{domain_for_ids}::{normalize_url_for_id(u)}")
            except Exception:
                out.add(f"{domain_for_ids}::{u}")
        return out

    def scan_ids(*, kind: str, ids: list[int] | None = None) -> None:
        nonlocal seen_ids, found_in_stock

        if kind == "pid":
            endpoints = pid_endpoints
            hard_max = hard_max_pid
        else:
            endpoints = gid_endpoints
            hard_max = hard_max_gid

        if not endpoints:
            return

        miss_streak = 0
        cur = 1
        found_any = False

        def probe_one(cur_id: int) -> tuple[int, bool, bool, list[Product], set[int]]:
            """
            Returns: (id, has_evidence, is_duplicate, parsed_products, extra_pids)
            """
            for tmpl in endpoints:
                url = tmpl.format(**{kind: cur_id})
                fetch = _fetch_text(client, url, allow_flaresolverr=True)
                if not fetch.ok or not fetch.text:
                    continue
                html = fetch.text
                pid_mentioned = _html_mentions_pid(html, cur_id) if kind == "pid" else True

                # Many WHMCS installs redirect invalid ids back to cart.php or the homepage.
                # Treat those as misses (no product/stock evidence for this id).
                if kind == "pid":
                    got = _query_param_int(fetch.url, "pid")
                    if got != cur_id:
                        continue
                else:
                    got = _query_param_int(fetch.url, "gid")
                    if got != cur_id:
                        continue

                evidence = _looks_like_whmcs_pid_page(html) if kind == "pid" else _looks_like_whmcs_gid_page(html)
                if kind == "pid" and evidence and not pid_mentioned:
                    # Some sites serve a generic default/cart page for any pid; don't treat that as evidence.
                    evidence = False
                extra_pids: set[int] = set()
                if kind == "gid":
                    extra_pids = _extract_pid_numbers(html)
                    if extra_pids:
                        evidence = True

                parsed = parser.parse(html, base_url=fetch.url)
                parsed = [_product_with_special_flag(p) for p in parsed]

                if kind == "pid" and parsed:
                    parsed = [p for p in parsed if _product_matches_pid(p, cur_id)]

                normalized: list[Product] = []
                for p in parsed:
                    page_avail = _infer_availability_from_detail_html(html)
                    normalized.append(_clone_product(p, available=(p.available if page_avail is None else page_avail)))

                if normalized:
                    is_dup = all(p.id in seen_ids for p in normalized)
                    return cur_id, True, is_dup, normalized, extra_pids

                # Some WHMCS themes require JS rendering or hide details; fall back to heuristics.
                if evidence:
                    is_dup = False
                    if kind == "gid" and extra_pids:
                        is_dup = not any((cid not in seen_ids) for pid in extra_pids for cid in _pid_id_candidates(pid))
                    return cur_id, True, is_dup, [], extra_pids

            return cur_id, False, False, [], set()

        max_workers = max(1, min(max(1, workers), 16))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            if ids is not None:
                # Explicit id list: probe all (useful for sparse/non-consecutive ids discovered from gid pages).
                id_list = [i for i in ids if isinstance(i, int) and i > 0]
                if not id_list:
                    return
                id_list = sorted(set(id_list))
                idx = 0
                while idx < len(id_list):
                    batch = id_list[idx : idx + batch_size]
                    idx += len(batch)
                    futs = {ex.submit(probe_one, cid): cid for cid in batch}
                    batch_results = [f.result() for f in as_completed(futs)]
                    batch_results.sort(key=lambda x: x[0])
                    for _id, has_evidence, is_dup, products, extra_pids in batch_results:
                        if kind == "gid" and extra_pids:
                            pid_candidates.update(extra_pids)
                        if not has_evidence or is_dup:
                            continue
                        found_any = True
                        for p in products:
                            if p.id in seen_ids:
                                continue
                            seen_ids.add(p.id)
                            if p.available is True:
                                found_in_stock[p.id] = p
                                if log_hits:
                                    print(f"[hidden:{kind}] in-stock {p.domain} :: {p.name} :: {p.url}", flush=True)
                return

            while cur <= hard_max:
                if miss_streak >= stop_after_miss and (found_any or cur > min_probe_before_stop):
                    break

                batch = list(range(cur, min(hard_max, cur + batch_size - 1) + 1))
                cur = batch[-1] + 1
                if kind == "pid" and probed_pids:
                    # Avoid re-fetching candidate pids we already probed (count as duplicates for stop logic).
                    kept: list[int] = []
                    for cid in batch:
                        if cid in probed_pids:
                            miss_streak += 1
                        else:
                            kept.append(cid)
                    batch = kept
                    if not batch:
                        continue

                futs = {ex.submit(probe_one, cid): cid for cid in batch}
                batch_results = [f.result() for f in as_completed(futs)]
                batch_results.sort(key=lambda x: x[0])

                for _id, has_evidence, is_dup, products, extra_pids in batch_results:
                    if kind == "gid" and extra_pids:
                        pid_candidates.update(extra_pids)

                    if not has_evidence:
                        miss_streak += 1
                        continue
                    if is_dup:
                        miss_streak += 1
                        continue

                    found_any = True
                    miss_streak = 0
                    for p in products:
                        if p.id in seen_ids:
                            continue
                        seen_ids.add(p.id)
                        if p.available is True:
                            found_in_stock[p.id] = p
                            if log_hits:
                                print(f"[hidden:{kind}] in-stock {p.domain} :: {p.name} :: {p.url}", flush=True)

    if seed_gids:
        scan_ids(kind="gid", ids=sorted(seed_gids))
    scan_ids(kind="gid")

    # If gid pages expose pid links, probe those pids first (handles sparse/non-consecutive pid allocations).
    if pid_candidates and pid_endpoints:
        probe_list = sorted(pid_candidates)
        if candidate_pid_limit > 0:
            probe_list = probe_list[:candidate_pid_limit]
        if probe_list:
            probed_pids.update(probe_list)
            scan_ids(kind="pid", ids=probe_list)

    scan_ids(kind="pid")
    return list(found_in_stock.values())


def _discover_candidate_pages(html: str, *, base_url: str, domain: str) -> list[str]:
    raw_page_limit = os.environ.get("DISCOVERY_MAX_PAGES_PER_DOMAIN")
    max_pages = int(raw_page_limit) if raw_page_limit and raw_page_limit.strip() else 16
    # Only apply higher defaults when the user hasn't explicitly configured a limit.
    if raw_page_limit is None:
        if domain == "greencloudvps.com":
            # GreenCloud uses many non-WHMCS *.php listing pages; allow more crawl depth.
            max_pages = max(max_pages, 40)
        if _is_whmcs_domain(domain, html):
            max_pages = max(max_pages, 24)
        if domain in {"vps.hosting", "clientarea.gigsgigscloud.com", "clients.zgovps.com"}:
            max_pages = max(max_pages, 32)
    soup = BeautifulSoup(html, "lxml")
    base_netloc = urlparse(base_url).netloc.lower()
    hostbill_like_domains = {"vps.hosting", "clientarea.gigsgigscloud.com", "clients.zgovps.com"}
    cart_depth = 2 if domain in hostbill_like_domains else 1

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
                from urllib.parse import parse_qs as _parse_qs

                rp_val = (_parse_qs(p.query).get("rp") or [None])[0]
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
