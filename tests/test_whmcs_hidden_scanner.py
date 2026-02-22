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


class _DynamicPidClient(_FakeClient):
    def __init__(self, domain: str) -> None:
        super().__init__(pages={})
        self._domain = domain

    def fetch_text(self, url: str, *, allow_flaresolverr: bool = True) -> _Fetch:
        self.calls.append(url)
        qs = parse_qs(urlparse(url).query)
        pid = (qs.get("pid") or [None])[0]
        if isinstance(pid, str) and pid.isdigit():
            return _Fetch(url=url, ok=True, text="<html><button>Add to cart</button></html>")
        return super().fetch_text(url, allow_flaresolverr=allow_flaresolverr)


class _RedirectingPidClient(_FakeClient):
    def __init__(self, domain: str) -> None:
        super().__init__(pages={})
        self._domain = domain

    def fetch_text(self, url: str, *, allow_flaresolverr: bool = True) -> _Fetch:
        self.calls.append(url)
        qs = parse_qs(urlparse(url).query)
        pid = (qs.get("pid") or [None])[0]
        if pid == "11":
            return _Fetch(
                # Simulate a valid redirect flow where the final URL drops pid.
                url=f"https://{self._domain}/cart.php?a=confproduct&i=0",
                ok=True,
                text="<html><input type='hidden' name='pid' value='11'><button>Add to cart</button></html>",
            )
        return super().fetch_text(url, allow_flaresolverr=allow_flaresolverr)


class _RedirectingPidNoEvidenceClient(_FakeClient):
    def __init__(self, domain: str) -> None:
        super().__init__(pages={})
        self._domain = domain

    def fetch_text(self, url: str, *, allow_flaresolverr: bool = True) -> _Fetch:
        self.calls.append(url)
        qs = parse_qs(urlparse(url).query)
        pid = (qs.get("pid") or [None])[0]
        if isinstance(pid, str) and pid.isdigit():
            return _Fetch(
                # Redirect flow that keeps pid only in HTML, but does not expose
                # parser/evidence markers. Stop logic should still terminate.
                url=f"https://{self._domain}/cart.php?a=confproduct&i=0",
                ok=True,
                text=f"<html><input type='hidden' name='pid' value='{pid}'></html>",
            )
        return super().fetch_text(url, allow_flaresolverr=allow_flaresolverr)


class _RedirectingPidNoIdMentionButEvidenceClient(_FakeClient):
    def __init__(self, domain: str) -> None:
        super().__init__(pages={})
        self._domain = domain

    def fetch_text(self, url: str, *, allow_flaresolverr: bool = True) -> _Fetch:
        self.calls.append(url)
        qs = parse_qs(urlparse(url).query)
        pid = (qs.get("pid") or [None])[0]
        if pid == "11":
            return _Fetch(
                # Valid redirect flow: no pid in final URL and no explicit pid in HTML.
                # Page still carries strong product-config markers.
                url=f"https://{self._domain}/cart.php?a=confproduct&i=0",
                ok=True,
                text="<html><select name='billingcycle'><option>Monthly</option></select><button>Add to cart</button></html>",
            )
        return super().fetch_text(url, allow_flaresolverr=allow_flaresolverr)


class _RedirectingGidClient(_FakeClient):
    def __init__(self, domain: str, pages: dict[str, str]) -> None:
        super().__init__(pages=pages)
        self._domain = domain

    def fetch_text(self, url: str, *, allow_flaresolverr: bool = True) -> _Fetch:
        self.calls.append(url)
        qs = parse_qs(urlparse(url).query)
        gid = (qs.get("gid") or [None])[0]
        if isinstance(gid, str) and gid.isdigit():
            # Simulate a redirect flow that drops gid from final URL while
            # preserving product candidates in HTML.
            return _Fetch(
                url=f"https://{self._domain}/cart.php",
                ok=True,
                text="<html><a href='/cart.php?a=add&pid=11'>Add to cart</a></html>",
            )
        return super().fetch_text(url, allow_flaresolverr=allow_flaresolverr)


