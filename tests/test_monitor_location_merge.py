from __future__ import annotations

import unittest

from actions_stock_monitor.models import Product
from actions_stock_monitor.monitor import _canonical_product_key, _fill_cycle_price_defaults, _merge_products_by_canonical_plan


class TestMonitorLocationMerge(unittest.TestCase):
    def test_merge_same_plan_different_locations(self) -> None:
        products = [
            Product(
                id="example.test::https://example.test/cart.php?a=add&pid=10::loc-ny",
                domain="example.test",
                url="https://example.test/cart.php?a=add&pid=10",
                name="Plan A",
                price="10.00 USD",
                currency="USD",
                description=None,
                specs={"CPU": "2 vCPU"},
                available=False,
                location="New York",
            ),
            Product(
                id="example.test::https://example.test/cart.php?a=add&pid=10::loc-la",
                domain="example.test",
                url="https://example.test/cart.php?a=add&pid=10",
                name="Plan A",
                price="10.00 USD",
                currency="USD",
                description=None,
                specs={"CPU": "2 vCPU"},
                available=True,
                location="Los Angeles",
            ),
        ]

        merged = _merge_products_by_canonical_plan(products)
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0].available)
        self.assertEqual(merged[0].locations, ["New York", "Los Angeles"])
        self.assertEqual(merged[0].location_links, {"New York": products[0].url, "Los Angeles": products[1].url})

    def test_merge_availability_unknown_when_mixed_false_and_unknown(self) -> None:
        products = [
            Product(
                id="example.test::https://example.test/cart.php?a=add&pid=20::loc-sg",
                domain="example.test",
                url="https://example.test/cart.php?a=add&pid=20",
                name="Plan B",
                price=None,
                currency="USD",
                description=None,
                specs=None,
                available=False,
                location="Singapore",
            ),
            Product(
                id="example.test::https://example.test/cart.php?a=add&pid=20::loc-hk",
                domain="example.test",
                url="https://example.test/cart.php?a=add&pid=20",
                name="Plan B",
                price=None,
                currency="USD",
                description=None,
                specs=None,
                available=None,
                location="Hong Kong",
            ),
        ]
        merged = _merge_products_by_canonical_plan(products)
        self.assertEqual(len(merged), 1)
        self.assertIsNone(merged[0].available)

    def test_does_not_merge_distinct_store_paths_with_same_generic_name(self) -> None:
        products = [
            Product(
                id="example.test::https://example.test/store/zone-a-plan",
                domain="example.test",
                url="https://example.test/store/zone-a-plan",
                name="$3.00",
                price="3.00 USD",
                currency="USD",
                description=None,
                specs=None,
                available=True,
                location=None,
            ),
            Product(
                id="example.test::https://example.test/store/zone-b-plan",
                domain="example.test",
                url="https://example.test/store/zone-b-plan",
                name="$3.00",
                price="3.00 USD",
                currency="USD",
                description=None,
                specs=None,
                available=True,
                location=None,
            ),
        ]
        merged = _merge_products_by_canonical_plan(products)
        self.assertEqual(len(merged), 2)

    def test_fill_cycle_price_defaults_uses_monthly_when_missing(self) -> None:
        products = [
            Product(
                id="example.test::https://example.test/store/plan-a",
                domain="example.test",
                url="https://example.test/store/plan-a",
                name="Plan A",
                price="3.00 USD",
                currency="USD",
                description=None,
                specs=None,
                available=True,
                billing_cycles=["Monthly", "Quarterly"],
                cycle_prices=None,
            )
        ]
        filled = _fill_cycle_price_defaults(products)
        self.assertEqual(filled[0].cycle_prices, {"Monthly": "3.00 USD"})

    def test_fill_cycle_price_defaults_adds_monthly_cycle_when_absent(self) -> None:
        products = [
            Product(
                id="example.test::https://example.test/store/plan-b",
                domain="example.test",
                url="https://example.test/store/plan-b",
                name="Plan B",
                price="12.00 USD",
                currency="USD",
                description=None,
                specs=None,
                available=True,
                billing_cycles=None,
                cycle_prices=None,
            )
        ]
        filled = _fill_cycle_price_defaults(products)
        self.assertEqual(filled[0].billing_cycles, ["Monthly"])
        self.assertEqual(filled[0].cycle_prices, {"Monthly": "12.00 USD"})

    def test_canonical_key_keeps_rp_store_product_urls_distinct(self) -> None:
        a = Product(
            id="example.test::a",
            domain="example.test",
            url="https://example.test/index.php?rp=/store/hk-lite-plus/hklite-plus-1",
            name="Traffic/Speed - 2TB @ 1000Mbps",
            price="3.00 USD",
            currency="USD",
            description=None,
            specs=None,
            available=True,
        )
        b = Product(
            id="example.test::b",
            domain="example.test",
            url="https://example.test/index.php?rp=/store/hk-lite-plus/hklite-plus-2",
            name="Traffic/Speed - 2TB @ 1000Mbps",
            price="4.00 USD",
            currency="USD",
            description=None,
            specs=None,
            available=True,
        )
        self.assertNotEqual(_canonical_product_key(a), _canonical_product_key(b))


if __name__ == "__main__":
    unittest.main()
