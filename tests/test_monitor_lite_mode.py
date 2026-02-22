from __future__ import annotations

import unittest

from actions_stock_monitor.models import DomainRun, Product
from actions_stock_monitor.monitor import _select_lite_targets, _update_state_from_runs


def _state_record(*, domain: str, url: str, name: str, available: bool) -> dict:
    return {
        "domain": domain,
        "url": url,
        "name": name,
        "price": "10.00 USD",
        "currency": "USD",
        "description": "",
        "specs": {},
        "variant_of": None,
        "location": None,
        "billing_cycles": [],
        "cycle_prices": {},
        "is_special": False,
        "available": available,
        "first_seen": "2026-02-01T00:00:00+00:00",
        "last_seen": "2026-02-01T00:00:00+00:00",
        "last_change": "2026-02-01T00:00:00+00:00",
        "last_notified_new": None,
        "last_notified_restock": None,
        "last_notified_new_location": None,
    }


class TestMonitorLiteMode(unittest.TestCase):
    def test_select_lite_targets_from_state(self) -> None:
        previous_state = {
            "domains": {
                "example.com": {},
            },
            "products": {
                "foo::p1": {
                    "domain": "foo.test",
                    "url": "https://foo.test/product/1",
                }
            },
        }
        fallback_targets = [
            "https://example.com/store",
            "https://bar.test/store",
        ]

        selected = _select_lite_targets(previous_state=previous_state, fallback_targets=fallback_targets)

        self.assertEqual(selected[0], "https://example.com/store")
        self.assertNotIn("https://foo.test/product/1", selected)
        self.assertNotIn("https://bar.test/store", selected)

    def test_lite_mode_keeps_unseen_products(self) -> None:
        previous_state = {
            "products": {
                "id-1": _state_record(domain="example.com", url="https://example.com/p/1", name="Plan 1", available=False),
                "id-2": _state_record(domain="example.com", url="https://example.com/p/2", name="Plan 2", available=True),
            },
            "domains": {},
            "last_run": {"started_at": "2026-02-01T00:00:00+00:00"},
        }
        run = DomainRun(
            domain="example.com",
            ok=True,
            error=None,
            duration_ms=123,
            products=[
                Product(
                    id="id-1",
                    domain="example.com",
                    url="https://example.com/p/1",
                    name="Plan 1",
                    price="10.00 USD",
                    currency="USD",
                    description="",
                    specs={},
                    available=True,
                )
            ],
        )

        state_lite, _ = _update_state_from_runs(
            previous_state,
            [run],
            dry_run=True,
            timeout_seconds=5.0,
            prune_missing_products=False,
        )
        state_full, _ = _update_state_from_runs(
            previous_state,
            [run],
            dry_run=True,
            timeout_seconds=5.0,
            prune_missing_products=True,
        )

        self.assertIn("id-2", state_lite["products"])
        self.assertNotIn("id-2", state_full["products"])

    def test_full_mode_skips_prune_when_run_marked_incomplete(self) -> None:
        previous_state = {
            "products": {
                "id-1": _state_record(domain="example.com", url="https://example.com/p/1", name="Plan 1", available=False),
                "id-2": _state_record(domain="example.com", url="https://example.com/p/2", name="Plan 2", available=True),
            },
            "domains": {},
            "last_run": {"started_at": "2026-02-01T00:00:00+00:00"},
        }
        run = DomainRun(
            domain="example.com",
            ok=True,
            error=None,
            duration_ms=123,
            products=[
                Product(
                    id="id-1",
                    domain="example.com",
                    url="https://example.com/p/1",
                    name="Plan 1",
                    price="10.00 USD",
                    currency="USD",
                    description="",
                    specs={},
                    available=True,
                )
            ],
            meta={
                "may_be_incomplete": True,
                "deadline_exceeded": False,
                "discovery_stop_reason": "queue_exhausted",
                "discovery_fetch_errors": 2,
            },
        )

        state_full, _ = _update_state_from_runs(
            previous_state,
            [run],
            dry_run=True,
            timeout_seconds=5.0,
            prune_missing_products=True,
        )

        self.assertIn("id-2", state_full["products"])

    def test_full_mode_still_prunes_when_run_is_complete(self) -> None:
        previous_state = {
            "products": {
                "id-1": _state_record(domain="example.com", url="https://example.com/p/1", name="Plan 1", available=False),
                "id-2": _state_record(domain="example.com", url="https://example.com/p/2", name="Plan 2", available=True),
            },
            "domains": {},
            "last_run": {"started_at": "2026-02-01T00:00:00+00:00"},
        }
        run = DomainRun(
            domain="example.com",
            ok=True,
            error=None,
            duration_ms=123,
            products=[
                Product(
                    id="id-1",
                    domain="example.com",
                    url="https://example.com/p/1",
                    name="Plan 1",
                    price="10.00 USD",
                    currency="USD",
                    description="",
                    specs={},
                    available=True,
                )
            ],
            meta={
                "may_be_incomplete": False,
                "deadline_exceeded": False,
                "discovery_stop_reason": "queue_exhausted",
                "discovery_fetch_errors": 0,
            },
        )

        state_full, _ = _update_state_from_runs(
            previous_state,
            [run],
            dry_run=True,
            timeout_seconds=5.0,
            prune_missing_products=True,
        )

        self.assertNotIn("id-2", state_full["products"])

    def test_full_mode_skips_prune_for_queue_exhausted_with_fetch_errors(self) -> None:
        previous_state = {
            "products": {
                "id-1": _state_record(domain="example.com", url="https://example.com/p/1", name="Plan 1", available=False),
                "id-2": _state_record(domain="example.com", url="https://example.com/p/2", name="Plan 2", available=True),
            },
            "domains": {},
            "last_run": {"started_at": "2026-02-01T00:00:00+00:00"},
        }
        run = DomainRun(
            domain="example.com",
            ok=True,
            error=None,
            duration_ms=123,
            products=[
                Product(
                    id="id-1",
                    domain="example.com",
                    url="https://example.com/p/1",
                    name="Plan 1",
                    price="10.00 USD",
                    currency="USD",
                    description="",
                    specs={},
                    available=True,
                )
            ],
            meta={
                "discovery_stop_reason": "queue_exhausted",
                "discovery_fetch_errors": 1,
            },
        )

        state_full, _ = _update_state_from_runs(
            previous_state,
            [run],
            dry_run=True,
            timeout_seconds=5.0,
            prune_missing_products=True,
        )

        self.assertIn("id-2", state_full["products"])


if __name__ == "__main__":
    unittest.main()
