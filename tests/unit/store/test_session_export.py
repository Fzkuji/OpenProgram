"""Session export: DAG walk, redaction, Markdown and HTML rendering."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from openprogram.store import SessionStore
from openprogram.store.session.export import (
    collect_turns,
    export_session,
    render_html,
    render_markdown,
)


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions-git")


def _append(store, sess, mid, *, role="user", parent=None, content="x", **extra):
    msg = {
        "id": mid, "role": role, "content": content,
        "predecessor": parent, "timestamp": time.time(),
    }
    msg.update(extra)
    store.append_message(sess, msg)


@pytest.fixture
def session(store: SessionStore) -> SessionStore:
    """A two-turn session whose assistant turn made one tool call."""
    store.create_session("s1", agent_id="a", title="Demo session")
    _append(store, "s1", "n1", role="user", content="hello there")
    _append(store, "s1", "n2", role="assistant", parent="n1", content="hi back")
    _append(
        store, "s1", "t1", role="tool", parent="n2", caller="n2",
        content="tool output body", function="read_file",
        extra=json.dumps({"tool_use": {"name": "read_file",
                                       "arguments": {"path": "/tmp/x"}}}),
    )
    return store


# collect_turns — the DAG walk


def test_collect_turns_walks_branch_and_attaches_calls(session):
    turns = collect_turns("s1", store=session)
    assert [t["index"] for t in turns] == [1, 2]
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["content"] == "hello there"
    # The tool node hangs off its caller turn, not off the branch chain.
    assert turns[0]["calls"] == []
    assert len(turns[1]["calls"]) == 1
    call = turns[1]["calls"][0]
    assert call["name"] == "read_file"
    assert call["status"] == "ok"
    assert "/tmp/x" in call["args"]
    assert call["result"] == "tool output body"


def test_collect_turns_can_omit_tool_calls(session):
    turns = collect_turns("s1", include_tool_calls=False, store=session)
    assert all(t["calls"] == [] for t in turns)


def test_collect_turns_missing_session_is_empty(store):
    assert collect_turns("nope", store=store) == []


def test_failed_call_reports_failed_status(store):
    store.create_session("s1", agent_id="a")
    _append(store, "s1", "n1", role="assistant", content="try")
    _append(store, "s1", "t1", role="tool", parent="n1", caller="n1",
            content="boom", function="bash", is_error=True)
    call = collect_turns("s1", store=store)[0]["calls"][0]
    assert call["status"] == "failed"


def test_long_tool_result_is_truncated(store):
    store.create_session("s1", agent_id="a")
    _append(store, "s1", "n1", role="assistant", content="go")
    _append(store, "s1", "t1", role="tool", parent="n1", caller="n1",
            content="z" * 50_000, function="read_file")
    result = collect_turns("s1", store=store)[0]["calls"][0]["result"]
    assert len(result) < 50_000
    assert "truncated" in result


# Redaction — reuses providers.recording.remove_secret_values


def test_secrets_are_removed_from_content_and_calls(store):
    store.create_session("s1", agent_id="a")
    _append(store, "s1", "n1", role="user",
            content="my key is sk-abcdef0123456789 keep it safe")
    _append(store, "s1", "t1", role="tool", parent="n1", caller="n1",
            content="Authorization: Bearer tok_secret_value_here",
            function="fetch")
    turns = collect_turns("s1", store=store)
    assert "sk-abcdef0123456789" not in turns[0]["content"]
    assert "[secret removed]" in turns[0]["content"]
    assert "tok_secret_value_here" not in turns[0]["calls"][0]["result"]


def test_secrets_do_not_survive_into_either_format(store):
    store.create_session("s1", agent_id="a")
    _append(store, "s1", "n1", role="user", content="token sk-deadbeef12345678")
    for fmt in ("md", "html"):
        assert "sk-deadbeef12345678" not in export_session("s1", fmt, store=store)


# Markdown rendering


def test_markdown_has_headers_content_and_call(session):
    out = export_session("s1", "md", store=session)
    assert out.startswith("# Demo session")
    assert "`s1`" in out
    assert "## [1] user" in out
    assert "## [2] assistant" in out
    assert "hello there" in out
    assert "**Tool call: `read_file` → ok**" in out
    assert "tool output body" in out


def test_markdown_of_empty_session_still_renders_header(store):
    store.create_session("s1", agent_id="a", title="Empty")
    out = export_session("s1", "md", store=store)
    assert "# Empty" in out
    assert "- Turns: 0" in out


# HTML rendering


def test_html_is_self_contained_and_theme_aware(session):
    out = export_session("s1", "html", store=session)
    assert out.startswith("<!DOCTYPE html>")
    assert out.rstrip().endswith("</html>")
    assert "<style>" in out
    assert "prefers-color-scheme" in out
    # Self-contained: no external fetches, no scripts.
    assert "<script" not in out
    assert "http://" not in out and "https://" not in out
    assert "hello there" in out
    assert "read_file" in out


def test_html_escapes_markup_in_content(store):
    store.create_session("s1", agent_id="a")
    _append(store, "s1", "n1", role="user",
            content="<script>alert(1)</script> & <b>bold</b>")
    out = export_session("s1", "html", store=store)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "&amp;" in out


# export_session dispatch


def test_unknown_format_raises(session):
    with pytest.raises(ValueError, match="unknown export format"):
        export_session("s1", "pdf", store=session)


def test_renderers_share_one_turn_collection(session):
    """Markdown and HTML must describe the same session, not two walks."""
    turns = collect_turns("s1", store=session)
    md = render_markdown("s1", turns, "T")
    doc = render_html("s1", turns, "T")
    for turn in turns:
        assert turn["content"] in md
        for call in turn["calls"]:
            assert call["name"] in md
            assert call["name"] in doc
