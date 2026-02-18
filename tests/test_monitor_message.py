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

        msg = _format_message("RESTOCK ALERT", "🟢", p, "2026-02-18T00:00:00+00:00")
        self.assertIn("RESTOCK ALERT", msg)
        self.assertIn("Example Plan", msg)
        self.assertIn("9.99 USD", msg)
        self.assertIn("Buy now", msg)
        self.assertIn("Detected:", msg)
        self.assertTrue(msg.startswith("<b>"))
        self.assertLessEqual(len(msg), 3900)


if __name__ == "__main__":
    unittest.main()

