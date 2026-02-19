from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .http_client import HttpClient
from .models import DomainRun, Product, RunSummary
from .parsers.common import extract_availability, extract_billing_cycles
from .parsers.registry import get_parser_for_domain
from .targets import DEFAULT_TARGETS
from .telegram import h, load_telegram_config, send_telegram_html
from .timeutil import utc_now_iso


def _domain_from_url(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc


_NON_PRODUCT_URL_FRAGMENTS = (
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
    msg = _format_message("NEW PRODUCT", "🆕", product, now)
    return send_telegram_html(cfg=cfg, message_html=msg, timeout_seconds=min(15.0, timeout_seconds))


def _notify_new_option(cfg, product: Product, now: str, *, timeout_seconds: float) -> bool:
    msg = _format_message("NEW OPTION", "✨", product, now)
    return send_telegram_html(cfg=cfg, message_html=msg, timeout_seconds=min(15.0, timeout_seconds))


def _format_message(kind: str, icon: str, product: Product, now: str) -> str:
    lines: list[str] = []
    lines.append(f"<b>{h(icon)} {h(kind)}</b>")
    lines.append(f"<b>{h(product.name)}</b>")
    lines.append(f"Domain: <b>{h(product.domain)}</b>")
    if product.variant_of and product.option:
        lines.append(f"Plan: <b>{h(product.variant_of)}</b>")
        lines.append(f"Option: <b>{h(product.option)}</b>")
    if product.available is True:
        lines.append("Stock: <b>In Stock</b>")
    elif product.available is False:
        lines.append("Stock: <b>Out of Stock</b>")
    else:
        lines.append("Stock: <b>Unknown</b>")
    if product.price:
        lines.append(f"Price: <b>{h(product.price)}</b>")
    if product.billing_cycles:
        lines.append(f"Cycles: <b>{h(', '.join(product.billing_cycles))}</b>")
    if product.specs:
        lines.append("Specs:")
        prio = ["Location", "CPU", "RAM", "Disk", "Storage", "Traffic", "Bandwidth", "Port", "Cycles", "OS"]
        items = list(product.specs.items())
        items.sort(key=lambda kv: (prio.index(kv[0]) if kv[0] in prio else 999, str(kv[0])))
        for k, v in items[:12]:
            if not k or not v:
                continue
            lines.append(f"• <b>{h(str(k))}</b>: {h(str(v))}")
    if product.description:
        desc = (product.description or "").strip()
        if len(desc) > 500:
            desc = desc[:500] + "…"
        if desc:
            lines.append(f"Details: {h(desc)}")
    lines.append(f"Link: <a href=\"{h(product.url)}\">Open</a>")
    lines.append(f"Detected: {h(now)}")
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
                    print(f"[{run.domain}] ok products={len(run.products)} {run.duration_ms}ms")
                else:
                    print(f"[{run.domain}] error products=0 {run.duration_ms}ms :: {run.error}")

    next_state, summary = _update_state_from_runs(previous_state, runs, dry_run=dry_run, timeout_seconds=timeout_seconds)
    return next_state, summary


def _scrape_target(client: HttpClient, target: str) -> DomainRun:
    domain = _domain_from_url(target)
    started = time.perf_counter()

    fetch = client.fetch_text(target)
    if not fetch.ok or not fetch.text:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return DomainRun(domain=domain, ok=False, error=fetch.error or "fetch failed", duration_ms=duration_ms, products=[])

    parser = get_parser_for_domain(domain)
    try:
        products = parser.parse(fetch.text, base_url=fetch.url)
        deduped: dict[str, Product] = {p.id: p for p in products}

        if _needs_discovery(products, base_url=fetch.url) or _should_force_discovery(fetch.text, base_url=fetch.url, domain=domain, product_count=len(deduped)):
            discovered = []
            discovered.extend(_discover_candidate_pages(fetch.text, base_url=fetch.url, domain=domain))
            discovered.extend(_default_entrypoint_pages(fetch.url))
            discovered.extend(_domain_extra_pages(domain))
            discovered = _dedupe_keep_order([u for u in discovered if u and u != fetch.url])

            max_products = int(os.getenv("DISCOVERY_MAX_PRODUCTS_PER_DOMAIN", "250"))
            stop_after_no_new = int(os.getenv("DISCOVERY_STOP_AFTER_NO_NEW_PAGES", "3"))
            stop_after_fetch_errors = int(os.getenv("DISCOVERY_STOP_AFTER_FETCH_ERRORS", "4"))
            no_new_streak = 0
            fetch_error_streak = 0

            for page_url in discovered:
                page_fetch = client.fetch_text(page_url)
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
        if domain == "fachost.cloud":
            products = _enrich_availability_via_product_pages(client, products, max_pages=30)
        if domain == "backwaves.net":
            # backwaves.net listings frequently render as "unavailable" without JS. If we saw no in-stock
            # products at all, do a fast detail-page pass to avoid false negatives.
            if products and not any(p.available is True for p in products):
                products = _enrich_availability_via_product_pages(client, products, max_pages=30, include_false=True)

        duration_ms = int((time.perf_counter() - started) * 1000)
        return DomainRun(domain=domain, ok=True, error=None, duration_ms=duration_ms, products=products)
    except Exception as e:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return DomainRun(domain=domain, ok=False, error=f"{type(e).__name__}: {e}", duration_ms=duration_ms, products=[])


def _dedupe_keep_order(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


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
) -> list[Product]:
    candidates: list[tuple[int, Product]] = []
    for idx, p in enumerate(products):
        if len(candidates) >= max_pages:
            break
        if not p.url.startswith(("http://", "https://")):
            continue
        if p.available is None or (include_false and p.available is False):
            candidates.append((idx, p))

    if not candidates:
        return products

    enriched = list(products)
    max_workers = int(os.getenv("ENRICH_WORKERS", "6"))
    max_workers = max(1, min(max_workers, len(candidates)))

    def fetch_one(p: Product) -> tuple[bool | None, list[str] | None]:
        fetch = client.fetch_text(p.url)
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


def _default_entrypoint_pages(base_url: str) -> list[str]:
    # Common product listing entry points across WHMCS installs and similar billing setups.
    return [
        urljoin(base_url, "/cart.php"),
        urljoin(base_url, "/index.php?rp=/store"),
        urljoin(base_url, "/store"),
        urljoin(base_url, "/billing/cart.php"),
        urljoin(base_url, "/billing/index.php?rp=/store"),
        urljoin(base_url, "/billing/store"),
    ]


def _domain_extra_pages(domain: str) -> list[str]:
    # Some targets are SPAs that render products client-side. For those, hit the backing API directly.
    if domain == "acck.io":
        return ["https://api.acck.io/api/v1/store/GetVpsStore"]
    if domain == "akile.io":
        return ["https://api.akile.io/api/v1/store/GetVpsStoreV3"]
    return []


def _discover_candidate_pages(html: str, *, base_url: str, domain: str) -> list[str]:
    max_pages = int(os.getenv("DISCOVERY_MAX_PAGES_PER_DOMAIN", "10"))
    if domain == "greencloudvps.com":
        # GreenCloud uses many non-WHMCS *.php listing pages; allow more crawl depth.
        max_pages = max(max_pages, 60)
    soup = BeautifulSoup(html, "lxml")
    base_netloc = urlparse(base_url).netloc.lower()

    candidates: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        if not u or u in seen or u == base_url:
            return
        seen.add(u)
        candidates.append(u)

    for a in soup.find_all("a"):
        href = a.get("href")
        if not href:
            continue
        href = str(href).strip()
        if href.startswith(("#", "javascript:")):
            continue
        abs_url = urljoin(base_url, href)
        p = urlparse(abs_url)
        if p.netloc.lower() != base_netloc:
            continue
        u = abs_url.lower()
        if any(x in u for x in ["a=view", "/knowledgebase", "rp=/knowledgebase", "/login", "clientarea.php", "register"]):
            continue

        # WHMCS store/category pages (avoid individual product detail pages).
        if "rp=/store" in u:
            rp = (p.query or "").lower()
            # Quick check: rp=/store/<cat> (category) vs rp=/store/<cat>/<product> (product detail).
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
            continue
        if "/store/" in p.path.lower():
            after = p.path.lower().split("/store/", 1)[1]
            parts = [x for x in after.split("/") if x]
            if len(parts) <= 1:
                add(abs_url)
            continue

        # WHMCS product group pages.
        if "cart.php" in u and ("gid=" in u or u.endswith("/cart.php")):
            add(abs_url)
            continue

        # GreenCloud listing pages are often top-level *.php pages (not WHMCS store URLs).
        if domain == "greencloudvps.com":
            path_l = (p.path or "").lower()
            if path_l.endswith(".php") and "/billing/" not in path_l:
                if any(k in path_l for k in ["vps", "server", "cloud", "resources", "vds"]):
                    add(abs_url)
                    continue

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
        if "rp=/store" in ul or "/store/" in ul:
            s += 2
        if "cart.php?gid=" in ul:
            s += 2
        if ul.endswith("/cart.php"):
            s += 1
        if domain == "greencloudvps.com" and ul.endswith(".php"):
            s += 1
        return s

    candidates.sort(key=score, reverse=True)
    return candidates[:max_pages]
