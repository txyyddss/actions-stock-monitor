from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from actions_stock_monitor.http_client import HttpClient
from actions_stock_monitor.monitor import (
    _dedupe_keep_order,
    _default_entrypoint_pages,
    _discover_candidate_pages,
    _domain_extra_pages,
    _scrape_target,
)
from actions_stock_monitor.parsers.registry import get_parser_for_domain


def _now_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def _product_to_dict(p) -> dict[str, Any]:
    return {
        "id": p.id,
        "domain": p.domain,
        "url": p.url,
        "name": p.name,
        "price": p.price,
        "currency": p.currency,
        "available": p.available,
        "variant_of": p.variant_of,
        "location": p.location,
        "locations": p.locations,
        "location_links": p.location_links,
        "billing_cycles": p.billing_cycles,
        "cycle_prices": p.cycle_prices,
        "specs": p.specs,
        "description": p.description,
        "is_special": p.is_special,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_manual_baseline(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    template = {
        "note": "Fill products manually from origin HTML/API source.",
        "products": [],
    }
    _write_json(path, template)
    return template


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _build_diff_report(*, parsed_simple: list[dict[str, Any]], parsed_monitor: list[dict[str, Any]], baseline: dict[str, Any]) -> dict[str, Any]:
    baseline_products = baseline.get("products") or []
    baseline_keys = {
        f"{str(x.get('name') or '').strip().lower()}::{str(x.get('url') or '').strip().lower()}"
        for x in baseline_products
        if isinstance(x, dict)
    }
    simple_keys = {f"{str(x.get('name') or '').strip().lower()}::{str(x.get('url') or '').strip().lower()}" for x in parsed_simple}
    monitor_keys = {f"{str(x.get('name') or '').strip().lower()}::{str(x.get('url') or '').strip().lower()}" for x in parsed_monitor}
    return {
        "counts": {
            "baseline": len(baseline_products),
            "simple": len(parsed_simple),
            "monitor": len(parsed_monitor),
        },
        "missing_vs_baseline_simple": sorted(list(baseline_keys - simple_keys)),
        "missing_vs_baseline_monitor": sorted(list(baseline_keys - monitor_keys)),
        "new_vs_baseline_simple": sorted(list(simple_keys - baseline_keys)),
        "new_vs_baseline_monitor": sorted(list(monitor_keys - baseline_keys)),
    }


def _run_simple_stage(*, client: HttpClient, target: str, out_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fetch = client.fetch_text(target)
    raw_pages: list[dict[str, Any]] = []
    if not fetch.ok or not fetch.text:
        raw_pages.append(
            {
                "url": target,
                "ok": False,
                "status_code": fetch.status_code,
                "error": fetch.error,
                "text": fetch.text,
            }
        )
        _write_json(out_dir / "raw_pages.json", raw_pages)
        _write_json(out_dir / "parsed_simple.json", [])
        return raw_pages, []

    domain = _domain(fetch.url)
    parser = get_parser_for_domain(domain)
    pages = []
    pages.extend(_domain_extra_pages(domain))
    pages.extend(_discover_candidate_pages(fetch.text, base_url=fetch.url, domain=domain))
    pages.extend(_default_entrypoint_pages(fetch.url))
    pages = _dedupe_keep_order([fetch.url] + pages)

    parsed_simple: list[dict[str, Any]] = []
    for url in pages:
        page = client.fetch_text(url)
        raw_pages.append(
            {
                "url": url,
                "ok": page.ok,
                "status_code": page.status_code,
                "error": page.error,
                "text": page.text if page.text else "",
            }
        )
        if not page.ok or not page.text:
            continue
        try:
            products = parser.parse(page.text, base_url=page.url)
        except Exception:
            products = []
        parsed_simple.extend([_product_to_dict(p) for p in products])

    # Dedup on id for deterministic output.
    dedup_simple: dict[str, dict[str, Any]] = {}
    for p in parsed_simple:
        dedup_simple[str(p.get("id") or "")] = p

    _write_json(out_dir / "raw_pages.json", raw_pages)
    _write_json(out_dir / "parsed_simple.json", list(dedup_simple.values()))
    return raw_pages, list(dedup_simple.values())


def _run_monitor_stage(*, client: HttpClient, target: str, out_dir: Path) -> list[dict[str, Any]]:
    run = _scrape_target(client, target, allow_expansion=True)
    payload = {
        "domain": run.domain,
        "ok": run.ok,
        "error": run.error,
        "duration_ms": run.duration_ms,
        "products": [_product_to_dict(p) for p in run.products],
    }
    _write_json(out_dir / "parsed_monitor.json", payload)
    return payload["products"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Single-site deterministic crawler debugger")
    ap.add_argument("--target", required=True, help="Target base URL")
    ap.add_argument("--flaresolverr-url", default="", help="FlareSolverr endpoint, e.g. http://127.0.0.1:8191/")
    ap.add_argument("--timeout", type=float, default=25.0, help="HTTP timeout seconds")
    ap.add_argument("--stage", choices=["simple", "monitor"], required=True)
    ap.add_argument("--save-dir", default="data/debug", help="Base output directory")
    args = ap.parse_args()

    target = str(args.target).strip()
    if not target.startswith(("http://", "https://")):
        raise SystemExit("--target must be an absolute http(s) URL")

    dom = _domain(target)
    base_dir = Path(args.save_dir) / dom
    if args.stage == "monitor":
        existing = [p for p in base_dir.glob("*") if p.is_dir()]
        out_dir = sorted(existing)[-1] if existing else (base_dir / _now_tag())
    else:
        out_dir = base_dir / _now_tag()
    out_dir.mkdir(parents=True, exist_ok=True)

    client = HttpClient(timeout_seconds=float(args.timeout), flaresolverr_url=(args.flaresolverr_url or None))

    parsed_simple: list[dict[str, Any]] = []
    parsed_monitor: list[dict[str, Any]] = []
    if args.stage == "simple":
        _, parsed_simple = _run_simple_stage(client=client, target=target, out_dir=out_dir)
        _write_json(out_dir / "parsed_monitor.json", {"domain": dom, "ok": None, "error": None, "duration_ms": 0, "products": []})
    else:
        # Preserve simple-stage artifacts for side-by-side diffing.
        parsed_simple = _load_json_list(out_dir / "parsed_simple.json")
        parsed_monitor = _run_monitor_stage(client=client, target=target, out_dir=out_dir)

    baseline_path = out_dir / "manual_baseline.json"
    baseline = _load_manual_baseline(baseline_path)
    diff_report = _build_diff_report(parsed_simple=parsed_simple, parsed_monitor=parsed_monitor, baseline=baseline)
    _write_json(out_dir / "diff_report.json", diff_report)

    print(f"saved={out_dir}")
    print(f"stage={args.stage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
