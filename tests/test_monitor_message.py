from __future__ import annotations

import unittest

from actions_stock_monitor.models import Product
from actions_stock_monitor.monitor import _format_message


class TestMonitorMessage(unittest.TestCase):
    def test_format_message_contains_required_fields(self) -> None:
        p = Product(
            id="d::u",
            domain="example.test",
            url="https://example.test/buy",
            name="Example Plan",
            price="9.99 USD",
            currency="USD",
            description="desc",
            specs={"RAM": "2GB RAM", "CPU": "1 vCPU"},
            available=True,
        )

        msg = _format_message("RESTOCK ALERT", "RESTOCK", p, "2026-02-18T00:00:00+00:00")
        self.assertIn("RESTOCK ALERT", msg)
        self.assertIn("Example Plan", msg)
        self.assertIn("9.99 USD", msg)
        self.assertIn("Open Product Page", msg)
        self.assertIn("https://example.test/buy", msg)
        self.assertIn("In Stock", msg)
        self.assertIn("#example", msg)
        self.assertLessEqual(len(msg), 3900)

    def test_format_message_avoids_name_split_or_repeat(self) -> None:
        p = Product(
            id="d::u",
            domain="clients.zgovps.com",
            url="https://clients.zgovps.com/index.php?/cart/special-offer/&action=add&id=122",
            name="Premium",
            price="25.00 USD",
            currency="USD",
            description=None,
            specs={"CPU": "2 Cores"},
            available=True,
            variant_of="Los Angeles AMD VDS",
            location="Los Angeles",
        )
        msg = _format_message("NEW PRODUCT", "NEW", p, "2026-02-18T00:00:00+00:00")
        self.assertIn("Los Angeles AMD VDS - Premium", msg)
        # location should not be duplicated inside product title and info line excessively
        self.assertLessEqual(msg.count("Los Angeles"), 3)

    def test_format_message_with_cycle_prices_and_special(self) -> None:
        p = Product(
            id="d::u",
            domain="cloud.colocrossing.com",
            url="https://cloud.colocrossing.com/index.php?rp=/store/specials",
            name="Special Plan",
            price="$3.00/mo",
            currency="USD",
            description=None,
            specs={"Cycles": "Monthly", "RAM": "4GB"},
            available=True,
            cycle_prices={"Monthly": "$3.00", "Quarterly": "$8.00"},
            billing_cycles=["Monthly", "Quarterly"],
            is_special=True,
        )

        msg = _format_message("NEW PRODUCT", "NEW", p, "2026-02-18T00:00:00+00:00")
        self.assertIn("#colocrossing", msg)
        self.assertIn("Special Plan", msg)
        self.assertIn("Monthly: $3.00", msg)
        # Cycles must not be duplicated in specs block
        self.assertNotIn("Cycles:", msg)


if __name__ == "__main__":
    unittest.main()
