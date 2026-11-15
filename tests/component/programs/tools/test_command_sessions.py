"""bash yields to a background session; write_stdin polls, types into and interrupts it."""
from __future__ import annotations

import asyncio
import importlib
import os
import re

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX shell commands")


@pytest.fixture
def tools(monkeypatch, tmp_path):
    import openprogram.host.processes as processes
    from openprogram.host import sandbox
    from openprogram.host.processes import ProcessStore

    session = importlib.import_module("openprogram.programs.tools.files.bash.session")
    store_path = tmp_path / "processes.db"
    monkeypatch.setattr(processes, "current_owner", lambda: ("conversation", None, None))
    monkeypatch.setattr(session, "ProcessStore", lambda: ProcessStore(store_path))
    monkeypatch.setattr(session, "scoped_records", lambda store, sid: store.list(sid))
    monkeypatch.setattr(sandbox, "resolve_policy", lambda *a, **k: None)
    bash = importlib.import_module("openprogram.programs.tools.files.bash.bash").bash
    write_stdin = importlib.import_module("openprogram.programs.tools.files.write_stdin").write_stdin

    def call(tool, **args):
        result = asyncio.run(tool.execute("call", args, None, None))
        text = "\n".join(block.text for block in result.content if hasattr(block, "text"))
        return result, text

    yield session, call, bash, write_stdin, ProcessStore(store_path)
    store = ProcessStore(store_path)
    for record in store.list():
        if record["status"] in {"starting", "running", "stopping"}:
            processes.control(store, record["id"], "stop")


def _session_id(text: str) -> str:
    return re.search(r"Process running with session ID (\w+)", text).group(1)


def test_a_quick_command_returns_its_exit_and_output(tools):
    _, call, bash, _, _ = tools
    result, text = call(bash, command="echo hi")
    assert not result.is_error, text
    assert "Process exited with code 0" in text
    assert text.rstrip().endswith("hi")
    assert result.details["running"] is False


def test_a_failing_command_is_an_error(tools):
    _, call, bash, _, _ = tools
    result, text = call(bash, command="echo bad >&2; exit 3")
    assert result.is_error
    assert "Process exited with code 3" in text and "bad" in text


def test_a_long_command_keeps_running_and_write_stdin_waits_for_its_exit(tools):
    _, call, bash, write_stdin, store = tools
    result, text = call(bash, command="echo started; sleep 1; echo done", yield_time_ms=300)
    assert not result.is_error
    session_id = _session_id(text)
    assert "started" in text
    assert store.get(session_id)["background"] is True
    result, text = call(write_stdin, session_id=session_id, yield_time_ms=10_000)
    assert "Process exited with code 0" in text
    assert "done" in text and "started" not in text
    # The agent saw the exit, so no notice turn is due.
    assert store.get(session_id)["reported"] is True
    assert store.unreported_exits() == []


def test_without_a_terminal_stdin_is_closed_but_ctrl_c_interrupts(tools):
    _, call, bash, write_stdin, _ = tools
    _, text = call(bash, command="sleep 30", yield_time_ms=250)
    session_id = _session_id(text)
    result, text = call(write_stdin, session_id=session_id, chars="hello\n")
    assert result.is_error and "stdin is closed" in text
    result, text = call(write_stdin, session_id=session_id, chars="\x03", yield_time_ms=5000)
    assert "Process exited with code 130" in text


def test_a_terminal_session_takes_typed_input(tools):
    _, call, bash, write_stdin, _ = tools
    _, text = call(bash, command="read line; echo got:$line", tty=True, yield_time_ms=500)
    session_id = _session_id(text)
    result, text = call(write_stdin, session_id=session_id, chars="abc\n", yield_time_ms=5000)
    assert "Process exited with code 0" in text, text
    assert "got:abc" in text


def test_a_session_of_another_conversation_is_not_reachable(tools, monkeypatch):
    import openprogram.host.processes as processes
    _, call, bash, write_stdin, _ = tools
    _, text = call(bash, command="sleep 30", yield_time_ms=250)
    session_id = _session_id(text)
    monkeypatch.setattr(processes, "current_owner", lambda: ("other", None, None))
    result, text = call(write_stdin, session_id=session_id)
    assert result.is_error and "no command session" in text


def test_the_least_recently_used_session_stops_at_the_cap(tools, monkeypatch):
    session, call, bash, _, store = tools
    monkeypatch.setattr(session, "MAX_SESSIONS", 3)
    monkeypatch.setattr(session, "PROTECTED_RECENT", 1)
    ids = [_session_id(call(bash, command="sleep 30", yield_time_ms=250)[1]) for _ in range(3)]
    _, text = call(bash, command="echo fourth")
    assert f"Stopped session {ids[0]}" in text
    assert store.get(ids[0])["status"] == "stopped"
    assert store.get(ids[0])["reported"] is True
    assert all(store.get(item)["status"] == "running" for item in ids[1:])


def test_head_tail_buffer_keeps_both_ends():
    from openprogram.programs.tools.files.bash.session import HeadTailBuffer

    buffer = HeadTailBuffer(10)
    buffer.push(b"abcde")
    buffer.push(b"0123456789")
    assert buffer.text() == "abcde\n... 5 bytes omitted ...\n56789"


def test_yield_times_are_clamped_as_in_codex():
    from openprogram.programs.tools.files.bash import session

    assert session.clamp_exec_yield(None) == 30_000
    assert session.clamp_exec_yield(10) == 250
    assert session.clamp_exec_yield(99_000) == 30_000
    assert session.clamp_write_yield(None, "x") == 250
    assert session.clamp_write_yield(None, "") == 5_000
    assert session.clamp_write_yield(999_999, "") == 300_000
