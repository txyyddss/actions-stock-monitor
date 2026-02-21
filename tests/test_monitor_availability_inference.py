from __future__ import annotations

import unittest

from actions_stock_monitor.monitor import _infer_availability_from_detail_html


class TestMonitorAvailabilityInference(unittest.TestCase):
    def test_generic_domain_prefers_oos_page_text(self) -> None:
        html = "<html><body><div>Out of stock</div><button>Add to Cart</button></body></html>"
        self.assertFalse(_infer_availability_from_detail_html(html))

    def test_cloud_colocrossing_ignores_noisy_page_oos_when_order_action_exists(self) -> None:
        html = "<html><body><div>Out of stock</div><button>Add to Cart</button></body></html>"
        self.assertTrue(_infer_availability_from_detail_html(html, domain="cloud.colocrossing.com"))

    def test_cloud_colocrossing_keeps_explicit_oos_controls(self) -> None:
        html = "<html><body><button disabled>Out of Stock</button></body></html>"
        self.assertFalse(_infer_availability_from_detail_html(html, domain="cloud.colocrossing.com"))

    def test_order_form_markers_beat_noisy_page_level_oos(self) -> None:
        html = (
            "<html><body>"
            "<div>Out of stock</div>"
            "<select name='billingcycle'><option>Monthly</option></select>"
            "<button>Add to Cart</button>"
            "</body></html>"
        )
        self.assertTrue(_infer_availability_from_detail_html(html, domain="app.vmiss.com"))


if __name__ == "__main__":
    unittest.main()
