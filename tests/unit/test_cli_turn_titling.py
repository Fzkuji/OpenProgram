"""CLI chat turn aligns with the unified two-phase session naming.

The CLI runs each turn as a bare ``rt.exec`` and persists via
``webui.persistence`` (a facade over ``default_db()``). Previously it
stamped ``title=message[:50]`` plus a permanent ``_titled=True`` lock,
which opted CLI sessions out of LLM renaming forever.

After the fix the CLI:
  * creates the session with an empty title (no ``_titled`` lock), and
  * calls ``_maybe_auto_title`` so phase 1 stamps a truncated
    placeholder and phase 2 (background, LLM) can rename later — the
    same hook the dispatcher's ``finalize_turn`` uses.

These tests mock ``build_default_llm`` to None so the phase-2 daemon
thread no-ops; we assert phase-1 behavior synchronously.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from openprogram._cli_chat.turn import _run_turn_with_history
from openprogram.agent.session_db import SessionDB


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    return db


@pytest.fixture(autouse=True)
def _stub_runtime_and_llm(monkeypatch: pytest.MonkeyPatch):
    # rt.exec returns a fixed reply; no real provider call.
    fake_rt = SimpleNamespace(exec=lambda *, content: "ack", on_stream=None)
    monkeypatch.setattr(
        "openprogram.agent.management.runtime_registry.get_runtime_for",
        lambda agent: fake_rt,
    )
    monkeypatch.setattr(
        "openprogram.context.system_prompt.build_system_prompt",
        lambda agent: "",
    )
    # Phase-2 LLM unavailable → background thread no-ops, leaving the
    # phase-1 truncated title in place for a deterministic assertion.
    monkeypatch.setattr(
        "openprogram.memory.llm_bridge.build_default_llm",
        lambda: None,
    )


def _agent():
    return SimpleNamespace(id="main")


def test_cli_first_turn_stamps_phase1_title_no_lock(tmp_db: SessionDB) -> None:
    _run_turn_with_history(_agent(), "s1", "What is the weather today?")

    sess = tmp_db.get_session("s1")
    assert sess is not None
    # Phase 1: truncated placeholder from the first user message.
    assert sess["title"] == "What is the weather today?"
    # Phase-2 ownership flag set; legacy lock must NOT be set so the
    # session stays eligible for LLM renaming.
    assert sess["extra_meta"].get("_auto_titled") is True
    assert sess["extra_meta"].get("_titled") is not True


def test_cli_first_turn_truncates_long_message(tmp_db: SessionDB) -> None:
    _run_turn_with_history(_agent(), "s1", "x" * 200)

    sess = tmp_db.get_session("s1")
    assert sess["title"].endswith("…")
    assert len(sess["title"]) == 51  # 50 chars + ellipsis


def test_cli_does_not_relock_existing_session(tmp_db: SessionDB) -> None:
    # User renamed and locked the session out-of-band.
    tmp_db.create_session("s1", "main")
    tmp_db.update_session("s1", title="Custom Title", _user_titled=True)

    _run_turn_with_history(_agent(), "s1", "completely different topic")

    sess = tmp_db.get_session("s1")
    assert sess["title"] == "Custom Title"
