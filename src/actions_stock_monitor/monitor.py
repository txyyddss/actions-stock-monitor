from __future__ import annotations

import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .http_client import HttpClient
from .models import DomainRun, Product, RunSummary
from .parsers.common import extract_availability, extract_billing_cycles, normalize_url_for_id
from .parsers.registry import get_parser_for_domain
from .targets import DEFAULT_TARGETS
from .telegram import h, load_telegram_config, send_telegram_html
from .timeutil import utc_now_iso


def _domain_from_url(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc


def _fetch_text(client: HttpClient, url: str, *, allow_flaresolverr: bool = True):
    try:
        return client.fetch_text(url, allow_flaresolverr=allow_flaresolverr)
    except TypeError as exc:
        # Keep compatibility with simple test doubles that only expose fetch_text(url).
        if "allow_flaresolverr" not in str(exc):
            raise
        return client.fetch_text(url)


_NON_PRODUCT_URL_FRAGMENTS = (
    "cart",
    "clientarea.php",
    "register",
    "login",
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
        "option": product.option,
        "billing_cycles": product.billing_cycles,
        "available": product.available,
        "first_seen": first_seen or now,
        "last_seen": now,
        "last_change": now,
        "last_notified_new": None,
        "last_notified_restock": None,
        "last_notified_new_option": None,
    }


def _update_state_from_runs(previous_state: dict, runs: list[DomainRun], *, dry_run: bool, timeout_seconds: float) -> tuple[dict, RunSummary]:
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

        # Remove products that disappeared from a successfully crawled domain.
        seen_ids = {p.id for p in run.products}
        for pid, rec in list((state.get("products") or {}).items()):
            if not isinstance(rec, dict):
                continue
            if rec.get("domain") != domain:
                continue
            if pid not in seen_ids:
                state["products"].pop(pid, None)

        for product in run.products:
            prev = state["products"].get(product.id)
            if not prev:
                state["products"][product.id] = _product_to_state_record(product, now)
                new_products += 1
                had_variant = bool(product.variant_of and (product.domain, product.variant_of) in existing_variant_keys)
                is_new_option = bool(product.option and had_variant)
                if product.variant_of:
                    existing_variant_keys.add((product.domain, product.variant_of))
                if telegram_cfg:
                    if is_new_option and product.available is not False:
                        if _notify_new_option(telegram_cfg, product, now, timeout_seconds=timeout_seconds):
                            state["products"][product.id]["last_notified_new_option"] = now
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
                or prev.get("option") != product.option
                or prev.get("billing_cycles") != product.billing_cycles
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
                    "option": product.option,
                    "billing_cycles": product.billing_cycles,
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
    msg = _format_message("RESTOCK ALERT", "🔥", product, now)
    return send_telegram_html(cfg=cfg, message_html=msg, timeout_seconds=min(15.0, timeout_seconds))


def _notify_new_product(cfg, product: Product, now: str, *, timeout_seconds: float) -> bool:
    msg = _format_message("NEW PRODUCT", "✨", product, now)
    return send_telegram_html(cfg=cfg, message_html=msg, timeout_seconds=min(15.0, timeout_seconds))


def _notify_new_option(cfg, product: Product, now: str, *, timeout_seconds: float) -> bool:
    msg = _format_message("NEW OPTION", "🧩", product, now)
    return send_telegram_html(cfg=cfg, message_html=msg, timeout_seconds=min(15.0, timeout_seconds))


def _format_message(kind: str, icon: str, product: Product, now: str) -> str:
    lines: list[str] = []

    domain_tag = product.domain.replace(".", "").replace("-", "")
    lines.append(f"<b>[{h(icon)}] {h(kind)}</b>")
    lines.append(f"<b>#{h(domain_tag)}</b>")
    lines.append("")

    name = product.name
    if product.variant_of and product.option:
        name = f"{product.variant_of} - {product.option}"
    elif product.variant_of:
        name = f"{product.variant_of} - {product.name}"
    lines.append(f"<b>{h(name)}</b>")
    lines.append("")

    if product.price:
        lines.append(f"💰 <b>Price:</b> {h(product.price)}")
    if product.billing_cycles:
        lines.append(f"<b>Cycles:</b> {h(', '.join(product.billing_cycles))}")

    if product.available is True:
        lines.append("✅ <b>Status:</b> In Stock")
    elif product.available is False:
        lines.append("❌ <b>Status:</b> Out of Stock")
    else:
        lines.append("<b>Status:</b> Unknown")

    if product.option:
        lines.append(f"<b>Option:</b> {h(product.option)}")
    if product.variant_of:
        lines.append(f"<b>Plan:</b> {h(product.variant_of)}")
    lines.append("")

    if product.specs:
        prio = ["Location", "Node", "CPU", "RAM", "Disk", "Storage", "Transfer", "Traffic", "Bandwidth", "Port", "IPv4", "IPv6", "Cycles", "OS"]
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

    # Detailed description in a code block (no ellipsis truncation).
    if product.description and product.description.strip():
        lines.append("<b>Details</b>")
        lines.append(f"<pre>{h(product.description.strip())}</pre>")

    lines.append(f'👉 <a href="{h(product.url)}">Order Now</a>')
    lines.append(f"🕒 <code>{h(now)}</code>")

    message = "\n".join(lines)
    return message[:3900]


def run_monitor(
    *,
    previous_state: dict,
    targets: list[str],
    timeout_seconds: float,
    max_workers: int,
    dry_run: bool,
) -> tuple[dict, RunSummary]:
    effective_targets = targets or DEFAULT_TARGETS

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

    runs: list[DomainRun] = []
    log_enabled = os.getenv("MONITOR_LOG", "1").strip() != "0"
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_scrape_target, client, target): target for target in effective_targets}
        for fut in as_completed(futures):
            run = fut.result()
            runs.append(run)
            if log_enabled:
                if run.ok:
                    print(f"[{run.domain}] ok products={len(run.products)} {run.duration_ms}ms", flush=True)
                else:
                    print(f"[{run.domain}] error products=0 {run.duration_ms}ms :: {run.error}", flush=True)

    next_state, summary = _update_state_from_runs(previous_state, runs, dry_run=dry_run, timeout_seconds=timeout_seconds)
    return next_state, summary


