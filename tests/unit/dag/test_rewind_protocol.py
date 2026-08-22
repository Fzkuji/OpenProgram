"""Public WS/CLI rewind protocol requires plan before apply."""
from __future__ import annotations


class _Console:
    def __init__(self):
        self.lines: list[str] = []

    def print(self, value=""):
        self.lines.append(str(value))


def test_cli_rewind_previews_before_confirming(monkeypatch):
    from openprogram.agent import _rewind
    from openprogram_cli._impl.repl.handlers import _handle_rewind

    point = {
        "msg_id": "u2", "summary": "change two", "files_affected": ["a.py"],
    }
    plan = {
        "status": "ready", "turns_reverted": 2,
        "files": [{"path": "a.py"}], "idempotency_key": "request-key",
        "plan_hash": "sha256:plan",
    }
    applied: list[dict] = []
    monkeypatch.setattr(_rewind, "list_rewind_points", lambda _sid: [point])
    monkeypatch.setattr(
        _rewind, "plan_rewind", lambda _sid, _target: dict(plan),
    )
    monkeypatch.setattr(
        _rewind, "rewind_to",
        lambda _sid, _target, **kwargs: applied.append(kwargs) or {
            "status": "committed", "errors": [], "total_restored_paths": ["a.py"],
            "turns_reverted": 2,
        },
    )
    console = _Console()

    _handle_rewind(["1"], console, "session")
    assert applied == []
    assert any(
        "/rewind 1 confirm sha256:plan request-key" in line
        for line in console.lines
    )

    _handle_rewind(
        ["1", "confirm", "sha256:plan", "request-key"], console, "session",
    )
    assert applied == [{
        "idempotency_key": "request-key",
        "expected_plan_hash": "sha256:plan",
    }]


def test_cli_confirm_rejects_changed_plan(monkeypatch):
    from openprogram.agent import _rewind
    from openprogram_cli._impl.repl.handlers import _handle_rewind

    monkeypatch.setattr(_rewind, "list_rewind_points", lambda _sid: [{
        "msg_id": "u2", "summary": "change two", "files_affected": [],
    }])
    monkeypatch.setattr(_rewind, "plan_rewind", lambda *_args: {
        "status": "ready", "turns_reverted": 3, "files": [],
        "idempotency_key": "new-key", "plan_hash": "sha256:changed",
    })
    applied = []
    monkeypatch.setattr(
        _rewind, "rewind_to", lambda *_args, **kwargs: applied.append(kwargs),
    )
    console = _Console()

    _handle_rewind(
        ["1", "confirm", "sha256:old", "old-key"], console, "session",
    )

    assert applied == []
    assert any("stale_plan" in line for line in console.lines)
