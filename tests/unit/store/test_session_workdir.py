"""workdir/ subdir lives inside the session git repo (task C, minimal)."""
from __future__ import annotations

from pathlib import Path

from openprogram.state.store.session.session_log import SessionLog


def test_workdir_materialized_on_init(tmp_path: Path):
    gs = SessionLog(tmp_path / "s1")
    gs._ensure_init()
    wd = gs.workdir_path
    assert wd.exists() and wd.is_dir()
