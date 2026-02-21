from __future__ import annotations

import unittest
from dataclasses import dataclass

from actions_stock_monitor.models import Product
from actions_stock_monitor.monitor import _enrich_availability_via_product_pages


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
            return _Fetch(url=url, ok=False, text=None, error="HTTP 404", status_code=404)
        return _Fetch(url=url, ok=True, text=html, status_code=200)


class TestMonitorEnrichPriority(unittest.TestCase):
    def test_prioritizes_unknown_availability_over_location_only(self) -> None:
        products = [
            Product(
                id="example.test::loc",
                domain="example.test",
                url="https://example.test/p-loc",
                name="LocOnly",
                price="10.00 USD",
                currency="USD",
                description=None,
                specs=None,
                available=True,
                location=None,
            ),
            Product(
                id="example.test::unk",
                domain="example.test",
                url="https://example.test/p-unk",
                name="UnknownAvail",
                price="10.00 USD",
                currency="USD",
                description=None,
                specs=None,
                available=None,
                location="X",
            ),
        ]
        client = _FakeClient(
            pages={
                "https://example.test/p-loc": "<html><body><div>Location page</div></body></html>",
                "https://example.test/p-unk": "<html><body><button>Add to cart</button><select name='billingcycle'><option>Monthly</option></select></body></html>",
            }
        )

        out = _enrich_availability_via_product_pages(
            client,
            products,
            domain="example.test",
            max_pages=1,
            include_false=False,
            include_true=False,
            include_missing_cycles=False,
        )
        by_id = {p.id: p for p in out}
        self.assertTrue(by_id["example.test::unk"].available)
        self.assertEqual(client.calls, ["https://example.test/p-unk"])


if __name__ == "__main__":
    unittest.main()