class TestWhmcsHiddenScanner(unittest.TestCase):
    def test_scanner_stops_after_consecutive_misses_and_returns_all_stock_states(self) -> None:
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
        self.assertTrue(any(p.url == pid2 for p in out))

    def test_scanner_starts_from_zero_for_gid_and_pid(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"
        client = _FakeClient(pages={})
        parser = _NoopParser()

        env = {
            "WHMCS_HIDDEN_GID_STOP_AFTER_SAME_PAGE": "1",
            "WHMCS_HIDDEN_PID_STOP_AFTER_NO_INFO": "1",
            "WHMCS_HIDDEN_BATCH": "1",
            "WHMCS_HIDDEN_WORKERS": "1",
            "WHMCS_HIDDEN_HARD_MAX_GID": "10",
            "WHMCS_HIDDEN_HARD_MAX_PID": "10",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            _scan_whmcs_hidden_products(client, parser, base_url=base_url, existing_ids=set())

        gid_calls = [u for u in client.calls if "gid=" in u]
        pid_calls = [u for u in client.calls if "pid=" in u]
        self.assertTrue(gid_calls and gid_calls[0].endswith("gid=0"))
        self.assertTrue(pid_calls and pid_calls[0].endswith("pid=0"))

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

    def test_pid_scan_stops_after_duplicate_streak(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"
        client = _DynamicPidClient(domain=domain)
        parser = _FakeParser(domain)
        existing_ids = {
            f"{domain}::{normalize_url_for_id(f'https://{domain}/cart.php?a=add&pid={pid}')}"
            for pid in range(0, 100)
        }

        env = {
            "WHMCS_HIDDEN_PID_STOP_AFTER_NO_INFO": "0",
            "WHMCS_HIDDEN_PID_STOP_AFTER_NO_PROGRESS": "0",
            "WHMCS_HIDDEN_PID_STOP_AFTER_DUPLICATES": "5",
            "WHMCS_HIDDEN_BATCH": "1",
            "WHMCS_HIDDEN_WORKERS": "1",
            "WHMCS_HIDDEN_HARD_MAX_PID": "200",
            "WHMCS_HIDDEN_HARD_MAX_GID": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            _scan_whmcs_hidden_products(client, parser, base_url=base_url, existing_ids=existing_ids)

        pid_calls = 0
        for url in client.calls:
            qs = parse_qs(urlparse(url).query)
            if (qs.get("pid") or [None])[0] is not None:
                pid_calls += 1
        self.assertEqual(pid_calls, 5)

    def test_gid_redirect_without_gid_can_still_discover_pid_candidates(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"
        pid11 = f"https://{domain}/cart.php?a=add&pid=11"
        client = _RedirectingGidClient(
            domain=domain,
            pages={
                pid11: "<html><button>Add to cart</button></html>",
            },
        )
        parser = _FakeParser(domain)

        env = {
            "WHMCS_HIDDEN_GID_STOP_AFTER_SAME_PAGE": "0",
            "WHMCS_HIDDEN_GID_STOP_AFTER_NO_PROGRESS": "6",
            "WHMCS_HIDDEN_PID_STOP_AFTER_NO_INFO": "6",
            "WHMCS_HIDDEN_BATCH": "1",
            "WHMCS_HIDDEN_WORKERS": "1",
            "WHMCS_HIDDEN_HARD_MAX_GID": "4",
            "WHMCS_HIDDEN_HARD_MAX_PID": "20",
            "WHMCS_HIDDEN_PID_CANDIDATES_MAX": "20",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            out = _scan_whmcs_hidden_products(client, parser, base_url=base_url, existing_ids=set())

        self.assertTrue(any(p.url == pid11 for p in out))

    def test_gid_scan_stops_after_same_redirect_signature(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"
        client = _FakeClient(pages={})
        parser = _NoopParser()

        env = {
            "WHMCS_HIDDEN_GID_STOP_AFTER_SAME_PAGE": "0",
            "WHMCS_HIDDEN_GID_STOP_AFTER_NO_PROGRESS": "0",
            "WHMCS_HIDDEN_GID_STOP_AFTER_DUPLICATES": "0",
            "WHMCS_HIDDEN_REDIRECT_SIGNATURE_STOP_AFTER": "6",
            "WHMCS_HIDDEN_BATCH": "1",
            "WHMCS_HIDDEN_WORKERS": "1",
            "WHMCS_HIDDEN_HARD_MAX_GID": "200",
            "WHMCS_HIDDEN_HARD_MAX_PID": "-1",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            _scan_whmcs_hidden_products(client, parser, base_url=base_url, existing_ids=set())

        gid_calls = 0
        for url in client.calls:
            qs = parse_qs(urlparse(url).query)
            if (qs.get("gid") or [None])[0] is not None:
                gid_calls += 1
        self.assertEqual(gid_calls, 6)

    def test_pid_scan_stops_after_same_redirect_signature(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"
        client = _FakeClient(pages={})
        parser = _NoopParser()

        env = {
            "WHMCS_HIDDEN_PID_STOP_AFTER_NO_INFO": "0",
            "WHMCS_HIDDEN_PID_STOP_AFTER_NO_PROGRESS": "0",
            "WHMCS_HIDDEN_REDIRECT_SIGNATURE_STOP_AFTER": "6",
            "WHMCS_HIDDEN_BATCH": "1",
            "WHMCS_HIDDEN_WORKERS": "1",
            "WHMCS_HIDDEN_HARD_MAX_PID": "200",
            "WHMCS_HIDDEN_HARD_MAX_GID": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            _scan_whmcs_hidden_products(client, parser, base_url=base_url, existing_ids=set())

        pid_calls = 0
        for url in client.calls:
            qs = parse_qs(urlparse(url).query)
            if (qs.get("pid") or [None])[0] is not None:
                pid_calls += 1
        self.assertEqual(pid_calls, 6)

    def test_pid_scan_handles_valid_redirect_without_pid_in_final_url(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"
        client = _RedirectingPidClient(domain=domain)
        parser = _FakeParser(domain)

        env = {
            "WHMCS_HIDDEN_PID_STOP_AFTER_NO_INFO": "0",
            "WHMCS_HIDDEN_PID_STOP_AFTER_NO_PROGRESS": "0",
            "WHMCS_HIDDEN_PID_STOP_AFTER_DUPLICATES": "0",
            "WHMCS_HIDDEN_REDIRECT_SIGNATURE_STOP_AFTER": "0",
            "WHMCS_HIDDEN_BATCH": "1",
            "WHMCS_HIDDEN_WORKERS": "1",
            "WHMCS_HIDDEN_HARD_MAX_PID": "12",
            "WHMCS_HIDDEN_HARD_MAX_GID": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            out = _scan_whmcs_hidden_products(client, parser, base_url=base_url, existing_ids=set())

        pid11 = f"https://{domain}/cart.php?a=add&pid=11"
        self.assertTrue(any(p.url == pid11 for p in out))

    def test_pid_scan_handles_valid_redirect_without_pid_mention_when_page_has_pid_markers(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"
        client = _RedirectingPidNoIdMentionButEvidenceClient(domain=domain)
        parser = _FakeParser(domain)

        env = {
            "WHMCS_HIDDEN_PID_STOP_AFTER_NO_INFO": "0",
            "WHMCS_HIDDEN_PID_STOP_AFTER_NO_PROGRESS": "0",
            "WHMCS_HIDDEN_PID_STOP_AFTER_DUPLICATES": "0",
            "WHMCS_HIDDEN_REDIRECT_SIGNATURE_STOP_AFTER": "0",
            "WHMCS_HIDDEN_BATCH": "1",
            "WHMCS_HIDDEN_WORKERS": "1",
            "WHMCS_HIDDEN_HARD_MAX_PID": "12",
            "WHMCS_HIDDEN_HARD_MAX_GID": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            out = _scan_whmcs_hidden_products(client, parser, base_url=base_url, existing_ids=set())

        pid11 = f"https://{domain}/cart.php?a=add&pid=11"
        self.assertTrue(any(p.url == pid11 for p in out))

    def test_pid_redirect_without_pid_still_honors_redirect_signature_stop(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"
        client = _RedirectingPidNoEvidenceClient(domain=domain)
        parser = _NoopParser()

        env = {
            "WHMCS_HIDDEN_PID_STOP_AFTER_NO_INFO": "0",
            "WHMCS_HIDDEN_PID_STOP_AFTER_NO_PROGRESS": "0",
            "WHMCS_HIDDEN_REDIRECT_SIGNATURE_STOP_AFTER": "6",
            "WHMCS_HIDDEN_BATCH": "1",
            "WHMCS_HIDDEN_WORKERS": "1",
            "WHMCS_HIDDEN_HARD_MAX_PID": "200",
            "WHMCS_HIDDEN_HARD_MAX_GID": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            _scan_whmcs_hidden_products(client, parser, base_url=base_url, existing_ids=set())

        pid_calls = 0
        for url in client.calls:
            qs = parse_qs(urlparse(url).query)
            if (qs.get("pid") or [None])[0] is not None:
                pid_calls += 1
        self.assertEqual(pid_calls, 6)


if __name__ == "__main__":
    unittest.main()
