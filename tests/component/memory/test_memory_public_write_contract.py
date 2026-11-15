"""Registered memory writes reject discarded prose without committing a batch."""
from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

NOTE = (
    "# Note\n\nA fact worth keeping.[^e-1f4c7a2b90] ^abc12345\n\n"
    "[^e-1f4c7a2b90]: Time: `2026-01-01`; Sources: [D1:1](/s/s1#d1-1)\n"
)


def _invoke(name, args):
    from openprogram.programs import get_agent_tool

    result = asyncio.run(get_agent_tool(name).execute("owned-write", args, None, None))
    text = "\n".join(block.text for block in result.content)
    if name == "memory_get":
        assert not result.is_error, result.content
        return text
    payload = json.loads(text)
    # A rejected call reaches the agent loop as a failed tool result.
    assert result.is_error is (payload.get("ok") is False), result.content
    return payload


def _snapshot(root):
    from openprogram.state.memory.management.transaction import workspace_revision

    return {
        "bytes": {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(root).parts
        },
        "revision": workspace_revision(root),
        "git_head": subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True,
        ).strip(),
    }


@pytest.fixture
def owned_memory(tmp_path, monkeypatch):
    import openprogram.state.paths as paths
    from openprogram.runtime.permissions import authority
    from openprogram.runtime.execution.run_control import binding as run_control_binding
    from openprogram.runtime.context import session_db
    from openprogram.state import store as chat_store
    from openprogram.state.memory import store
    from openprogram.programs.tools.knowledge.memory import memory as tools

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    authority._reset_owner_cache_for_tests()
    root = store.ensure()
    (root / "topics/note.md").write_text(NOTE)
    store._ensure_git_history(root)
    db = session_db.SessionDB(tmp_path / "owned-sessions")
    db.create_session("owned", "main")
    db.append_message("owned", {
        "id": "owned-user", "role": "user", "content": "Edit owned memory",
        "timestamp": 0, **authority.local_owner_authority(),
    })
    monkeypatch.setattr(session_db, "default_db", lambda: db)
    monkeypatch.setattr(tools, "_root", lambda: root)
    session_token = run_control_binding._current_session_id.set("owned")
    turn_token = chat_store._current_turn_id.set("owned-user")
    try:
        yield root
    finally:
        chat_store._current_turn_id.reset(turn_token)
        run_control_binding._current_session_id.reset(session_token)
        db.close()
        authority._reset_owner_cache_for_tests()


def _create(topic, content, **record):
    return {
        "op": "create_record", "content": content, "time": "2026-10-03",
        "destination": {"topic_path": topic, "headings": [], "position": "end"},
        **record,
    }


def test_a_chat_edit_cites_the_message_that_asked_for_it(owned_memory):
    root = owned_memory
    result = _invoke("memory_update", {
        "memory_changes": [_create("topics/asked.md", "The owner asked for this.")],
    })
    assert result["ok"] is True, result
    assert "[chat](/s/owned#owned-user)" in (root / "topics/asked.md").read_text()


def test_a_citation_of_a_message_no_chat_holds_commits_nothing(owned_memory):
    root = owned_memory
    before = _snapshot(root)
    result = _invoke("memory_update", {
        "memory_changes": [_create(
            "topics/invented.md", "Invented.",
            sources=[{"source": "/s/owned#nobody-said-this", "label": "made up"}],
        )],
    })
    assert result["ok"] is False
    assert "missing source reference" in result["error"]["message"]
    assert _snapshot(root) == before
    assert "worth keeping" in _invoke("memory_get", {"path": "topics/note.md"})


def test_registered_invalid_batch_does_not_install_a_valid_record(owned_memory):
    root = owned_memory
    before = _snapshot(root)
    result = _invoke("memory_update", {
        "memory_changes": [
            _create("topics/new.md", "Supported new fact."),
            {"op": "update_record", "memory_id": "missing1", "content": "No such record.",
             "time": "2026-10-03"},
        ],
    })
    assert result.get("ok") is False, result
    assert _snapshot(root) == before


