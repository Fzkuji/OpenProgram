"""open_work: one list per conversation, in its system prompt on every turn."""
from __future__ import annotations

import importlib

import pytest

from openprogram.runtime.conversation import open_work as store

tool = importlib.import_module("openprogram.programs.tools.planning.open_work.open_work")


@pytest.fixture
def session_dirs(tmp_path, monkeypatch):
    class Db:
        def _session_dir(self, session_id):
            return tmp_path / session_id

        def state_file(self, session_id, name):
            return tmp_path / session_id / name

    for sid in ("s1", "s2"):
        (tmp_path / sid).mkdir()
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: Db())
    monkeypatch.setattr(store, "_publish", lambda *a: None)
    return tmp_path


def _in(monkeypatch, session_id: str) -> None:
    monkeypatch.setattr(tool, "_current", lambda: session_id)


def test_each_conversation_keeps_its_own_list(session_dirs, monkeypatch) -> None:
    _in(monkeypatch, "s1")
    assert tool._open_work_impl("add", text="Wait for  Dana's\nreply", note="asked Mon") \
        == "Open work #1 added: Wait for Dana's reply"
    assert "#1 Wait for Dana's reply — asked Mon" in tool._open_work_impl("list")
    assert (session_dirs / "s1" / "open_work.json").exists()
    _in(monkeypatch, "s2")
    assert tool._open_work_impl("list") == "(no open work)"


def test_done_items_leave_the_prompt(session_dirs, monkeypatch) -> None:
    _in(monkeypatch, "s1")
    tool._open_work_impl("add", text="Check the PR")
    tool._open_work_impl("add", text="Send the draft")
    assert "#2 Send the draft" in store.prompt_block("s1")
    assert tool._open_work_impl("done", id="1") == "Open work #1 done: Check the PR"
    block = store.prompt_block("s1")
    assert "Check the PR" not in block and "#2 Send the draft" in block
    tool._open_work_impl("done", id="2")
    assert store.prompt_block("s1") == ""
    assert store.prompt_block(None) == ""


def test_the_prompt_component_reads_the_current_conversation(session_dirs, monkeypatch) -> None:
    from openprogram.runtime.execution import run_control as execution_run_control
    from openprogram.runtime.conversation import context_registration as components

    store.add("s2", "Follow up on the invoice")
    token = execution_run_control.set_current_session_id("s2")
    try:
        assert "Follow up on the invoice" in components._build_open_work(None)
    finally:
        execution_run_control.reset_current_session_id(token)
    assert components._build_open_work(None) == ""


def test_errors_are_returned_not_raised(session_dirs, monkeypatch) -> None:
    _in(monkeypatch, "s1")
    assert tool._open_work_impl("done") == "[open_work error] done needs id"
    assert tool._open_work_impl("remove", id="9") == "[open_work error] no open work #9"
    assert tool._open_work_impl("add", text="  ") == "[open_work error] text is required"
    assert tool._open_work_impl("fly").startswith("[open_work error] action must be")
    _in(monkeypatch, "gone")
    assert tool._open_work_impl("add", text="x") == "[open_work error] conversation 'gone' not found"
    _in(monkeypatch, "")
    assert tool._open_work_impl("list") == "[open_work error] no conversation in this context"


def test_old_done_items_are_dropped(session_dirs, monkeypatch) -> None:
    monkeypatch.setattr(store, "DONE_KEPT", 2)
    for n in range(4):
        item = store.add("s1", f"item {n}")
        store.update("s1", item["id"], status="done")
    store.add("s1", "still open")
    texts = [i["text"] for i in store.load("s1")]
    assert texts == ["item 2", "item 3", "still open"]
