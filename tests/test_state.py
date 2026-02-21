from __future__ import annotations

import json
from pathlib import Path

from actions_stock_monitor.state import SCHEMA_VERSION, save_state


def test_save_state_unlinks_existing_file_before_write(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text('{"stale": true}\n', encoding="utf-8")

    unlink_calls: list[tuple[Path, bool]] = []
    real_unlink = Path.unlink

    def _spy_unlink(self: Path, *args, **kwargs):
        unlink_calls.append((self, bool(kwargs.get("missing_ok"))))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _spy_unlink)

    save_state(
        state_path,
        {
            "products": {},
            "domains": {},
            "last_run": {"started_at": "2026-02-21T00:00:00+00:00", "finished_at": "2026-02-21T00:00:01+00:00"},
        },
    )

    assert any(path == state_path and missing_ok for path, missing_ok in unlink_calls)

    loaded = json.loads(state_path.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == SCHEMA_VERSION
    assert "stale" not in loaded
