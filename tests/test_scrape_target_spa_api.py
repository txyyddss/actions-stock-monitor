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

    def test_spa_api_plan_tag_controls_product_link_type(self) -> None:
        target = "https://akile.io/"
        api = "https://api.akile.io/api/v1/store/GetVpsStoreV3"

        api_payload = (
            '{"status_code":0,"status_msg":"ok","data":{"areas":[{"id":6,"area_name":"Area","nodes":[{"id":11,"node_name":"Node","plans":['
            '{"id":827,"plan_name":"Traffic Plan","tag":"traffic","cpu":1,"memory":1024,"disk":10,"stock":1,"price_datas":[{"price":1000,"cycle":1}]},'
            '{"id":903,"plan_name":"Bandwidth Plan","tag":"bandwidth","cpu":1,"memory":1024,"disk":10,"stock":1,"price_datas":[{"price":1000,"cycle":1}]},'
            '{"id":999,"plan_name":"Fallback Plan","tag":"unknown","cpu":1,"memory":1024,"disk":10,"stock":1,"price_datas":[{"price":1000,"cycle":1}]}'  # falls back to configured default
            "]}]}]}}"
        )

        def ok(url: str, text: str) -> FetchResult:
            return FetchResult(url=url, status_code=200, ok=True, text=text, error=None, elapsed_ms=1)

        def fail(url: str) -> FetchResult:
            return FetchResult(url=url, status_code=404, ok=False, text=None, error="HTTP 404", elapsed_ms=1)

        mapping = {
            target: ok(target, "<html><head></head><body>hello</body></html>"),
            api: ok(api, api_payload),
            "https://akile.io/cart.php": fail("https://akile.io/cart.php"),
            "https://akile.io/index.php?rp=/store": fail("https://akile.io/index.php?rp=/store"),
            "https://akile.io/store": fail("https://akile.io/store"),
            "https://akile.io/billing/cart.php": fail("https://akile.io/billing/cart.php"),
            "https://akile.io/billing/index.php?rp=/store": fail("https://akile.io/billing/index.php?rp=/store"),
            "https://akile.io/billing/store": fail("https://akile.io/billing/store"),
        }

        client = _FakeHttpClient(mapping)
        run = _scrape_target(client, target)
        self.assertTrue(run.ok)
        self.assertEqual(len(run.products), 3)

        product_by_plan = {}
        for product in run.products:
            if "planId=" not in product.url:
                continue
            plan_id = product.url.split("planId=", 1)[1].split("&", 1)[0]
            product_by_plan[plan_id] = product

        self.assertIn("type=traffic", product_by_plan["827"].url)
        self.assertIn("type=bandwidth", product_by_plan["903"].url)
        self.assertIn("type=bandwidth", product_by_plan["999"].url)


if __name__ == "__main__":
    unittest.main()
