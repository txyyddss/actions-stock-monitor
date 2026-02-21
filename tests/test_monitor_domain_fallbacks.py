from __future__ import annotations

import unittest

from actions_stock_monitor.models import Product
from actions_stock_monitor.monitor import _apply_domain_availability_fallbacks, _apply_domain_product_cleanup


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

    def test_app_vmiss_dedupes_bandwidth_and_traffic(self) -> None:
        products = [
            Product(
                id="app.vmiss.com::x",
                domain="app.vmiss.com",
                url="https://app.vmiss.com/cart.php?a=add&pid=1",
                name="Plan",
                price="10.00 USD",
                currency="USD",
                description=None,
                specs={"Bandwidth": "1200GB", "Traffic": "1200GB"},
                available=True,
            )
        ]
        out, diag = _apply_domain_product_cleanup("app.vmiss.com", products)
        self.assertEqual(diag.get("dropped_noise"), 0)
        specs = out[0].specs or {}
        self.assertIn("Bandwidth", specs)
        self.assertNotIn("Traffic", specs)

    def test_cloud_boil_drops_diy_false_positive(self) -> None:
        products = [
            Product(
                id="cloud.boil.network::x",
                domain="cloud.boil.network",
                url="https://cloud.boil.network/store/taiwan-shared-bandwidth/zone-3-diy-hinet-500mbps-deip",
                name="DIY false positive",
                price="8.00 USD",
                currency="USD",
                description=None,
                specs=None,
                available=True,
            )
        ]
        out, diag = _apply_domain_product_cleanup("cloud.boil.network", products)
        self.assertEqual(out, [])
        self.assertEqual(diag.get("dropped_noise"), 1)

    def test_zgovps_reconstructs_generic_tier_name(self) -> None:
        products = [
            Product(
                id="clients.zgovps.com::x",
                domain="clients.zgovps.com",
                url="https://clients.zgovps.com/cart.php?a=add&pid=22",
                name="Premium",
                price="18.00 USD",
                currency="USD",
                description=None,
                specs=None,
                available=True,
                variant_of="Los Angeles AMD VDS",
            )
        ]
        out, _diag = _apply_domain_product_cleanup("clients.zgovps.com", products)
        self.assertEqual(out[0].name, "Los Angeles AMD VDS - Premium")


if __name__ == "__main__":
    unittest.main()
