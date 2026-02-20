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
        self.assertTrue(msg.startswith("<b>"))
        self.assertLessEqual(len(msg), 3900)

    def test_format_message_with_location(self) -> None:
        p = Product(
            id="d::u",
            domain="greencloudvps.com",
            url="https://greencloudvps.com/billing/store/budget-kvm-vps/budget-2gb",
            name="Budget 2GB",
            price="25.00 USD",
            currency="USD",
            description=None,
            specs={"RAM": "2GB", "CPU": "2 vCPU", "Disk": "40GB", "Location": "Dallas"},
            available=True,
            variant_of="Budget KVM VPS",
            location="Dallas",
        )

        msg = _format_message("NEW LOCATION", "LOCATION", p, "2026-02-18T00:00:00+00:00")
        self.assertIn("Budget KVM VPS - Dallas", msg)
        self.assertIn("<b>Location:</b> Dallas", msg)
        self.assertIn("25.00 USD", msg)

    def test_format_message_with_cycle_prices_and_special(self) -> None:
        p = Product(
            id="d::u",
            domain="cloud.colocrossing.com",
            url="https://cloud.colocrossing.com/index.php?rp=/store/specials",
            name="Special Plan",
            price="$3.00/mo",
            currency="USD",
            description=None,
            specs=None,
            available=True,
            cycle_prices={"Monthly": "$3.00", "Quarterly": "$8.00"},
            billing_cycles=["Monthly", "Quarterly"],
            is_special=True,
        )

        msg = _format_message("NEW PRODUCT", "NEW", p, "2026-02-18T00:00:00+00:00")
        self.assertIn("#colocrossing", msg)
        self.assertIn("[SPECIAL] Special Plan", msg)
        self.assertIn("Cycle Prices", msg)
        self.assertIn("Monthly: $3.00", msg)
        self.assertIn("Tag:</b> Special/Promo", msg)

    def test_format_message_oos(self) -> None:
        p = Product(
            id="d::u",
            domain="test.com",
            url="https://test.com/buy",
            name="OOS Plan",
            price="5.00 USD",
            currency="USD",
            description=None,
            specs=None,
            available=False,
        )

        msg = _format_message("RESTOCK ALERT", "RESTOCK", p, "2026-02-18T00:00:00+00:00")
        self.assertIn("Out of Stock", msg)


if __name__ == "__main__":
    unittest.main()
