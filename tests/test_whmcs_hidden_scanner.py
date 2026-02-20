from __future__ import annotations

import os
import unittest
from unittest import mock
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse
import time

from actions_stock_monitor.models import Product
from actions_stock_monitor.monitor import _scan_whmcs_hidden_products
from actions_stock_monitor.parsers.common import normalize_url_for_id


@dataclass
class _Fetch:
    url: str
    ok: bool
    text: str | None
    error: str | None = None
    status_code: int | None = 200


class _FakeClient:
    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = dict(pages)
        self.calls: list[str] = []

    def fetch_text(self, url: str, *, allow_flaresolverr: bool = True) -> _Fetch:
        self.calls.append(url)
        html = self._pages.get(url)
        if html is None:
            # Simulate "invalid pid/gid returns default page" behavior (often via redirect).
            p = urlparse(url)
            root = f"{p.scheme}://{p.netloc}"
            return _Fetch(url=f"{root}/cart.php", ok=True, text="<html>default page</html>")
        return _Fetch(url=url, ok=True, text=html)


class _FakeParser:
    def __init__(self, domain: str) -> None:
        self._domain = domain

    def parse(self, html: str, *, base_url: str) -> list[Product]:
        qs = parse_qs(urlparse(base_url).query)
        pid = (qs.get("pid") or [None])[0]
        gid = (qs.get("gid") or [None])[0]
        if isinstance(pid, str) and pid.isdigit():
            url = base_url
            pid_norm = f"{self._domain}::{normalize_url_for_id(url)}"
            return [
                Product(
                    id=pid_norm,
                    domain=self._domain,
                    url=url,
                    name=f"pid-{pid}",
                    price="1.00 USD",
                    description=None,
                    specs=None,
                    available=None,
                )
            ]
        if isinstance(gid, str) and gid.isdigit():
            # Simulate a listing page: link points to a pid add page.
            pid_url = f"https://{self._domain}/cart.php?a=add&pid={gid}"
            pid_norm = f"{self._domain}::{normalize_url_for_id(pid_url)}"
            return [
                Product(
                    id=pid_norm,
                    domain=self._domain,
                    url=pid_url,
                    name=f"gid-{gid}",
                    price="1.00 USD",
                    description=None,
                    specs=None,
                    available=None,
                )
            ]
        return []


class _NoopParser:
    def parse(self, html: str, *, base_url: str) -> list[Product]:
        return []


class _DynamicGidClient(_FakeClient):
    def __init__(self, domain: str) -> None:
        super().__init__(pages={})
        self._domain = domain
        self._counter = 0

    def fetch_text(self, url: str, *, allow_flaresolverr: bool = True) -> _Fetch:
        self.calls.append(url)
        qs = parse_qs(urlparse(url).query)
        gid = (qs.get("gid") or [None])[0]
        if isinstance(gid, str) and gid.isdigit():
            # Same logical content with a changing nonce to defeat simple page-signature streak checks.
            self._counter += 1
            html = (
                f"<html><a href='/cart.php?a=add&pid=11'>Add to cart</a>"
                f"<div data-nonce='{self._counter}'></div></html>"
            )
            return _Fetch(url=url, ok=True, text=html)
        return super().fetch_text(url, allow_flaresolverr=allow_flaresolverr)


