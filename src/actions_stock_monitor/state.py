from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .timeutil import utc_now_iso


SCHEMA_VERSION = 1


def _empty_state() -> dict[str, Any]:
    now = utc_now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": now,
        "products": {},
        "domains": {},
        "last_run": {"started_at": now, "finished_at": now},
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    if data.get("schema_version") != SCHEMA_VERSION:
        migrated = _empty_state()
        migrated["products"] = data.get("products", {}) if isinstance(data.get("products"), dict) else {}
        migrated["domains"] = data.get("domains", {}) if isinstance(data.get("domains"), dict) else {}
        return migrated
    data.setdefault("products", {})
    data.setdefault("domains", {})
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    state = dict(state)
    state["schema_version"] = SCHEMA_VERSION
    state["updated_at"] = utc_now_iso()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

