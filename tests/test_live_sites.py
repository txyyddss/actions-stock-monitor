from __future__ import annotations

import os
import unittest

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
        client = HttpClient(timeout_seconds=timeout_seconds, proxy_url=proxy_url, flaresolverr_url=flaresolverr_url)

        failures: list[str] = []
        for target in targets:
            run = _scrape_target(client, target)
            if not run.ok or not run.products:
                failures.append(f"{target} ok={run.ok} products={len(run.products)} error={run.error}")

        if failures and not allow_errors:
            self.fail("Live fetch failures:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
