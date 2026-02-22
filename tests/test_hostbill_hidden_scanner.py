from __future__ import annotations

import os
import unittest
from dataclasses import dataclass
from unittest import mock
from urllib.parse import parse_qs, urlparse

from actions_stock_monitor.models import Product
from actions_stock_monitor.monitor import _discover_candidate_pages, _is_hostbill_domain, _scan_whmcs_hidden_products
from actions_stock_monitor.parsers.common import normalize_url_for_id


@dataclass
class _Fetch:
    url: str
    ok: bool
    text: str | None
    error: str | None = None
    status_code: int | None = 200


class _HostBillFakeClient:
    def __init__(self, domain: str) -> None:
        self._domain = domain
        self.calls: list[str] = []

    def fetch_text(self, url: str, *, allow_flaresolverr: bool = True) -> _Fetch:
        self.calls.append(url)
        return _Fetch(
            url=f"https://{self._domain}/index.php?/cart/",
            ok=True,
            text="<html>default page</html>",
        )


class _HostBillFidToIdClient(_HostBillFakeClient):
    def fetch_text(self, url: str, *, allow_flaresolverr: bool = True) -> _Fetch:
        self.calls.append(url)
        qs = parse_qs(urlparse(url).query or "")
        fid = (qs.get("fid") or [None])[0]
        plan_id = (qs.get("id") or [None])[0]
        if isinstance(fid, str) and fid.isdigit():
            return _Fetch(
                url=url,
                ok=True,
                text="<html><form action='/index.php?/cart/special-offer/'><input type='hidden' name='id' value='11'></form></html>",
            )
        if plan_id == "11":
            return _Fetch(
                # Valid flow may redirect to a URL without id.
                url=f"https://{self._domain}/index.php?/cart/&step=3",
                ok=True,
                text="<html><button>Add to cart</button></html>",
            )
        return super().fetch_text(url, allow_flaresolverr=allow_flaresolverr)


class _HostBillEscapedIdClient(_HostBillFakeClient):
    def fetch_text(self, url: str, *, allow_flaresolverr: bool = True) -> _Fetch:
        self.calls.append(url)
        qs = parse_qs(urlparse(url).query or "")
        fid = (qs.get("fid") or [None])[0]
        plan_id = (qs.get("id") or [None])[0]
        if isinstance(fid, str) and fid.isdigit():
            return _Fetch(
                url=url,
                ok=True,
                text="<html><a href='/index.php?/cart/special-offer/&action=add&amp;id=12'>Order</a></html>",
            )
        if plan_id == "12":
            return _Fetch(
                url=f"https://{self._domain}/index.php?/cart/&step=3",
                ok=True,
                text="<html>Out of stock</html>",
            )
        return super().fetch_text(url, allow_flaresolverr=allow_flaresolverr)


class _HostBillCatIdClient(_HostBillFakeClient):
    def fetch_text(self, url: str, *, allow_flaresolverr: bool = True) -> _Fetch:
        self.calls.append(url)
        qs = parse_qs(urlparse(url).query or "")
        cat_id = (qs.get("cat_id") or [None])[0]
        plan_id = (qs.get("id") or [None])[0]
        if plan_id == "94" and isinstance(cat_id, str) and cat_id.isdigit():
            return _Fetch(
                url=f"https://{self._domain}/index.php?/cart/&step=3",
                ok=True,
                text="<html><button>Add to cart</button></html>",
            )
        if isinstance(cat_id, str) and cat_id.isdigit():
            return _Fetch(
                url=url,
                ok=True,
                text="<html><a href='/index.php?/cart/special-offer/&action=add&id=94'>Order</a></html>",
            )
        return super().fetch_text(url, allow_flaresolverr=allow_flaresolverr)


class _HostBillParser:
    def __init__(self, domain: str) -> None:
        self._domain = domain

    def parse(self, html: str, *, base_url: str) -> list[Product]:
        qs = parse_qs(urlparse(base_url).query or "")
        plan_id = (qs.get("id") or [None])[0]
        if not (isinstance(plan_id, str) and plan_id.isdigit()):
            return []
        url = base_url
        pid_norm = f"{self._domain}::{normalize_url_for_id(url)}"
        avail: bool | None = None
        if "out of stock" in (html or "").lower():
            avail = False
        return [
            Product(
                id=pid_norm,
                domain=self._domain,
                url=url,
                name=f"id-{plan_id}",
                price="9.00 USD",
                description=None,
                specs=None,
                available=avail,
            )
        ]


class _NoopParser:
    def parse(self, html: str, *, base_url: str) -> list[Product]:
        return []