def _scrape_target(client: HttpClient, target: str) -> DomainRun:
    domain = _domain_from_url(target)
    started = time.perf_counter()

    fetch = _fetch_text(client, target, allow_flaresolverr=True)
    if not fetch.ok or not fetch.text:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return DomainRun(domain=domain, ok=False, error=fetch.error or "fetch failed", duration_ms=duration_ms, products=[])

    parser = get_parser_for_domain(domain)
    try:
        products = parser.parse(fetch.text, base_url=fetch.url)
        deduped: dict[str, Product] = {p.id: p for p in products}

        if _needs_discovery(products, base_url=fetch.url) or _should_force_discovery(fetch.text, base_url=fetch.url, domain=domain, product_count=len(deduped)):
            raw_page_limit = os.environ.get("DISCOVERY_MAX_PAGES_PER_DOMAIN")
            max_pages_limit = int(raw_page_limit) if raw_page_limit and raw_page_limit.strip() else 16
            # Only apply higher defaults when the user hasn't explicitly configured a limit.
            if raw_page_limit is None:
                if domain == "greencloudvps.com":
                    max_pages_limit = max(max_pages_limit, 40)
                if _is_whmcs_domain(domain, fetch.text):
                    max_pages_limit = max(max_pages_limit, 24)
                if domain in {"vps.hosting", "clientarea.gigsgigscloud.com", "clients.zgovps.com"}:
                    # HostBill-style carts often require an extra discovery hop from category -> products.
                    max_pages_limit = max(max_pages_limit, 48)

            discovered = []
            # Try domain-specific extra pages (including SPA API endpoints) first so we don't
            # abort discovery after a streak of 404/blocked default entry points.
            discovered.extend(_domain_extra_pages(domain))
            discovered.extend(_discover_candidate_pages(fetch.text, base_url=fetch.url, domain=domain))
            discovered.extend(_default_entrypoint_pages(fetch.url))
            discovered = _dedupe_keep_order([u for u in discovered if u and u != fetch.url])
            if max_pages_limit > 0:
                discovered = discovered[:max_pages_limit]

            max_products = int(os.getenv("DISCOVERY_MAX_PRODUCTS_PER_DOMAIN", "500"))
            # Use a higher no-new threshold for WHMCS sites where each gid= page has
            # genuinely different products – stopping after 3 empty pages could miss
            # entire product groups.
            default_stop = "6" if _is_whmcs_domain(domain, fetch.text) else "4"
            stop_after_no_new = int(os.getenv("DISCOVERY_STOP_AFTER_NO_NEW_PAGES", default_stop))
            stop_after_fetch_errors = int(os.getenv("DISCOVERY_STOP_AFTER_FETCH_ERRORS", "4"))
            if domain in {"vps.hosting", "clientarea.gigsgigscloud.com", "clients.zgovps.com"}:
                # HostBill sites often discover real product pages only after several category hops.
                stop_after_no_new = 0
            no_new_streak = 0
            fetch_error_streak = 0
            pages_visited = 0

            for page_url in discovered:
                if max_pages_limit > 0 and pages_visited >= max_pages_limit:
                    break
                pages_visited += 1
                allow_solver = _should_use_flaresolverr_for_discovery_page(page_url)
                page_fetch = _fetch_text(client, page_url, allow_flaresolverr=allow_solver)
                if not page_fetch.ok or not page_fetch.text:
                    fetch_error_streak += 1
                    if stop_after_fetch_errors > 0 and fetch_error_streak >= stop_after_fetch_errors and _is_primary_listing_page(page_url):
                        break
                    continue
                fetch_error_streak = 0

                page_products = parser.parse(page_fetch.text, base_url=page_fetch.url)
                new_count = 0
                for p in page_products:
                    if p.id not in deduped:
                        new_count += 1
                    deduped[p.id] = p

                # Also discover more pages from this page's links.
                more_pages = _discover_candidate_pages(page_fetch.text, base_url=page_fetch.url, domain=domain)
                for mp in more_pages:
                    if max_pages_limit > 0 and len(discovered) >= max_pages_limit:
                        break
                    if mp and mp not in discovered and mp != fetch.url:
                        discovered.append(mp)

                if new_count == 0:
                    no_new_streak += 1
                else:
                    no_new_streak = 0

                if len(deduped) >= max_products:
                    break
                if stop_after_no_new > 0 and no_new_streak >= stop_after_no_new and _is_primary_listing_page(page_fetch.url):
                    break

            products = list(deduped.values())

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
        }
        _CYCLE_ENRICH_DOMAINS = {
            "fachost.cloud",
            "wawo.wiki",
            "vps.hosting",
            "clients.zgovps.com",
            "clientarea.gigsgigscloud.com",
        }
        if domain in _ENRICH_DOMAINS:
            false_only = all(p.available is False for p in products) if products else False
            include_missing_cycles = domain in _CYCLE_ENRICH_DOMAINS
            enrich_pages = 80 if include_missing_cycles and domain in {"wawo.wiki", "clientarea.gigsgigscloud.com"} else 40
            products = _enrich_availability_via_product_pages(
                client,
                products,
                max_pages=enrich_pages,
                include_false=(false_only or domain in {"fachost.cloud", "backwaves.net"}),
                include_missing_cycles=include_missing_cycles,
            )

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
        "clients.zgovps.com", "my.racknerd.com", "clientarea.gigsgigscloud.com",
        "cloud.boil.network",
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
    tl = (html or "").lower()
    if "add to cart" in tl or "checkout" in tl:
        return True
    if "加入购物车" in html or "加入購物車" in html:
        return True
    return None


