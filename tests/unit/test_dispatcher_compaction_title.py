"""Auto-title + compaction signal + trigger_compaction.

Auto-title: dispatcher stamps a 50-char title from the user's first
message on the first non-empty turn, idempotently. User-set titles
(via /rename) win because we mark _titled=True after the first
auto-stamp.

Compaction signal: after each turn, dispatcher fires a
"compaction_recommended" envelope when the active branch crosses
70% of the model's context window. Doesn't auto-compact — the UI is
expected to surface a /compact button.

trigger_compaction: explicit user-driven compaction. Persists a
synthesized compactionSummary message + re-parented kept tail,
moves head to the new leaf. Old chain stays in SessionDB but is
off the active branch.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import patch

import pytest

from openprogram.agent import dispatcher as D
from openprogram.agent.session_db import SessionDB
from openprogram.providers.types import (
    AssistantMessage,
    AssistantMessageEvent,
    EventDone,
    EventStart,
    EventTextDelta,
    EventTextEnd,
    EventTextStart,
    Model,
    TextContent,
    Usage,
)


def _stub_model(max_tokens: int = 200_000,
                context_window: int | None = None) -> Model:
    return Model(id="stub", name="stub", api="completion",
                 provider="openai", base_url="https://x",
                 max_tokens=max_tokens,
                 context_window=context_window or max_tokens)


def _build_partial(t: str = "") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=t)] if t else [],
        api="completion", provider="openai", model="stub",
        timestamp=int(time.time() * 1000),
    )


def _build_final(t: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=t)],
        api="completion", provider="openai", model="stub",
        usage=Usage(input=1, output=1), stop_reason="stop",
        timestamp=int(time.time() * 1000),
    )


def make_text_stream(text: str, *, input_tokens: int = 1):
    """Fake stream. ``input_tokens`` controls the synthetic usage on
    the final message — set high to trigger budget-based events that
    care about provider-reported input size."""
    def _final(t: str) -> AssistantMessage:
        return AssistantMessage(
            content=[TextContent(text=t)],
            api="completion", provider="openai", model="stub",
            usage=Usage(input=input_tokens, output=1), stop_reason="stop",
            timestamp=int(time.time() * 1000),
        )
    async def _fn(model, ctx, opts) -> AsyncGenerator[AssistantMessageEvent, None]:
        yield EventStart(partial=_build_partial(""))
        yield EventTextStart(content_index=0, partial=_build_partial(""))
        yield EventTextDelta(content_index=0, delta=text, partial=_build_partial(text))
        yield EventTextEnd(content_index=0, content=text, partial=_build_partial(text))
        yield EventDone(reason="stop", message=_final(text))
    return _fn


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    return db


@pytest.fixture(autouse=True)
def stubs(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(D, "_resolve_model",
                        lambda profile, override=None: _stub_model())
    monkeypatch.setattr(D, "_load_agent_profile",
                        lambda agent_id: {"id": agent_id,
                                            "system_prompt": "",
                                            "tools": []})


# ---------------------------------------------------------------------------
# Auto-title
# ---------------------------------------------------------------------------

def test_auto_title_stamps_from_first_user_message(tmp_db: SessionDB) -> None:
    fake = make_text_stream("ack")
    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, **_):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=fake)

    with patch.object(D, "_run_loop_blocking", _w):
        D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="What is the weather?",
                          agent_id="main", source="tui"),
        )

    sess = tmp_db.get_session("c1")
    assert sess["title"] == "What is the weather?"
    # Titled flag set so a future turn doesn't overwrite it
    assert sess["extra_meta"].get("_titled") is True


def test_auto_title_truncates_long_input(tmp_db: SessionDB) -> None:
    fake = make_text_stream("ok")
    orig = D._run_loop_blocking
    long_msg = "x" * 200

    def _w(*, req, history, on_event, cancel_event, **_):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=fake)

    with patch.object(D, "_run_loop_blocking", _w):
        D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text=long_msg,
                          agent_id="main", source="tui"),
        )

    sess = tmp_db.get_session("c1")
    assert sess["title"].endswith("…")
    assert len(sess["title"]) == 51  # 50 chars + ellipsis


def test_auto_title_is_idempotent_across_turns(tmp_db: SessionDB) -> None:
    """User explicitly renames after turn 1 → turn 2's user_text must
    NOT overwrite the chosen title. Done by marking _titled=True on
    the first auto-stamp; a /rename action would set _titled=True too."""
    fake = make_text_stream("ok")
    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, **_):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=fake)

    with patch.object(D, "_run_loop_blocking", _w):
        D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="first",
                          agent_id="main", source="tui"),
        )

    # Simulate user rename
    tmp_db.update_session("c1", title="Custom Title", _titled=True)

    with patch.object(D, "_run_loop_blocking", _w):
        D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="completely different topic",
                          agent_id="main", source="tui"),
        )

    sess = tmp_db.get_session("c1")
    assert sess["title"] == "Custom Title"


def test_auto_title_skips_empty_input(tmp_db: SessionDB) -> None:
    """A turn with empty user_text (e.g. tool-only follow-up) shouldn't
    title the session as empty string."""
    fake = make_text_stream("ok")
    orig = D._run_loop_blocking

    # Pre-create with no title to verify dispatcher doesn't auto-stamp
    tmp_db.create_session("c1", "main")

    def _w(*, req, history, on_event, cancel_event, **_):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=fake)

    with patch.object(D, "_run_loop_blocking", _w):
        D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="   \n  ",  # whitespace only
                          agent_id="main", source="tui"),
        )

    sess = tmp_db.get_session("c1")
    # Title stays whatever the original was (None or default)
    assert sess["extra_meta"].get("_titled") is not True


# ---------------------------------------------------------------------------
# Compaction recommendation signal
# ---------------------------------------------------------------------------

def test_compaction_recommended_fires_when_branch_large(
    tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a tiny context_window stub + a few seeded messages, the
    signal should fire after the next turn."""
    monkeypatch.setattr(D, "_resolve_model",
                        lambda profile, override=None: _stub_model(max_tokens=500))

    # Seed 50 large messages so the active branch easily crosses
    # the 70% threshold of a 500-token window.
    tmp_db.create_session("c1", "main")
    last = None
    for i in range(50):
        mid = f"m{i}"
        tmp_db.append_message("c1", {
            "id": mid, "role": "user" if i % 2 == 0 else "assistant",
            "content": "x" * 200,   # ~50 tokens each
            "timestamp": float(i), "parent_id": last,
        })
        last = mid
    tmp_db.set_head("c1", last)

    captured: list[dict] = []
    # Report 400 input tokens on the assistant turn — crosses 70% of
    # the 500-token window stub so engine.after_turn emits the
    # recommendation envelope.
    fake = make_text_stream("ok", input_tokens=400)
    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, **_):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=fake)

    with patch.object(D, "_run_loop_blocking", _w):
        D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="more",
                          agent_id="main", source="tui"),
            on_event=captured.append,
        )

    recs = [e for e in captured
            if e.get("type") == "chat_response"
            and e["data"].get("type") == "compaction_recommended"]
    assert recs, "expected at least one compaction_recommended envelope"
    assert recs[0]["data"]["session_id"] == "c1"
    # New schema (from engine.after_turn): includes budget_pct + window.
    # The legacy ``branch_messages`` field is gone — engine now reasons
    # in tokens, not message counts.
    assert recs[0]["data"]["budget_pct"] >= 0.70
    assert recs[0]["data"]["context_window"] == 500