class TestHostBillHiddenScanner(unittest.TestCase):
    def test_hostbill_detection_heuristics(self) -> None:
        self.assertTrue(_is_hostbill_domain("example.test", "<html>index.php?/cart/</html>"))
        self.assertFalse(_is_hostbill_domain("example.test", "<html>rp=/store</html>"))

    def test_fid_scan_discovers_id_from_hidden_inputs(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"
        client = _HostBillFidToIdClient(domain)
        parser = _HostBillParser(domain)

        env = {
            "WHMCS_HIDDEN_BATCH": "1",
            "WHMCS_HIDDEN_WORKERS": "1",
            "WHMCS_HIDDEN_HARD_MAX_GID": "3",
            "WHMCS_HIDDEN_HARD_MAX_PID": "20",
            "WHMCS_HIDDEN_PID_CANDIDATES_MAX": "20",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            out = _scan_whmcs_hidden_products(
                client,
                parser,
                base_url=base_url,
                existing_ids=set(),
                platform="hostbill",
            )

        self.assertTrue(any("id=11" in p.url for p in out))
        self.assertTrue(any(p.available is None for p in out))

    def test_fid_scan_extracts_escaped_id_and_keeps_out_of_stock(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"
        client = _HostBillEscapedIdClient(domain)
        parser = _HostBillParser(domain)

        env = {
            "WHMCS_HIDDEN_BATCH": "1",
            "WHMCS_HIDDEN_WORKERS": "1",
            "WHMCS_HIDDEN_HARD_MAX_GID": "3",
            "WHMCS_HIDDEN_HARD_MAX_PID": "20",
            "WHMCS_HIDDEN_PID_CANDIDATES_MAX": "20",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            out = _scan_whmcs_hidden_products(
                client,
                parser,
                base_url=base_url,
                existing_ids=set(),
                platform="hostbill",
            )

        hit = [p for p in out if "id=12" in p.url]
        self.assertTrue(hit)
        self.assertTrue(any(p.available is False for p in hit))

    def test_hostbill_scans_start_from_zero_for_fid_and_id(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"
        client = _HostBillFakeClient(domain)
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
            _scan_whmcs_hidden_products(
                client,
                parser,
                base_url=base_url,
                existing_ids=set(),
                platform="hostbill",
            )

        fid_calls = [u for u in client.calls if "fid=" in u]
        id_calls = [u for u in client.calls if "action=add" in u and "id=" in u]
        self.assertTrue(fid_calls and "fid=0" in fid_calls[0])
        self.assertTrue(id_calls and "id=0" in id_calls[0])

    def test_hostbill_id_scan_stops_after_duplicate_redirect_signature(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"
        client = _HostBillFakeClient(domain)
        parser = _NoopParser()

        env = {
            "WHMCS_HIDDEN_PID_STOP_AFTER_NO_INFO": "0",
            "WHMCS_HIDDEN_PID_STOP_AFTER_NO_PROGRESS": "0",
            "WHMCS_HIDDEN_PID_STOP_AFTER_DUPLICATES": "0",
            "WHMCS_HIDDEN_REDIRECT_SIGNATURE_STOP_AFTER": "4",
            "WHMCS_HIDDEN_BATCH": "1",
            "WHMCS_HIDDEN_WORKERS": "1",
            "WHMCS_HIDDEN_HARD_MAX_GID": "-1",
            "WHMCS_HIDDEN_HARD_MAX_PID": "200",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            _scan_whmcs_hidden_products(
                client,
                parser,
                base_url=base_url,
                existing_ids=set(),
                platform="hostbill",
            )

        probed_ids: set[int] = set()
        for url in client.calls:
            qs = parse_qs(urlparse(url).query or "")
            raw_id = (qs.get("id") or [None])[0]
            if isinstance(raw_id, str) and raw_id.isdigit() and "action=add" in url:
                probed_ids.add(int(raw_id))
        self.assertEqual(len(probed_ids), 4)

    def test_hostbill_cat_id_group_scan_discovers_ids(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/"
        client = _HostBillCatIdClient(domain)
        parser = _HostBillParser(domain)

        env = {
            "WHMCS_HIDDEN_BATCH": "1",
            "WHMCS_HIDDEN_WORKERS": "1",
            "WHMCS_HIDDEN_HARD_MAX_GID": "3",
            "WHMCS_HIDDEN_HARD_MAX_PID": "120",
            "WHMCS_HIDDEN_PID_CANDIDATES_MAX": "20",
            "WHMCS_HIDDEN_PID_STOP_AFTER_NO_INFO": "8",
            "WHMCS_HIDDEN_GID_STOP_AFTER_SAME_PAGE": "2",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            out = _scan_whmcs_hidden_products(
                client,
                parser,
                base_url=base_url,
                existing_ids=set(),
                seed_urls=[f"https://{domain}/index.php?/cart/&cat_id=7"],
                platform="hostbill",
            )

        self.assertTrue(any("id=94" in p.url for p in out))
        self.assertTrue(any("cat_id=" in u for u in client.calls))

    def test_discovery_considers_cat_id_onclick_links(self) -> None:
        domain = "example.test"
        base_url = f"https://{domain}/index.php?/cart/"
        html = """
        <html>
          <div onclick="window.location='index.php?/cart/&cat_id=7'">Category</div>
        </html>
        """
        out = _discover_candidate_pages(html, base_url=base_url, domain=domain)
        self.assertIn(f"https://{domain}/index.php?/cart/&cat_id=7", out)


if __name__ == "__main__":
    unittest.main()
