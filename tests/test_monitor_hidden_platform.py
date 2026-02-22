from __future__ import annotations

import os
import unittest
from dataclasses import dataclass
from unittest import mock

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
        return self.pages.get(url, _Fetch(url=url, ok=False, text=None, error="HTTP 404", status_code=404))


class _FakeParser:
    def parse(self, html: str, *, base_url: str):
        return []


class TestMonitorHiddenPlatform(unittest.TestCase):
    def test_scrape_uses_hostbill_hidden_platform(self) -> None:
        target = "https://hb.example/"
        pages = {
            target: _Fetch(url=target, ok=True, text="<html><a href='/index.php?/cart/'>Cart</a></html>"),
        }
        client = _FakeClient(pages)

        env = {
            "MONITOR_LOG": "0",
            "PARALLEL_SIMPLE_HIDDEN": "0",
            "DISCOVERY_MAX_PAGES_PER_DOMAIN": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch("actions_stock_monitor.monitor.get_parser_for_domain", return_value=_FakeParser()):
                with mock.patch("actions_stock_monitor.monitor._needs_discovery", return_value=False):
                    with mock.patch("actions_stock_monitor.monitor._should_force_discovery_with_candidates", return_value=False):
                        with mock.patch("actions_stock_monitor.monitor._scan_whmcs_hidden_products", return_value=[]) as scan_mock:
                            run = _scrape_target(client, target, allow_expansion=True)

        self.assertTrue(run.ok)
        self.assertTrue(scan_mock.called)
        self.assertEqual(scan_mock.call_args.kwargs.get("platform"), "hostbill")

    def test_scrape_uses_whmcs_hidden_platform(self) -> None:
        target = "https://whmcs.example/"
        pages = {
            target: _Fetch(url=target, ok=True, text="<html>index.php?rp=/store</html>"),
        }
        client = _FakeClient(pages)

        env = {
            "MONITOR_LOG": "0",
            "PARALLEL_SIMPLE_HIDDEN": "0",
            "DISCOVERY_MAX_PAGES_PER_DOMAIN": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch("actions_stock_monitor.monitor.get_parser_for_domain", return_value=_FakeParser()):
                with mock.patch("actions_stock_monitor.monitor._needs_discovery", return_value=False):
                    with mock.patch("actions_stock_monitor.monitor._should_force_discovery_with_candidates", return_value=False):
                        with mock.patch("actions_stock_monitor.monitor._scan_whmcs_hidden_products", return_value=[]) as scan_mock:
                            run = _scrape_target(client, target, allow_expansion=True)

        self.assertTrue(run.ok)
        self.assertTrue(scan_mock.called)
        self.assertEqual(scan_mock.call_args.kwargs.get("platform"), "whmcs")

    def test_scrape_infers_whmcs_platform_from_candidate_urls(self) -> None:
        target = "https://unknown.example/"
        pages = {
            target: _Fetch(url=target, ok=True, text="<html><a href='/products'>Plans</a></html>"),
        }
        client = _FakeClient(pages)

        env = {
            "MONITOR_LOG": "0",
            "PARALLEL_SIMPLE_HIDDEN": "0",
            "DISCOVERY_MAX_PAGES_PER_DOMAIN": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch("actions_stock_monitor.monitor.get_parser_for_domain", return_value=_FakeParser()):
                with mock.patch("actions_stock_monitor.monitor._discover_candidate_pages", return_value=["/cart.php?gid=3"]):
                    with mock.patch("actions_stock_monitor.monitor._needs_discovery", return_value=False):
                        with mock.patch("actions_stock_monitor.monitor._should_force_discovery_with_candidates", return_value=False):
                            with mock.patch("actions_stock_monitor.monitor._scan_whmcs_hidden_products", return_value=[]) as scan_mock:
                                run = _scrape_target(client, target, allow_expansion=True)

        self.assertTrue(run.ok)
        self.assertTrue(scan_mock.called)
        self.assertEqual(scan_mock.call_args.kwargs.get("platform"), "whmcs")

    def test_scrape_infers_hostbill_platform_from_candidate_urls(self) -> None:
        target = "https://unknown-hb.example/"
        pages = {
            target: _Fetch(url=target, ok=True, text="<html><a href='/products'>Plans</a></html>"),
        }
        client = _FakeClient(pages)

        env = {
            "MONITOR_LOG": "0",
            "PARALLEL_SIMPLE_HIDDEN": "0",
            "DISCOVERY_MAX_PAGES_PER_DOMAIN": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch("actions_stock_monitor.monitor.get_parser_for_domain", return_value=_FakeParser()):
                with mock.patch("actions_stock_monitor.monitor._discover_candidate_pages", return_value=["/index.php?/cart/&cat_id=2"]):
                    with mock.patch("actions_stock_monitor.monitor._needs_discovery", return_value=False):
                        with mock.patch("actions_stock_monitor.monitor._should_force_discovery_with_candidates", return_value=False):
                            with mock.patch("actions_stock_monitor.monitor._scan_whmcs_hidden_products", return_value=[]) as scan_mock:
                                run = _scrape_target(client, target, allow_expansion=True)

        self.assertTrue(run.ok)
        self.assertTrue(scan_mock.called)
        self.assertEqual(scan_mock.call_args.kwargs.get("platform"), "hostbill")


if __name__ == "__main__":
    unittest.main()
