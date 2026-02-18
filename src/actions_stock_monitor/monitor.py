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
from .parsers.registry import get_parser_for_domain
from .targets import DEFAULT_TARGETS
from .telegram import h, load_telegram_config, send_telegram_html
from .timeutil import utc_now_iso


def _domain_from_url(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc


def _product_to_state_record(product: Product, now: str, *, first_seen: str | None = None) -> dict:
    return {
        "domain": product.domain,
        "url": product.url,
        "name": product.name,
        "price": product.price,
        "currency": product.currency,
        "description": product.description,
        "specs": product.specs,
        "available": product.available,
        "first_seen": first_seen or now,
        "last_seen": now,
        "last_change": now,
        "last_notified_new": None,
        "last_notified_restock": None,
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
                if telegram_cfg and product.available is True:
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
    msg = _format_message("RESTOCK ALERT", "馃煝", product, now)
    return send_telegram_html(cfg=cfg, message_html=msg, timeout_seconds=min(15.0, timeout_seconds))


def _notify_new_product(cfg, product: Product, now: str, *, timeout_seconds: float) -> bool:
    msg = _format_message("NEW PRODUCT", "馃啎", product, now)
    return send_telegram_html(cfg=cfg, message_html=msg, timeout_seconds=min(15.0, timeout_seconds))


def _format_message(kind: str, icon: str, product: Product, now: str) -> str:
    lines: list[str] = []
    lines.append(f"<b>{h(icon)} {h(kind)}</b>")
    lines.append(f"<b>{h(product.name)}</b>")
    if product.available is True:
        lines.append("Stock: <b>In Stock</b>")
    elif product.available is False:
        lines.append("Stock: <b>Out of Stock</b>")
    else:
        lines.append("Stock: <b>Unknown</b>")
    if product.price:
        lines.append(f"Price: <b>{h(product.price)}</b>")
    if product.specs:
        specs_short = ", ".join([f"{k}:{v}" for k, v in product.specs.items()][:6])
        if specs_short:
            lines.append(f"Specs: {h(specs_short)}")
    lines.append(f"Domain: {h(product.domain)}")
    lines.append(f"<a href=\"{h(product.url)}\">Buy now</a>")
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
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_scrape_target, client, target): target for target in effective_targets}
        for fut in as_completed(futures):
            runs.append(fut.result())

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
        if _needs_discovery(products):
            discovered = _discover_candidate_pages(fetch.text, base_url=fetch.url, domain=domain)
            for page_url in discovered:
                page_fetch = client.fetch_text(page_url)
                if not page_fetch.ok or not page_fetch.text:
                    continue
                products.extend(parser.parse(page_fetch.text, base_url=page_fetch.url))

            # De-dupe by id after discovery.
            deduped: dict[str, Product] = {}
            for p in products:
                deduped[p.id] = p
            products = list(deduped.values())

        duration_ms = int((time.perf_counter() - started) * 1000)
        return DomainRun(domain=domain, ok=True, error=None, duration_ms=duration_ms, products=products)
    except Exception as e:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return DomainRun(domain=domain, ok=False, error=f"{type(e).__name__}: {e}", duration_ms=duration_ms, products=[])


def _needs_discovery(products: list[Product]) -> bool:
    if not products:
        return True
    if len(products) == 1 and products[0].price is None and products[0].specs is None:
        return True
    return False


def _discover_candidate_pages(html: str, *, base_url: str, domain: str) -> list[str]:
    max_pages = int(os.getenv("DISCOVERY_MAX_PAGES_PER_DOMAIN", "25"))
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
        return s

    candidates.sort(key=score, reverse=True)
    return candidates[:max_pages]