def _enrich_availability_via_product_pages(
    client: HttpClient,
    products: list[Product],
    *,
    max_pages: int,
    include_false: bool = False,
    include_missing_cycles: bool = False,
) -> list[Product]:
    candidates: list[tuple[int, Product]] = []
    for idx, p in enumerate(products):
        if len(candidates) >= max_pages:
            break
        if not p.url.startswith(("http://", "https://")):
            continue
        needs_availability = p.available is None or (include_false and p.available is False)
        needs_cycles = include_missing_cycles and not p.billing_cycles
        if needs_availability or needs_cycles:
            candidates.append((idx, p))

    if not candidates:
        return products

    enriched = list(products)
    max_workers = int(os.getenv("ENRICH_WORKERS", "6"))
    max_workers = max(1, min(max_workers, len(candidates)))

    def fetch_one(p: Product) -> tuple[bool | None, list[str] | None]:
        allow_solver = _should_use_flaresolverr_for_discovery_page(p.url)
        fetch = _fetch_text(client, p.url, allow_flaresolverr=allow_solver)
        if not fetch.ok or not fetch.text:
            return None, None
        html = fetch.text
        avail = _infer_availability_from_detail_html(html)
        cycles = extract_billing_cycles(html)
        return avail, cycles

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(fetch_one, p): (idx, p) for idx, p in candidates}
        for fut in as_completed(futs):
            idx, p = futs[fut]
            try:
                avail, cycles = fut.result()
            except Exception:
                avail = None
                cycles = None
            if avail is None and not cycles:
                continue

            next_available = p.available if avail is None else avail
            next_cycles = p.billing_cycles if not cycles else cycles
            next_specs = p.specs
            if cycles:
                next_specs = dict(p.specs or {})
                next_specs.setdefault("Cycles", ", ".join(cycles))
            enriched[idx] = Product(
                id=p.id,
                domain=p.domain,
                url=p.url,
                name=p.name,
                price=p.price,
                currency=p.currency,
                description=p.description,
                specs=next_specs,
                available=next_available,
                raw=p.raw,
                variant_of=p.variant_of,
                option=p.option,
                billing_cycles=next_cycles,
            )

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
        return True
    if path.rstrip("/") in ("/cart", "/products", "/store"):
        return True
    if "/cart/" in path or "/products/" in path:
        return True
    return False


