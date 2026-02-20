from __future__ import annotations

import os
import unittest
from urllib.parse import urlparse

from actions_stock_monitor.http_client import HttpClient
from actions_stock_monitor.monitor import _scrape_target
from actions_stock_monitor.targets import DEFAULT_TARGETS


class TestLiveSites(unittest.TestCase):
    def _run_one(self, target: str) -> None:
        if os.getenv("RUN_LIVE_TESTS", "").strip() != "1":
            self.skipTest("Set RUN_LIVE_TESTS=1 to enable live fetch tests.")

        timeout_seconds = float(os.getenv("LIVE_TIMEOUT_SECONDS", "20"))
        allow_errors = os.getenv("LIVE_ALLOW_ERRORS", "").strip() == "1"
        proxy_url = os.getenv("PROXY_URL", "").strip() or None
        flaresolverr_url = os.getenv("FLARESOLVERR_URL", "").strip() or None

        client = HttpClient(timeout_seconds=timeout_seconds, proxy_url=proxy_url, flaresolverr_url=flaresolverr_url)
        run = _scrape_target(client, target)

        if (not run.ok) or (not run.products):
            if allow_errors:
                return
            self.fail(f"{target} ok={run.ok} products={len(run.products)} error={run.error}")


if __name__ == "__main__":
    unittest.main()


def _sanitize(name: str) -> str:
    return "".join(ch if ("a" <= ch <= "z" or "0" <= ch <= "9") else "_" for ch in name.lower()).strip("_")


def _targets_for_live_tests() -> list[str]:
    targets_env = os.getenv("LIVE_TARGETS", "").strip()
    targets = [t.strip() for t in targets_env.split(",") if t.strip()] if targets_env else list(DEFAULT_TARGETS)
    # Keep stable ordering for nicer test output.
    return sorted(set(targets))


for _t in _targets_for_live_tests():
    _domain = urlparse(_t).netloc or _t
    _name = f"test_live_{_sanitize(_domain)}"
    if hasattr(TestLiveSites, _name):
        continue

    def _make_test(target: str):
        def _test(self: TestLiveSites) -> None:
            self._run_one(target)

        return _test

    setattr(TestLiveSites, _name, _make_test(_t))
