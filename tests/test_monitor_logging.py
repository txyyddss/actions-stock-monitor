from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

from actions_stock_monitor.models import DomainRun, Product
from actions_stock_monitor.monitor import run_monitor


def _make_product(*, pid: str, domain: str, url: str, available: bool | None = True, is_special: bool = False) -> Product:
    return Product(
        id=pid,
        domain=domain,
        url=url,
        name=pid,
        price="1.00 USD",
        currency="USD",
        description="",
        specs={},
        available=available,
        is_special=is_special,
    )


class TestMonitorLogging(unittest.TestCase):
    def test_full_mode_logs_progress_and_summary(self) -> None:
        previous_state = {"products": {}, "domains": {}, "last_run": {}}
        targets = ["https://example.test/"]

        def fake_scrape(_client, _target: str, *, allow_expansion: bool = True) -> DomainRun:
            self.assertTrue(allow_expansion)
            return DomainRun(
                domain="example.test",
                ok=True,
                error=None,
                duration_ms=12,
                products=[_make_product(pid="id-1", domain="example.test", url="https://example.test/p/1")],
            )

        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"MONITOR_LOG": "1"}):
            with mock.patch("actions_stock_monitor.monitor._scrape_target", side_effect=fake_scrape):
                with redirect_stdout(buf):
                    run_monitor(
                        previous_state=previous_state,
                        targets=targets,
                        timeout_seconds=5.0,
                        max_workers=1,
                        dry_run=True,
                        mode="full",
                    )

        out = buf.getvalue()
        self.assertIn("[monitor] start mode=full", out)
        self.assertIn("selected_targets=1", out)
        self.assertIn("progress=1/1 target=https://example.test/ domain=example.test status=ok", out)
        self.assertIn("[monitor] merge mode=full", out)
        self.assertIn("[example.test] status=ok merged_targets=1 products=1", out)
        self.assertIn("[monitor] done mode=full", out)
        self.assertIn("tracked_products=1", out)

    def test_lite_mode_logs_selected_targets(self) -> None:
        previous_state = {
            "products": {
                "id-1": {
                    "domain": "example.test",
                    "url": "https://example.test/p/1",
                    "name": "P1",
                    "price": "1.00 USD",
                    "available": True,
                }
            },
            "domains": {"example.test": {"last_status": "ok"}},
            "last_run": {},
        }
        targets = ["https://example.test/", "https://other.test/"]

        def fake_scrape(_client, _target: str, *, allow_expansion: bool = True) -> DomainRun:
            self.assertFalse(allow_expansion)
            return DomainRun(domain="example.test", ok=True, error=None, duration_ms=3, products=[])

        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"MONITOR_LOG": "1"}):
            with mock.patch("actions_stock_monitor.monitor._scrape_target", side_effect=fake_scrape):
                with redirect_stdout(buf):
                    run_monitor(
                        previous_state=previous_state,
                        targets=targets,
                        timeout_seconds=5.0,
                        max_workers=1,
                        dry_run=True,
                        mode="lite",
                    )

        out = buf.getvalue()
        self.assertIn("[monitor] start mode=lite", out)
        self.assertIn("configured_targets=2", out)
        self.assertIn("selected_targets=1", out)
        self.assertIn("allow_expansion=False", out)
        self.assertIn("prune_missing_products=False", out)
        self.assertIn("target=https://example.test/", out)

    def test_logs_show_merged_target_count_per_domain(self) -> None:
        previous_state = {"products": {}, "domains": {}, "last_run": {}}
        targets = ["https://example.test/a", "https://example.test/b"]

        def fake_scrape(_client, target: str, *, allow_expansion: bool = True) -> DomainRun:
            pid = "id-a" if target.endswith("/a") else "id-b"
            product = _make_product(pid=pid, domain="example.test", url=f"https://example.test/p/{pid}")
            return DomainRun(domain="example.test", ok=True, error=None, duration_ms=7, products=[product])

        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"MONITOR_LOG": "1"}):
            with mock.patch("actions_stock_monitor.monitor._scrape_target", side_effect=fake_scrape):
                with redirect_stdout(buf):
                    run_monitor(
                        previous_state=previous_state,
                        targets=targets,
                        timeout_seconds=5.0,
                        max_workers=1,
                        dry_run=True,
                        mode="full",
                    )

        out = buf.getvalue()
        self.assertEqual(out.count("progress="), 2)
        self.assertIn("[example.test] status=ok merged_targets=2 products=2", out)


if __name__ == "__main__":
    unittest.main()
