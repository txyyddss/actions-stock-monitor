from __future__ import annotations

import unittest

from actions_stock_monitor.models import Product
from actions_stock_monitor.monitor import _apply_domain_availability_fallbacks


class TestMonitorDomainFallbacks(unittest.TestCase):
    def test_zgovps_unknown_add_id_with_price_becomes_in_stock(self) -> None:
        products = [
            Product(
                id="clients.zgovps.com::x",
                domain="clients.zgovps.com",
                url="https://clients.zgovps.com/index.php?/cart/special-offer/&action=add&id=122&cycle=a",
                name="Special Plan",
                price="88.00 USD",
                currency="USD",
                description=None,
                specs=None,
                available=None,
            )
        ]
        out = _apply_domain_availability_fallbacks("clients.zgovps.com", products)
        self.assertTrue(out[0].available)

    def test_non_target_domain_is_unchanged(self) -> None:
        products = [
            Product(
                id="example.test::x",
                domain="example.test",
                url="https://example.test/cart.php?action=add&id=1",
                name="Plan",
                price="10.00 USD",
                currency="USD",
                description=None,
                specs=None,
                available=None,
            )
        ]
        out = _apply_domain_availability_fallbacks("example.test", products)
        self.assertIsNone(out[0].available)


if __name__ == "__main__":
    unittest.main()
