"""Cross-session history operations share one profile mutation lock."""
from __future__ import annotations

import threading
import time

from openprogram.store.snapshot.checkpoint import transactions


def test_overlapping_history_operations_serialize(tmp_path, monkeypatch):
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")
    path = str(tmp_path / "workspace" / "a" / "x.py")
    other = str(tmp_path / "workspace" / "b" / "y.py")
    waiting = threading.Event()
    entered = threading.Event()

    def contender():
        waiting.set()
        with transactions._workspace_lock([path]):
            entered.set()

    with transactions._workspace_lock([path, other]):
        thread = threading.Thread(target=contender)
        thread.start()
        assert waiting.wait(1)
        time.sleep(0.05)
        assert not entered.is_set()
    thread.join(timeout=1)
    assert entered.is_set()
