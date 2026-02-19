from __future__ import annotations

import unittest

from actions_stock_monitor.parsers.common import extract_price, extract_specs, normalize_url_for_id


class TestParsersCommon(unittest.TestCase):
    def test_extract_price_symbols_and_codes(self) -> None:
        self.assertEqual(extract_price("Only $9.99 / mo"), ("9.99 USD", "USD"))
        self.assertEqual(extract_price("€10.00 / mo"), ("10.00 EUR", "EUR"))
        self.assertEqual(extract_price("pay 12 GBP today"), ("12 GBP", "GBP"))
        self.assertEqual(extract_price("¥ 5 / mo"), ("5 CNY", "CNY"))
        self.assertEqual(extract_price("\u4ece\u5f00\u59cb \u00a51,999.00 CNY \u6708\u7f34"), ("1999.00 CNY", "CNY"))

    def test_extract_specs(self) -> None:
        specs = extract_specs("2GB RAM 1 vCPU 20GB NVMe SSD 2TB bandwidth 1Gbps")
        self.assertIsNotNone(specs)
        assert specs is not None
        self.assertIn("RAM", specs)
        self.assertIn("CPU", specs)
        self.assertIn("Disk", specs)
        self.assertIn("Bandwidth", specs)
        self.assertIn("Port", specs)

    def test_normalize_url_for_id_removes_tracking(self) -> None:
        url = "https://example.test/p?a=1&utm_source=x&b=2&fbclid=y"
        self.assertEqual(normalize_url_for_id(url), "https://example.test/p?a=1&b=2")


if __name__ == "__main__":
    unittest.main()