def test_compaction_signal_silent_under_threshold(tmp_db: SessionDB) -> None:
    """Short conversation → no signal."""
    captured: list[dict] = []
    fake = make_text_stream("ok")
    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, **_):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=fake)

    with patch.object(D, "_run_loop_blocking", _w):
        D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="hi",
                          agent_id="main", source="tui"),
            on_event=captured.append,
        )

    recs = [e for e in captured
            if e.get("type") == "chat_response"
            and e["data"].get("type") == "compaction_recommended"]
    assert recs == []


# ---------------------------------------------------------------------------
# trigger_compaction
# ---------------------------------------------------------------------------

def test_trigger_compaction_inserts_summary_and_moves_head(
    tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User clicks /compact → dispatcher writes a synthesized summary
    row, re-parents the kept tail, and sets head to the new leaf."""
    # Pre-seed a real conversation
    tmp_db.create_session("c1", "main")
    last = None
    for i in range(20):
        mid = f"m{i}"
        tmp_db.append_message("c1", {
            "id": mid, "role": "user" if i % 2 == 0 else "assistant",
            "content": f"turn {i} content " * 20,
            "timestamp": float(i), "parent_id": last,
        })
        last = mid
    tmp_db.set_head("c1", last)
    pre_count = len(tmp_db.get_messages("c1"))

    # Stub the LLM summary call to avoid hitting a real provider
    async def _fake_gen(*args, **kwargs):
        return "compressed summary of earlier discussion"
    monkeypatch.setattr(
        "openprogram.context.summarize.Summarizer._llm_summary",
        _fake_gen,
    )

    captured: list[dict] = []
    # keep_recent_tokens=200 → most of the seeded turns get summarized,
    # leaving a small kept tail. Without this, the default 20k-token
    # keep window swallows everything and there's nothing to summarize.
    res = D.trigger_compaction("c1", agent_id="main",
                                 on_event=captured.append,
                                 keep_recent_tokens=200)
    assert res["summary"]
    assert res["summary_id"]

    # SessionDB grew by at least 1 (summary) + however many recent
    # messages were re-parented.
    post_count = len(tmp_db.get_messages("c1"))
    assert post_count > pre_count

    # New head is the tail of the re-parented chain (or the summary
    # itself if no kept tail). It must NOT be one of the original
    # m0..m19 ids.
    sess = tmp_db.get_session("c1")
    assert sess["head_id"] not in {f"m{i}" for i in range(20)}

    # Active branch starts with the summary row.
    branch = tmp_db.get_branch("c1")
    assert branch
    first = branch[0]
    assert first["source"] == "compaction"
    assert "summary" in first["content"].lower()

    # Old messages still findable via get_messages (append-only)
    all_msgs = tmp_db.get_messages("c1")
    old_ids = {m["id"] for m in all_msgs} & {f"m{i}" for i in range(20)}
    assert old_ids == {f"m{i}" for i in range(20)}, "old messages must stay in DB"

    # And one compaction_finished envelope was emitted
    done = [e for e in captured
            if e["data"].get("type") == "compaction_finished"]
    assert len(done) == 1
