from __future__ import annotations

import os
from urllib.parse import urlparse

from actions_stock_monitor.http_client import HttpClient
from actions_stock_monitor.monitor import _scrape_target
from actions_stock_monitor.targets import DEFAULT_TARGETS


def main() -> int:
    targets_env = os.getenv("LIVE_TARGETS", "").strip()
    targets = [t.strip() for t in targets_env.split(",") if t.strip()] if targets_env else list(DEFAULT_TARGETS)

    timeout_seconds = float(os.getenv("LIVE_TIMEOUT_SECONDS", "20"))
    max_products = int(os.getenv("LIVE_MAX_PRODUCTS", "10"))

    proxy_url = os.getenv("PROXY_URL", "").strip() or None
    flaresolverr_url = os.getenv("FLARESOLVERR_URL", "").strip() or None
    client = HttpClient(timeout_seconds=timeout_seconds, proxy_url=proxy_url, flaresolverr_url=flaresolverr_url)

    total_products = 0
    for target in targets:
        run = _scrape_target(client, target)
        domain = urlparse(target).netloc
        print(f"\n== {domain} :: {target}")
        print(f"ok={run.ok} products={len(run.products)} error={run.error}")
        for p in run.products[:max_products]:
            specs_preview = ""
            if p.specs:
                items = list(p.specs.items())[:4]
                specs_preview = " | specs=" + ", ".join([f"{k}:{v}" for k, v in items])
            print(f"- {p.name} | {p.price} | available={p.available} | {p.url}{specs_preview}")
        total_products += len(run.products)

    print(f"\nTotal products: {total_products}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
