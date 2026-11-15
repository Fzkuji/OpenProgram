"""The note a turn carries when the reply before it was stopped.

A fact, plus a warning only when a tool call was cut (Codex
``<turn_aborted>``), in front of the next user message (Grok Build), never
in the assistant's place (Hermes).
"""
from __future__ import annotations

import pytest

from openprogram.runtime.context import turn_context as tc


@pytest.mark.parametrize("reason, start", [
    ("cancel.user", "The user stopped your previous reply"),
    ("cancel.parent", "Your previous reply was stopped because the task that started it"),
    ("cancel.session", "Your previous reply was stopped when the whole conversation"),
    ("time_limit", "Your previous reply was stopped by its time limit"),
    ("worker_stopping", "Your previous reply was cut off by a service restart, not by the user"),
    ("error.worker_lost", "Your previous reply was cut off by a service restart"),
    ("interrupted", "Your previous reply was cut off by a service restart"),
    ("wait_declined", "Your previous reply was stopped before it finished."),
])
def test_each_reason_has_its_note(reason, start) -> None:
    note = tc.interruption_note({"reason_code": reason, "cut_tools": False})
    assert note.startswith(start)
    assert "partly run" not in note


def test_a_cut_tool_call_adds_that_it_may_have_partly_run() -> None:
    note = tc.interruption_note({"reason_code": "cancel.user", "cut_tools": True})
    assert note == ("The user stopped your previous reply before it finished; its last part is incomplete."
                    " A tool call that was stopped may have partly run.")


def test_no_interruption_no_note() -> None:
    assert tc.interruption_note(None) == ""
    assert tc.reply_interruption({"role": "assistant", "status": "done"}) is None
    assert tc.reply_interruption({"role": "user", "status": "cancelled"}) is None


def test_a_reply_stopped_before_it_recorded_why_reads_from_its_status() -> None:
    assert tc.reply_interruption({"role": "assistant", "status": "cancelled"}) == {"reason_code": "cancel.user"}
    assert tc.reply_interruption({"role": "assistant", "status": "interrupted"}) == {"reason_code": "interrupted"}
    recorded = {"reason_code": "cancel.parent", "phase": "tools", "cut_tools": True}
    assert tc.reply_interruption({"role": "assistant", "status": "cancelled", "interruption": recorded}) == recorded


def test_the_note_renders_after_the_date_and_before_a_rewind() -> None:
    items = tc.put([], tc.ATTACHMENT, "rewind", "REWIND")
    items = tc.put(items, tc.ATTACHMENT, "date", "DATE")
    items = tc.put(items, tc.ATTACHMENT, "interrupted", "STOPPED")
    items = tc.put(items, tc.SECTION, "environment", "ENV")
    assert [entry["id"] for entry in items] == ["date", "interrupted", "rewind", "environment"]
    assert tc.notes_text(items) == "STOPPED\n\nREWIND\n\nENV"
    stored = tc.node_metadata(items)
    assert tc.block(stored, tc.CONVERSATION) == "DATE\n\nSTOPPED\n\nREWIND\n\nENV"
