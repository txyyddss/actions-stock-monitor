from __future__ import annotations

import argparse
import os
from dataclasses import asdict
from pathlib import Path

from .dashboard import render_dashboard_html
from .monitor import run_monitor
from .state import load_state, save_state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="actions-stock-monitor")
    parser.add_argument("--state", default="data/state.json")
    parser.add_argument("--output", default="docs/index.html")
    parser.add_argument(
        "--targets",
        default="",
        help="Comma-separated list of target base URLs. Defaults to built-in list.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not send Telegram notifications.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("TIMEOUT_SECONDS", "25")),
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=int(os.getenv("MAX_WORKERS", "8")),
    )
    args = parser.parse_args(argv)

    state_path = Path(args.state)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    previous_state = load_state(state_path)

    new_state, run_summary = run_monitor(
        previous_state=previous_state,
        targets=targets,
        timeout_seconds=args.timeout_seconds,
        max_workers=args.max_workers,
        dry_run=args.dry_run,
    )

    save_state(state_path, new_state)

    html = render_dashboard_html(new_state, run_summary=asdict(run_summary))
    output_path.write_text(html, encoding="utf-8")
    return 0

