from __future__ import annotations

import unittest

from actions_stock_monitor.dashboard import render_dashboard_html


class TestDashboard(unittest.TestCase):
    def test_renders_core_ui_bits(self) -> None:
        state = {
            "updated_at": "2026-02-18T00:00:00+00:00",
            "products": {
                "p1": {
                    "domain": "example.test",
                    "name": "Example Plan",
                    "price": "9.99 USD",
                    "available": True,
                    "specs": {"RAM": "2GB RAM"},
                    "url": "https://example.test/buy",
                    "first_seen": "2026-02-18T00:00:00+00:00",
                    "last_seen": "2026-02-18T00:00:00+00:00",
                }
            },
            "domains": {"example.test": {"last_status": "ok"}},
        }

        html = render_dashboard_html(state, run_summary={"finished_at": state["updated_at"]})
        self.assertIn("Restock Monitor", html)
        self.assertIn("Last updated:", html)
        self.assertIn("Buy Now", html)
        self.assertIn("Click headers to sort.", html)

    def test_embeds_products_json_safely(self) -> None:
        state = {
            "updated_at": "2026-02-18T00:00:00+00:00",
            "products": {
                "p1": {
                    "domain": "example.test",
                    "name": "Plan",
                    "price": "9.99 USD",
                    "available": True,
                    "specs": {},
                    "description": '</script><script>alert("x")</script>',
                    "url": "https://example.test/buy",
                    "first_seen": "2026-02-18T00:00:00+00:00",
                    "last_seen": "2026-02-18T00:00:00+00:00",
                }
            },
            "domains": {"example.test": {"last_status": "ok"}},
        }

        html = render_dashboard_html(state, run_summary={"finished_at": state["updated_at"]})
        self.assertIn('id="dashboard-data"', html)
        self.assertNotIn("</script><script>alert", html)
        self.assertIn("\\u003c/script\\u003e\\u003cscript\\u003ealert", html)


if __name__ == "__main__":
    unittest.main()
