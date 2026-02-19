from __future__ import annotations

import os
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed

from actions_stock_monitor.http_client import HttpClient
from actions_stock_monitor.monitor import _scrape_target
from actions_stock_monitor.targets import DEFAULT_TARGETS


class TestLiveSites(unittest.TestCase):
    @unittest.skipUnless(os.getenv("RUN_LIVE_TESTS", "").strip() == "1", "Set RUN_LIVE_TESTS=1 to enable live fetch tests.")
    def test_listed_sites_return_products(self) -> None:
        targets_env = os.getenv("LIVE_TARGETS", "").strip()
        targets = [t.strip() for t in targets_env.split(",") if t.strip()] if targets_env else list(DEFAULT_TARGETS)

        timeout_seconds = float(os.getenv("LIVE_TIMEOUT_SECONDS", "20"))
        allow_errors = os.getenv("LIVE_ALLOW_ERRORS", "").strip() == "1"

        proxy_url = os.getenv("PROXY_URL", "").strip() or None
        flaresolverr_url = os.getenv("FLARESOLVERR_URL", "").strip() or None
        max_workers = int(os.getenv("LIVE_WORKERS", "6"))

        failures: list[str] = []

        def scrape_one(url: str):
            client = HttpClient(timeout_seconds=timeout_seconds, proxy_url=proxy_url, flaresolverr_url=flaresolverr_url)
            return _scrape_target(client, url)

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(scrape_one, t): t for t in targets}
            for fut in as_completed(futs):
                target = futs[fut]
                try:
                    run = fut.result()
                except Exception as e:
                    failures.append(f"{target} ok=False products=0 error={type(e).__name__}: {e}")
                    continue
                if not run.ok or not run.products:
                    failures.append(f"{target} ok={run.ok} products={len(run.products)} error={run.error}")

        if failures and not allow_errors:
            self.fail("Live fetch failures:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
