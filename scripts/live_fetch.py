from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

from actions_stock_monitor.http_client import HttpClient
from actions_stock_monitor.monitor import _scrape_target
from actions_stock_monitor.targets import DEFAULT_TARGETS


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    targets_env = os.getenv("LIVE_TARGETS", "").strip()
    targets = [t.strip() for t in targets_env.split(",") if t.strip()] if targets_env else list(DEFAULT_TARGETS)

    timeout_seconds = float(os.getenv("LIVE_TIMEOUT_SECONDS", "25"))
    max_products = int(os.getenv("LIVE_MAX_PRODUCTS", "20"))

    proxy_url = os.getenv("PROXY_URL", "").strip() or None
    flaresolverr_url = os.getenv("FLARESOLVERR_URL", "").strip() or None
    client = HttpClient(timeout_seconds=timeout_seconds, proxy_url=proxy_url, flaresolverr_url=flaresolverr_url)

    total_products = 0
    total_in_stock = 0
    total_oos = 0
    total_unknown = 0
    errors: list[str] = []

    for target in targets:
        run = _scrape_target(client, target)
        domain = urlparse(target).netloc
        print(f"\n== {domain} :: {target}", flush=True)
        print(f"ok={run.ok} products={len(run.products)} error={run.error}", flush=True)

        if not run.ok:
            errors.append(f"  ✗ {domain}: {run.error}")

        in_stock = sum(1 for p in run.products if p.available is True)
        oos = sum(1 for p in run.products if p.available is False)
        unk = len(run.products) - in_stock - oos
        print(f"  Stock: {in_stock} in stock, {oos} OOS, {unk} unknown", flush=True)

        for p in run.products[:max_products]:
            specs_preview = ""
            if p.specs:
                items = list(p.specs.items())[:4]
                specs_preview = " | specs=" + ", ".join([f"{k}:{v}" for k, v in items])
            cycles_str = ""
            if p.billing_cycles:
                cycles_str = f" | cycles={', '.join(p.billing_cycles)}"
            location_str = ""
            if p.location:
                location_str = f" | location={p.location}"
            print(f"- {p.name} | {p.price} | available={p.available}{location_str}{cycles_str} | {p.url}{specs_preview}", flush=True)

        if len(run.products) > max_products:
            print(f"  ... and {len(run.products) - max_products} more products", flush=True)

        total_products += len(run.products)
        total_in_stock += in_stock
        total_oos += oos
        total_unknown += unk

    print(f"\n{'='*60}", flush=True)
    print(f"Total products: {total_products}", flush=True)
    print(f"  In Stock: {total_in_stock}", flush=True)
    print(f"  Out of Stock: {total_oos}", flush=True)
    print(f"  Unknown: {total_unknown}", flush=True)
    if errors:
        print(f"\nErrors ({len(errors)}):", flush=True)
        for e in errors:
            print(e, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
