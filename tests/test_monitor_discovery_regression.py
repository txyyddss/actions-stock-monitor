from __future__ import annotations

import os
import unittest
from dataclasses import dataclass
from unittest import mock

from actions_stock_monitor.models import Product
from actions_stock_monitor.monitor import _scrape_target


@dataclass
class _Fetch:
    url: str
    ok: bool
    text: str | None
    error: str | None = None
    status_code: int | None = 200


class _FakeClient:
    def __init__(self, pages: dict[str, _Fetch]) -> None:
        self.pages = pages

    def fetch_text(self, url: str, *, allow_flaresolverr: bool = True) -> _Fetch:
        if url in self.pages:
            return self.pages[url]
        return _Fetch(url=url, ok=False, text=None, error="HTTP 404", status_code=404)


class _FakeParser:
    def parse(self, html: str, *, base_url: str):
        if "store/real-products" in base_url:
            return [
                Product(
                    id="my.rfchost.com::https://my.rfchost.com/cart.php?a=add&pid=88",
                    domain="my.rfchost.com",
                    url="https://my.rfchost.com/cart.php?a=add&pid=88",
                    name="Real Plan",
                    price="8.00 USD",
                    currency="USD",
                    description=None,
                    specs={"RAM": "2GB"},
                    available=True,
                )
            ]
        return []


class TestMonitorDiscoveryRegression(unittest.TestCase):
    def test_discovery_does_not_abort_early_when_queue_still_has_candidates(self) -> None:
        target = "https://my.rfchost.com/"
        bad1 = "https://my.rfchost.com/cart.php"
        bad2 = "https://my.rfchost.com/index.php?rp=/store"
        good = "https://my.rfchost.com/store/real-products"

        pages = {
            target: _Fetch(url=target, ok=True, text="<html>landing</html>"),
            good: _Fetch(url=good, ok=True, text="<html>real products</html>"),
            bad1: _Fetch(url=bad1, ok=False, text=None, error="HTTP 404", status_code=404),
            bad2: _Fetch(url=bad2, ok=False, text=None, error="HTTP 404", status_code=404),
        }
        client = _FakeClient(pages)

        def fake_discover(html: str, *, base_url: str, domain: str) -> list[str]:
            if base_url == target:
                return [bad1, bad2, good]
            return []

        env = {
            "DISCOVERY_MAX_PAGES_PER_DOMAIN": "10",
            "DISCOVERY_BATCH": "1",
            "DISCOVERY_WORKERS": "1",
            "DISCOVERY_STOP_AFTER_FETCH_ERRORS": "2",
            "MONITOR_LOG": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch("actions_stock_monitor.monitor.get_parser_for_domain", return_value=_FakeParser()):
                with mock.patch("actions_stock_monitor.monitor._discover_candidate_pages", side_effect=fake_discover):
                    with mock.patch("actions_stock_monitor.monitor._domain_extra_pages", return_value=[]):
                        with mock.patch("actions_stock_monitor.monitor._default_entrypoint_pages", return_value=[]):
                            with mock.patch("actions_stock_monitor.monitor._scan_whmcs_hidden_products", return_value=[]):
                                run = _scrape_target(client, target, allow_expansion=True)

        self.assertTrue(run.ok)
        self.assertTrue(any(p.name == "Real Plan" for p in run.products))
        self.assertIsInstance(run.meta, dict)
        self.assertEqual((run.meta or {}).get("discovery_stop_reason"), "queue_exhausted")
        self.assertEqual((run.meta or {}).get("discovery_fetch_errors"), 2)
        self.assertTrue((run.meta or {}).get("may_be_incomplete"))


if __name__ == "__main__":
    unittest.main()
