"""The control plane settles Goals through hooks the conversation engine registers."""
from __future__ import annotations

import subprocess
import sys
import textwrap

from openprogram.runtime.execution import hooks

GOAL = "openprogram.programs.tools.planning.goal"


def test_goal_hooks_register_with_conversation_before_the_goal_tool_loads():
    script = textwrap.dedent(f"""
        import sys
        from openprogram.runtime.execution import hooks

        assert hooks.registered("session_goals") is None
        try:
            hooks.session_goals()
        except hooks.EngineNotRegistered as exc:
            assert exc.hook == "session_goals"
        else:
            raise AssertionError("session_goals must be required")
        hooks.settle_terminal(None, None)  # optional: nothing to settle

        import openprogram.runtime.conversation  # noqa: F401
        assert hooks.registered("terminal_settler") is not None
        assert hooks.registered("session_goals") is not None
        assert "{GOAL}" not in sys.modules, "the hooks must import the Goal tool lazily"
        print("ok")
    """)
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("ok")


def test_terminal_settlement_reaches_the_goal_tool_at_call_time(monkeypatch):
    import openprogram.runtime.conversation  # noqa: F401
    from openprogram.programs.tools.planning.goal import chat

    seen = []
    monkeypatch.setattr(chat, "after_terminal", lambda store, execution: seen.append((store, execution)))

    hooks.settle_terminal("store", "execution")

    assert seen == [("store", "execution")]


def test_session_goals_use_the_goal_owner_lock(tmp_path):
    import openprogram.runtime.conversation  # noqa: F401
    from openprogram.runtime.context.session_db import SessionDB

    store = SessionDB(tmp_path / "sessions")
    goals = hooks.session_goals()
    with goals.owner(store, "s") as first:
        assert first is True
        from openprogram.programs.tools.planning.goal.ownership import goal_owner

        with goal_owner(store, "s") as second:
            assert second is False
