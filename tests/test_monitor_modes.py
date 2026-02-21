from __future__ import annotations

import unittest
from unittest import mock

from actions_stock_monitor.models import DomainRun, Product
from actions_stock_monitor.monitor import run_monitor


def _make_product(*, pid: str, domain: str, url: str) -> Product:
    return Product(
        id=pid,
        domain=domain,
        url=url,
        name=pid,
        price="1.00 USD",
        currency="USD",
        description="",
        specs={},
        available=True,
    )


class TestMonitorModes(unittest.TestCase):
    def test_full_mode_merges_same_domain_targets_before_pruning(self) -> None:
        previous_state = {
            "products": {},
            "domains": {},
            "last_run": {"started_at": "2026-02-01T00:00:00+00:00"},
        }
        targets = ["https://example.test/a", "https://example.test/b"]

        def fake_scrape(_client, target: str, *, allow_expansion: bool = True) -> DomainRun:
            if target.endswith("/a"):
                products = [_make_product(pid="id-a", domain="example.test", url="https://example.test/p/a")]
            else:
                products = [_make_product(pid="id-b", domain="example.test", url="https://example.test/p/b")]
            return DomainRun(domain="example.test", ok=True, error=None, duration_ms=1, products=products)

        with mock.patch("actions_stock_monitor.monitor._scrape_target", side_effect=fake_scrape):
            state, _summary = run_monitor(
                previous_state=previous_state,
                targets=targets,
                timeout_seconds=5.0,
                max_workers=2,
                dry_run=True,
                mode="full",
            )

        self.assertIn("id-a", state["products"])
        self.assertIn("id-b", state["products"])

    def test_mode_controls_expansion_flag(self) -> None:
        previous_state = {"products": {}, "domains": {}, "last_run": {}}
        targets = ["https://example.test/"]

        calls_full: list[bool] = []
        calls_lite: list[bool] = []

        def fake_scrape_full(_client, target: str, *, allow_expansion: bool = True) -> DomainRun:
            calls_full.append(allow_expansion)
            return DomainRun(domain="example.test", ok=True, error=None, duration_ms=1, products=[])

        with mock.patch("actions_stock_monitor.monitor._scrape_target", side_effect=fake_scrape_full):
            run_monitor(
                previous_state=previous_state,
                targets=targets,
                timeout_seconds=5.0,
                max_workers=1,
                dry_run=True,
                mode="full",
            )

        def fake_scrape_lite(_client, target: str, *, allow_expansion: bool = True) -> DomainRun:
            calls_lite.append(allow_expansion)
            return DomainRun(domain="example.test", ok=True, error=None, duration_ms=1, products=[])

        with mock.patch("actions_stock_monitor.monitor._scrape_target", side_effect=fake_scrape_lite):
            run_monitor(
                previous_state=previous_state,
                targets=targets,
                timeout_seconds=5.0,
                max_workers=1,
                dry_run=True,
                mode="lite",
            )

        self.assertTrue(calls_full and all(calls_full))
        self.assertTrue(calls_lite and not any(calls_lite))

    def test_full_default_run_prunes_removed_domains(self) -> None:
        previous_state = {
            "products": {
                "old-id": {
                    "domain": "removed.test",
                    "url": "https://removed.test/p/1",
                    "name": "Old",
                    "price": "1.00 USD",
                    "currency": "USD",
                    "description": "",
                    "specs": {},
                    "available": True,
                }
            },
            "domains": {"removed.test": {"last_status": "ok"}},
            "last_run": {"started_at": "2026-02-01T00:00:00+00:00"},
        }

        def fake_scrape(_client, _target: str, *, allow_expansion: bool = True) -> DomainRun:
            return DomainRun(
                domain="kept.test",
                ok=True,
                error=None,
                duration_ms=1,
                products=[
                    Product(
                        id="kept-id",
                        domain="kept.test",
                        url="https://kept.test/p/1",
                        name="Kept",
                        price="1.00 USD",
                        currency="USD",
                        description="",
                        specs={},
                        available=True,
                    )
                ],
            )

        with mock.patch("actions_stock_monitor.monitor.DEFAULT_TARGETS", ["https://kept.test/"]):
            with mock.patch("actions_stock_monitor.monitor._scrape_target", side_effect=fake_scrape):
                state, _summary = run_monitor(
                    previous_state=previous_state,
                    targets=[],
                    timeout_seconds=5.0,
                    max_workers=1,
                    dry_run=True,
                    mode="full",
                )

        self.assertNotIn("removed.test", state["domains"])
        self.assertNotIn("old-id", state["products"])
        self.assertIn("kept-id", state["products"])

    def test_full_explicit_targets_does_not_prune_removed_domains(self) -> None:
        previous_state = {
            "products": {
                "old-id": {
                    "domain": "removed.test",
                    "url": "https://removed.test/p/1",
                    "name": "Old",
                    "price": "1.00 USD",
                    "currency": "USD",
                    "description": "",
                    "specs": {},
                    "available": True,
                }
            },
            "domains": {"removed.test": {"last_status": "ok"}},
            "last_run": {"started_at": "2026-02-01T00:00:00+00:00"},
        }

        def fake_scrape(_client, _target: str, *, allow_expansion: bool = True) -> DomainRun:
            return DomainRun(domain="kept.test", ok=True, error=None, duration_ms=1, products=[])

        with mock.patch("actions_stock_monitor.monitor._scrape_target", side_effect=fake_scrape):
            state, _summary = run_monitor(
                previous_state=previous_state,
                targets=["https://kept.test/"],
                timeout_seconds=5.0,
                max_workers=1,
                dry_run=True,
                mode="full",
            )

        self.assertIn("removed.test", state["domains"])
        self.assertIn("old-id", state["products"])


if __name__ == "__main__":
    unittest.main()