class TestWhmcsHiddenScanner(unittest.TestCase):
    def test_scanner_stops_after_consecutive_misses_and_returns_in_stock_only(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"

        # gid=1 has a product and a purchase button (in stock)
        gid1 = f"https://{domain}/cart.php?gid=1"
        # pid=1 is in stock; pid=2 is out of stock
        pid1 = f"https://{domain}/cart.php?a=add&pid=1"
        pid2 = f"https://{domain}/cart.php?a=add&pid=2"

        pages = {
            gid1: "<html><a href='/cart.php?a=add&pid=1'>Add to cart</a></html>",
            pid1: "<html><button>Add to cart</button></html>",
            pid2: "<html>Out of stock</html>",
        }
        client = _FakeClient(pages)
        parser = _FakeParser(domain)

        env = {
            "WHMCS_HIDDEN_STOP_AFTER_MISS": "10",
            "WHMCS_HIDDEN_MIN_PROBE": "0",
            "WHMCS_HIDDEN_PID_CANDIDATES_MAX": "50",
            "WHMCS_HIDDEN_BATCH": "4",
            "WHMCS_HIDDEN_WORKERS": "2",
            "WHMCS_HIDDEN_HARD_MAX_PID": "30",
            "WHMCS_HIDDEN_HARD_MAX_GID": "30",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            out = _scan_whmcs_hidden_products(client, parser, base_url=base_url, existing_ids=set())

        self.assertTrue(any(p.url == pid1 for p in out))
        self.assertFalse(any(p.url == pid2 for p in out))

    def test_gid_scan_stops_after_five_same_pages(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"

        client = _FakeClient(pages={})
        parser = _FakeParser(domain)

        env = {
            "WHMCS_HIDDEN_GID_STOP_AFTER_SAME_PAGE": "5",
            "WHMCS_HIDDEN_BATCH": "1",
            "WHMCS_HIDDEN_WORKERS": "1",
            "WHMCS_HIDDEN_HARD_MAX_GID": "50",
            "WHMCS_HIDDEN_HARD_MAX_PID": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            _scan_whmcs_hidden_products(client, parser, base_url=base_url, existing_ids=set())

        gid_calls = 0
        for url in client.calls:
            qs = parse_qs(urlparse(url).query)
            if (qs.get("gid") or [None])[0] is not None:
                gid_calls += 1
        self.assertEqual(gid_calls, 5)

    def test_pid_scan_stops_after_ten_consecutive_no_info_pages(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"

        client = _FakeClient(pages={})
        parser = _FakeParser(domain)

        env = {
            "WHMCS_HIDDEN_PID_STOP_AFTER_NO_INFO": "10",
            "WHMCS_HIDDEN_BATCH": "1",
            "WHMCS_HIDDEN_WORKERS": "1",
            "WHMCS_HIDDEN_HARD_MAX_PID": "100",
            "WHMCS_HIDDEN_HARD_MAX_GID": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            _scan_whmcs_hidden_products(client, parser, base_url=base_url, existing_ids=set())

        pid_calls = 0
        for url in client.calls:
            qs = parse_qs(urlparse(url).query)
            if (qs.get("pid") or [None])[0] is not None:
                pid_calls += 1
        self.assertEqual(pid_calls, 10)

    def test_pid_duplicates_do_not_count_as_no_info_streak(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"

        pages = {}
        for pid in range(1, 12):
            pages[f"https://{domain}/cart.php?a=add&pid={pid}"] = "<html><button>Add to cart</button></html>"

        client = _FakeClient(pages=pages)
        parser = _FakeParser(domain)

        existing_ids = {
            f"{domain}::{normalize_url_for_id(f'https://{domain}/cart.php?a=add&pid={pid}')}"
            for pid in range(1, 11)
        }

        env = {
            "WHMCS_HIDDEN_PID_STOP_AFTER_NO_INFO": "10",
            "WHMCS_HIDDEN_BATCH": "1",
            "WHMCS_HIDDEN_WORKERS": "1",
            "WHMCS_HIDDEN_HARD_MAX_PID": "20",
            "WHMCS_HIDDEN_HARD_MAX_GID": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            out = _scan_whmcs_hidden_products(client, parser, base_url=base_url, existing_ids=existing_ids)

        pid11 = f"https://{domain}/cart.php?a=add&pid=11"
        self.assertTrue(any(p.url == pid11 for p in out))

    def test_gid_scan_stops_after_no_progress_even_when_signatures_change(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"

        client = _DynamicGidClient(domain=domain)
        parser = _NoopParser()

        env = {
            "WHMCS_HIDDEN_GID_STOP_AFTER_SAME_PAGE": "0",
            "WHMCS_HIDDEN_GID_STOP_AFTER_NO_PROGRESS": "6",
            "WHMCS_HIDDEN_BATCH": "1",
            "WHMCS_HIDDEN_WORKERS": "1",
            "WHMCS_HIDDEN_HARD_MAX_GID": "200",
            "WHMCS_HIDDEN_HARD_MAX_PID": "0",
            "WHMCS_HIDDEN_PID_CANDIDATES_MAX": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            _scan_whmcs_hidden_products(client, parser, base_url=base_url, existing_ids=set())

        gid_calls = 0
        for url in client.calls:
            qs = parse_qs(urlparse(url).query)
            if (qs.get("gid") or [None])[0] is not None:
                gid_calls += 1
        # One initial discovery hit + six consecutive no-progress ids.
        self.assertEqual(gid_calls, 7)

    def test_scan_respects_deadline(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"

        client = _DynamicGidClient(domain=domain)
        parser = _NoopParser()

        env = {
            "WHMCS_HIDDEN_BATCH": "1",
            "WHMCS_HIDDEN_WORKERS": "1",
            "WHMCS_HIDDEN_HARD_MAX_GID": "200",
            "WHMCS_HIDDEN_HARD_MAX_PID": "200",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            _scan_whmcs_hidden_products(
                client,
                parser,
                base_url=base_url,
                existing_ids=set(),
                deadline=time.perf_counter() - 0.001,
            )

        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
