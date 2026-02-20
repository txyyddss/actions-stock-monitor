from __future__ import annotations

import unittest

from actions_stock_monitor.parsers.common import (
    extract_availability,
    extract_location_variants,
    extract_price,
    extract_specs,
    looks_like_purchase_action,
    normalize_url_for_id,
)


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

    def test_extract_availability_is_conservative_for_purchase_labels(self) -> None:
        self.assertIsNone(extract_availability("Add to cart"))
        self.assertIsNone(extract_availability("Order now"))
        self.assertTrue(looks_like_purchase_action("Add to cart"))
        self.assertTrue(looks_like_purchase_action("Order now"))
        self.assertFalse(looks_like_purchase_action("In stock"))

    def test_extract_availability_prefers_oos_over_weak_in_stock(self) -> None:
        self.assertFalse(extract_availability("Out of stock - Add to cart"))

    def test_extract_location_variants_examples(self) -> None:
        html_a = """
        <div class="form-group">
          <label for="inputConfigOption72">Location</label>
          <select name="configoption[72]" id="inputConfigOption72" class="form-control">
            <option value="174" selected="selected">New York, USA</option>
          </select>
        </div>
        """
        self.assertEqual(extract_location_variants(html_a), [("New York, USA", None)])

        html_b = """
        <div class="form-group border-bottom my-3 cart-form cart-item ">
          <label class="font-weight-bold d-block" for="custom[718]">Data Center *</label>
          <input name="custom[718]" value="14307" id="custom_field_718" type="radio" checked="checked">Hong Kong - GGC DC4 (TGT DC) <br>
        </div>
        """
        self.assertEqual(extract_location_variants(html_b), [("Hong Kong - GGC DC4 (TGT DC)", None)])

        html_c = """
        <div class="form-group col-12 mb-5 mt-0 option-val cart-form cart-item cf-select ">
          <div class="d-flex flex-row align-items-center mb-3"><h3>Zone </h3></div>
          <select name="custom[1646]">
            <option data-description="" data-val="9242" value="9242" selected="selected">Hong Kong DC3 </option>
          </select>
        </div>
        """
        self.assertEqual(extract_location_variants(html_c), [("Hong Kong DC3", None)])

        html_d = """
        <div class="form-group">
          <label for="inputConfigOption414">Location</label>
          <select name="configoption[414]" id="inputConfigOption414" class="form-control">
            <option value="1735" selected="selected">New York (Test IP: 192.3.81.8)</option>
            <option value="1734">Atlanta (Test IP: 107.173.164.160)</option>
          </select>
        </div>
        """
        self.assertEqual(extract_location_variants(html_d), [("New York", None), ("Atlanta", None)])


if __name__ == "__main__":
    unittest.main()
