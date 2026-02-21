from __future__ import annotations

import unittest

from actions_stock_monitor.http_client import FetchResult
from actions_stock_monitor.monitor import _scrape_target


class _FakeHttpClient:
    def __init__(self, mapping: dict[str, FetchResult]) -> None:
        self._mapping = mapping
        self.calls: list[str] = []

    def fetch_text(self, url: str) -> FetchResult:
        self.calls.append(url)
        res = self._mapping.get(url)
        if res is not None:
            return res
        return FetchResult(url=url, status_code=404, ok=False, text=None, error="HTTP 404", elapsed_ms=1)


class TestScrapeTargetSpaApiDiscovery(unittest.TestCase):
    def test_spa_api_extra_pages_are_tried_before_default_entrypoints(self) -> None:
        target = "https://acck.io/"
        api = "https://api.acck.io/api/v1/store/GetVpsStore"

        api_payload = (
            '{"status_code":0,"status_msg":"ok","data":[{"id":1,"area_name":"Area","nodes":[{"id":1,"node_name":"Node","plans":['
            '{"id":1,"plan_name":"Plan A","cpu":1,"memory":1024,"disk":10,"stock":1,"price_datas":[{"price":1000,"cycle":1}] }'
            "]}]}]}"
        )

        def ok(url: str, text: str) -> FetchResult:
            return FetchResult(url=url, status_code=200, ok=True, text=text, error=None, elapsed_ms=1)

        def fail(url: str) -> FetchResult:
            return FetchResult(url=url, status_code=404, ok=False, text=None, error="HTTP 404", elapsed_ms=1)

        mapping = {
            target: ok(target, "<html><head></head><body>hello</body></html>"),
            api: ok(api, api_payload),
            # Default entrypoints all fail for this SPA site; we still must try the API endpoint.
            "https://acck.io/cart.php": fail("https://acck.io/cart.php"),
            "https://acck.io/index.php?rp=/store": fail("https://acck.io/index.php?rp=/store"),
            "https://acck.io/store": fail("https://acck.io/store"),
            "https://acck.io/billing/cart.php": fail("https://acck.io/billing/cart.php"),
            "https://acck.io/billing/index.php?rp=/store": fail("https://acck.io/billing/index.php?rp=/store"),
            "https://acck.io/billing/store": fail("https://acck.io/billing/store"),
        }

        client = _FakeHttpClient(mapping)
        run = _scrape_target(client, target)

        self.assertTrue(run.ok)
        self.assertGreater(len(run.products), 0)
        self.assertIn(api, client.calls)
        self.assertIn("type=bandwidth", run.products[0].url)
        self.assertIn("planId=1", run.products[0].url)
        # The API endpoint should be attempted before the default entrypoints.
        self.assertLess(client.calls.index(api), client.calls.index("https://acck.io/cart.php"))


if __name__ == "__main__":
    unittest.main()
