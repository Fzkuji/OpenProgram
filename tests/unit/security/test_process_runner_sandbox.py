from __future__ import annotations

import queue


def test_spawn_payload_contains_explicit_sandbox_snapshot(monkeypatch):
    from openprogram.agent import process_runner

    captured = {}

    class FakeProcess:
        exitcode = 0

        def __init__(self, *, target, args, daemon):
            captured["target"] = target
            captured["args"] = args

        def start(self):
            pass

        def join(self):
            pass

        def is_alive(self):
            return False

    class FakeContext:
        Queue = queue.Queue

        def Process(self, **kwargs):
            return FakeProcess(**kwargs)

    monkeypatch.setattr(process_runner.mp, "get_context", lambda _kind: FakeContext())
    monkeypatch.setattr(
        process_runner,
        "_capture_sandbox_snapshot",
        lambda: {"enabled": True, "policy": {"network": False}},
    )

    process_runner.run_agentic_in_subprocess(
        tool_name="demo",
        kwargs={},
        session_id="s",
        anchor_msg_id="m",
        work_dir="/workspace",
    )

    assert captured["args"][-2] == {
        "enabled": True,
        "policy": {"network": False},
    }
    assert captured["args"][-1] is None


def test_spawn_payload_preserves_turn_render_range(monkeypatch):
    from openprogram.agent import process_runner

    captured = {}

    class FakeProcess:
        exitcode = 0

        def __init__(self, *, target, args, daemon):
            captured["args"] = args

        def start(self):
            pass

        def join(self):
            pass

        def is_alive(self):
            return False

    class FakeContext:
        Queue = queue.Queue

        def Process(self, **kwargs):
            return FakeProcess(**kwargs)

    monkeypatch.setattr(process_runner.mp, "get_context", lambda _kind: FakeContext())

    process_runner.run_agentic_in_subprocess(
        tool_name="demo",
        kwargs={},
        session_id="s",
        anchor_msg_id="m",
        render_range={"callers": 0, "subcalls": 2},
    )

    assert captured["args"][-4] == {"callers": 0, "subcalls": 2}