def _default_entrypoint_pages(base_url: str) -> list[str]:
    # Common product listing entry points across WHMCS installs and similar billing setups.
    return [
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


def _domain_extra_pages(domain: str) -> list[str]:
    """Extra pages to crawl for specific domains, including API endpoints for SPAs
    and explicit WHMCS product group pages for sites that were missing products."""
    if domain == "acck.io":
        return ["https://api.acck.io/api/v1/store/GetVpsStore"]
    if domain == "akile.io":
        return ["https://api.akile.io/api/v1/store/GetVpsStoreV3"]

    # WHMCS sites: enumerate product groups to ensure all are crawled.
    if domain == "my.rfchost.com":
        base = "https://my.rfchost.com"
        return [f"{base}/cart.php?gid={i}" for i in range(1, 20)]
    if domain == "wawo.wiki":
        base = "https://wawo.wiki"
        return [f"{base}/cart.php?gid={i}" for i in range(1, 20)]
    if domain == "fachost.cloud":
        base = "https://fachost.cloud"
        return [
            f"{base}/products/tw-hinet-vds",
            f"{base}/products/tw-nat",
            f"{base}/products/custom",
            f"{base}/products/hk-hkt-vds",
            f"{base}/products/tw-seednet-vds",
            f"{base}/products/tw-tbc-vds",
        ]
    if domain == "app.vmiss.com":
        return [
            "https://app.vmiss.com/cart.php",
            "https://app.vmiss.com/index.php?rp=/store",
        ] + [f"https://app.vmiss.com/cart.php?gid={i}" for i in range(1, 20)]
    if domain == "my.racknerd.com":
        return [f"https://my.racknerd.com/cart.php?gid={i}" for i in range(1, 30)]
    if domain == "bill.hostdare.com":
        return [f"https://bill.hostdare.com/cart.php?gid={i}" for i in range(1, 15)]
    if domain == "clients.zgovps.com":
        return ["https://clients.zgovps.com/index.php?/cart/"]
    if domain == "vps.hosting":
        return ["https://vps.hosting/cart/"]
    if domain == "clientarea.gigsgigscloud.com":
        return ["https://clientarea.gigsgigscloud.com/cart/"]
    if domain == "www.vps.soy":
        base = "https://www.vps.soy"
        return [f"{base}/cart?fid=1"] + [f"{base}/cart?fid=1&gid={i}" for i in range(1, 20)]
    if domain == "www.dmit.io":
        return [
            "https://www.dmit.io/cart.php",
            "https://www.dmit.io/pages/pricing",
            "https://www.dmit.io/pages/tier1",
        ] + [f"https://www.dmit.io/cart.php?gid={i}" for i in range(1, 15)]

    return []


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
        if any(x in u for x in ["a=view", "/knowledgebase", "rp=/knowledgebase", "/login", "clientarea.php", "register"]):
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
            if tail and tail.count("/") <= 1:
                add(abs_url)
            elif not tail:
                add(abs_url)
            return

        # Category pages under /cart/<category>.
        if "/cart/" in path_l:
            after = path_l.split("/cart/", 1)[1]
            parts = [x for x in after.split("/") if x]
            if len(parts) <= 1:
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