def test_memory_get_reads_the_chat_a_footnote_cites(owned_memory):
    text = _invoke("memory_get", {"path": "/s/owned#owned-user"})
    assert "Edit owned memory" in text
    assert "**→**" in text


@pytest.mark.parametrize("callback_fails", [False, True])
def test_staged_edit_trusted_callback_runs_before_install(owned_memory, callback_fails):
    from openprogram.state.memory.management.transaction import staged_edit

    root = owned_memory
    before = _snapshot(root)
    calls = []

    def before_install():
        calls.append(_snapshot(root))
        assert not (root / ".scriptorium-block-backup").exists()
        if callback_fails:
            raise RuntimeError("owned callback failure")

    ok, _message = staged_edit(
        root, lambda stage: (stage / "topics/note.md").write_text(NOTE.replace("keeping", "updating")),
        before_install=before_install,
    )
    assert calls == [before]
    assert ok is not callback_fails
    if callback_fails:
        assert _snapshot(root) == before
    else:
        assert "worth updating" in (root / "topics/note.md").read_text()


def test_validation_rejection_does_not_call_trusted_callback(owned_memory):
    from openprogram.state.memory.management.transaction import staged_edit

    root = owned_memory
    before = _snapshot(root)
    calls = []
    # A person's uncited paragraph is kept, but a hand edit may not drop a
    # committed block ID.
    ok, message = staged_edit(
        root, lambda stage: (stage / "topics/note.md").unlink(),
        before_install=lambda: calls.append("must not run"),
    )
    assert not ok, message
    assert calls == []
    assert _snapshot(root) == before


def test_a_write_leaves_untouched_topics_unmodified(owned_memory):
    """Only the topic a write changes gets a new modification time."""
    import os

    from openprogram.state.memory.management.transaction import staged_edit
    from openprogram.state.memory.store import _ensure_git_history

    root = owned_memory
    other = root / "topics/other.md"
    other.write_text(
        "# Other\n\nA second fact.[^e-2a5d8b3c01] ^def67890\n\n"
        "[^e-2a5d8b3c01]: Time: `2026-01-01`; Sources: [D1:1](/s/s1#d1-1)\n"
    )
    _ensure_git_history(root)
    old = 1_000_000_000
    for path in (root / "topics").rglob("*.md"):
        os.utime(path, (old, old))
    other_text = other.read_text()

    ok, message = staged_edit(
        root,
        lambda stage: (stage / "topics/note.md").write_text(
            NOTE.replace("keeping", "updating")
        ),
    )

    assert ok, message
    assert "worth updating" in (root / "topics/note.md").read_text()
    assert (root / "topics/note.md").stat().st_mtime != old
    assert other.read_text() == other_text
    assert other.stat().st_mtime == old


def test_a_tool_call_under_the_reply_cites_the_message_the_reply_answers(owned_memory):
    """Tools run under the assistant reply's id; the reply itself carries
    the runtime's authority and no text yet, so it is not what the record
    cites, nor whose authority decides what the edit may do."""
    from openprogram.runtime.permissions import authority
    from openprogram.runtime.context import session_db
    from openprogram.state import store as chat_store

    root = owned_memory
    session_db.default_db().append_message("owned", {
        "id": "owned-user_reply", "role": "assistant", "content": "",
        "timestamp": 1, "predecessor": "owned-user",
        **authority.runtime_authority(authority.local_owner_authority(), "agent/main"),
    })
    token = chat_store._current_turn_id.set("owned-user_reply")
    try:
        result = _invoke("memory_update", {
            "memory_changes": [_create("topics/asked.md", "The owner asked for this.")],
        })
    finally:
        chat_store._current_turn_id.reset(token)
    assert result["ok"] is True, result
    assert "[chat](/s/owned#owned-user)" in (root / "topics/asked.md").read_text()
