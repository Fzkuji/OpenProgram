"""Webui's active-branch messages cache + SessionDB hydration.

The webui used to mirror chat messages in an in-memory ``_sessions``
dict, with SessionDB writes happening as a separate side-effect. Plan B
makes SessionDB the truth source: ``_get_messages(conv_id)`` walks the
branch from the session's head_id, with an LRU cache so we don't pay
the SQL walk on every WS frame.

These tests cover the cache layer in isolation; the integration with
the webui's WS handlers is tested by the existing
``tests/integration/test_persistence.py`` and ``test_ws_reconnect.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.agent.session_db import SessionDB


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh SessionDB + isolated webui module state.

    We import server fresh per test so its module-level
    ``_sessions`` and ``_msg_cache`` start clean. We also point
    SessionDB.default_db at a per-test sqlite file so the cache
    actually has somewhere to read from.
    """
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.agent.session_db.default_db",
                        lambda: db)

    # Reset webui module state. We don't reimport — we just clear the
    # in-process structures so each test sees a clean slate.
    from openprogram.webui import server as srv
    srv._sessions.clear()
    srv._msg_cache.clear()
    return srv, db


def test_get_messages_reads_from_db_on_cache_miss(server) -> None:
    srv, db = server
    db.create_session("c1", "main", title="t")
    db.append_message("c1", {"id": "m1", "role": "user",
                              "content": "hi", "timestamp": 1.0,
                              "parent_id": None})
    db.set_head("c1", "m1")

    msgs = srv._get_messages("c1")
    assert [m["id"] for m in msgs] == ["m1"]


def test_get_messages_uses_cache_on_warm_read(server) -> None:
    srv, db = server
    db.create_session("c1", "main")
    db.append_message("c1", {"id": "m1", "role": "user", "content": "x",
                              "timestamp": 1.0, "parent_id": None})
    db.set_head("c1", "m1")

    # First read populates cache.
    first = srv._get_messages("c1")
    assert first

    # Mutate the DB without invalidating cache → second read should
    # still see the cached version (proves the cache is actually used).
    db.append_message("c1", {"id": "m2", "role": "assistant", "content": "y",
                              "timestamp": 2.0, "parent_id": "m1"})
    db.set_head("c1", "m2")

    second = srv._get_messages("c1")
    assert [m["id"] for m in second] == ["m1"]   # cached, stale on purpose


def test_invalidate_clears_cache(server) -> None:
    srv, db = server
    db.create_session("c1", "main")
    db.append_message("c1", {"id": "m1", "role": "user", "content": "x",
                              "timestamp": 1.0, "parent_id": None})
    db.set_head("c1", "m1")

    srv._get_messages("c1")  # warm cache
    db.append_message("c1", {"id": "m2", "role": "assistant", "content": "y",
                              "timestamp": 2.0, "parent_id": "m1"})
    db.set_head("c1", "m2")
    srv._invalidate_messages("c1")

    fresh = srv._get_messages("c1")
    assert [m["id"] for m in fresh] == ["m1", "m2"]


def test_get_messages_unknown_conv_returns_empty(server) -> None:
    srv, _db = server
    assert srv._get_messages("does-not-exist") == []


def test_lru_cap_evicts_oldest(server, monkeypatch: pytest.MonkeyPatch) -> None:
    srv, db = server
    monkeypatch.setattr(srv, "_MSG_CACHE_CAP", 3)
    for i in range(5):
        cid = f"c{i}"
        db.create_session(cid, "main")
        db.append_message(cid, {"id": f"m{i}", "role": "user",
                                 "content": "x", "timestamp": 1.0,
                                 "parent_id": None})
        db.set_head(cid, f"m{i}")
        srv._get_messages(cid)   # warm
    # Cache should hold the 3 most-recent (c2, c3, c4).
    cached_keys = list(srv._msg_cache.keys())
    assert cached_keys == ["c2", "c3", "c4"]


def test_get_or_create_conversation_hydrates_from_db(server) -> None:
    """A fresh worker process / restart sees pre-existing SessionDB
    conversations through the in-memory _sessions dict. The
    hydration must happen at conv creation time so existing webui
    code that reads conv["messages"] keeps working without changes."""
    srv, db = server
    db.create_session("c1", "main", title="from-db")
    db.append_message("c1", {"id": "m1", "role": "user",
                              "content": "hi", "timestamp": 1.0,
                              "parent_id": None})
    db.append_message("c1", {"id": "m2", "role": "assistant",
                              "content": "hello", "timestamp": 2.0,
                              "parent_id": "m1"})
    db.set_head("c1", "m2")

    conv = srv._get_or_create_session("c1")
    assert [m["id"] for m in conv["messages"]] == ["m1", "m2"]
    assert conv["head_id"] == "m2"


def test_append_msg_dual_writes_memory_and_db(server) -> None:
    """``_append_msg`` is the unified write path for non-dispatcher
    webui writes. Each call must:
      - mutate conv["messages"] (so existing readers see it immediately)
      - write to SessionDB.messages (so dispatcher/TUI/channels read fresh)
      - bump SessionDB.sessions.head_id (so get_branch follows the new leaf)
      - invalidate the cache (so the next _get_messages re-reads)
    """
    srv, db = server
    conv = srv._get_or_create_session("c1", agent_id="main")

    msg = {"id": "m1", "role": "user", "content": "hello",
           "timestamp": 1.0}
    srv._append_msg(conv, msg)

    # In-memory mirror
    assert [m["id"] for m in conv["messages"]] == ["m1"]
    assert conv["head_id"] == "m1"

    # SessionDB
    db_msgs = db.get_messages("c1")
    assert [m["id"] for m in db_msgs] == ["m1"]
    sess = db.get_session("c1")
    assert sess["head_id"] == "m1"

    # Cache reflects the new branch
    assert [m["id"] for m in srv._get_messages("c1")] == ["m1"]


def test_append_msg_invalidates_existing_cache(server) -> None:
    """Cache must drop after _append_msg so the next reader sees the
    new row. This is the regression-prone case where the old code wrote
    to the dict but stale cache served readers."""
    srv, db = server
    conv = srv._get_or_create_session("c1", agent_id="main")
    srv._append_msg(conv, {"id": "m1", "role": "user", "content": "x",
                            "timestamp": 1.0})
    # Warm the cache by reading
    assert [m["id"] for m in srv._get_messages("c1")] == ["m1"]

    srv._append_msg(conv, {"id": "m2", "role": "assistant", "content": "y",
                            "timestamp": 2.0})
    # Next reader sees both, not just the cached m1
    assert [m["id"] for m in srv._get_messages("c1")] == ["m1", "m2"]


def test_set_active_head_syncs_db_memory_and_cache(server) -> None:
    """_set_active_head is the unified branch-switch primitive used by
    retry / edit / sibling-checkout. It must update SessionDB,
    in-memory conv["head_id"], and invalidate the cache atomically
    enough that the next reader sees a coherent view."""
    srv, db = server
    conv = srv._get_or_create_session("c1", agent_id="main")
    srv._append_msg(conv, {"id": "m1", "role": "user", "content": "x",
                            "timestamp": 1.0})
    srv._append_msg(conv, {"id": "m2", "role": "assistant", "content": "y",
                            "timestamp": 2.0})
    srv._get_messages("c1")  # warm cache

    srv._set_active_head("c1", "m1")    # rewind to m1

    # SessionDB
    assert db.get_session("c1")["head_id"] == "m1"
    # In-memory mirror
    assert conv["head_id"] == "m1"
    # Cache fresh + branch correct
    assert [m["id"] for m in srv._get_messages("c1")] == ["m1"]


def test_set_active_head_to_none_clears(server) -> None:
    srv, db = server
    conv = srv._get_or_create_session("c1", agent_id="main")
    srv._append_msg(conv, {"id": "m1", "role": "user", "content": "x",
                            "timestamp": 1.0})
    srv._set_active_head("c1", None)
    assert db.get_session("c1")["head_id"] is None
    assert conv["head_id"] is None


def test_deepest_leaf_db_finds_subtree_tip(server) -> None:
    srv, db = server
    db.create_session("c1", "main")
    # root → a → b (leaf t=2)
    #          → c → d (leaf t=4)
    db.append_message("c1", {"id": "root", "role": "user", "content": "x",
                              "timestamp": 1.0, "parent_id": None})
    db.append_message("c1", {"id": "a", "role": "assistant", "content": "x",
                              "timestamp": 1.5, "parent_id": "root"})
    db.append_message("c1", {"id": "b", "role": "user", "content": "x",
                              "timestamp": 2.0, "parent_id": "a"})
    db.append_message("c1", {"id": "c", "role": "user", "content": "x",
                              "timestamp": 3.0, "parent_id": "a"})
    db.append_message("c1", {"id": "d", "role": "assistant", "content": "x",
                              "timestamp": 4.0, "parent_id": "c"})

    assert srv._deepest_leaf_db("c1", "a") == "d"
    assert srv._deepest_leaf_db("c1", "b") == "b"
    assert srv._deepest_leaf_db("c1", "missing") is None


def test_agentic_function_result_persists_via_append_msg(server) -> None:
    """The webui "run" action ends by calling _append_msg with the
    @agentic_function's return value as an assistant message. After
    task #17 that single _append_msg writes through to SessionDB,
    so a fresh worker can hydrate the run result without depending
    on _save_conversation having flushed."""
    srv, db = server
    conv = srv._get_or_create_session("c1", agent_id="main")
    # Simulate run action: user runtime command + assistant result
    srv._append_msg(conv, {
        "id": "u1", "role": "user",
        "display": "runtime",         # marks it as a function trigger
        "content": "create(description='hello world')",
        "timestamp": 1.0,
    })
    srv._append_msg(conv, {
        "id": "a1", "role": "assistant",
        "function": "create",          # tagged with function name
        "content": "Created the function successfully.",
        "timestamp": 2.0,
    })

    # The run action also stuffs function_trees on the conv dict.
    # function_trees stay in memory only — they're a separate concept
    # from chat messages and should NOT land in SessionDB.messages.
    conv["function_trees"] = [{"name": "create", "status": "success",
                                  "output": "ok"}]

    # New worker process (simulated) sees the messages via SessionDB
    fresh_msgs = db.get_branch("c1")
    fresh_ids = [m["id"] for m in fresh_msgs]
    assert fresh_ids == ["u1", "a1"]
    # The assistant message keeps its function annotation
    a_row = next(m for m in fresh_msgs if m["id"] == "a1")
    assert a_row["role"] == "assistant"
    assert a_row.get("function") == "create"

    # function_trees never leaked into SessionDB (it's not a message)
    by_role = {m["role"] for m in fresh_msgs}
    assert by_role == {"user", "assistant"}


def test_hydrate_returns_branch_after_set_head_change(server) -> None:
    """When the head moves (retry / edit), _hydrate_messages_from_db
    forces a fresh load. Tests the seam used by retry / edit handlers."""
    srv, db = server
    db.create_session("c1", "main")
    db.append_message("c1", {"id": "u1", "role": "user", "content": "first",
                              "timestamp": 1.0, "parent_id": None})
    db.append_message("c1", {"id": "a1", "role": "assistant", "content": "old",
                              "timestamp": 2.0, "parent_id": "u1"})
    db.set_head("c1", "a1")
    srv._get_messages("c1")  # warm

    # User retries → new branch from u1's parent (None)
    db.append_message("c1", {"id": "u1prime", "role": "user",
                              "content": "retry", "timestamp": 3.0,
                              "parent_id": None})
    db.append_message("c1", {"id": "a1prime", "role": "assistant",
                              "content": "new", "timestamp": 4.0,
                              "parent_id": "u1prime"})
    db.set_head("c1", "a1prime")

    # _hydrate forces re-read; branch now follows the new fork.
    fresh = srv._hydrate_messages_from_db("c1")
    assert [m["id"] for m in fresh] == ["u1prime", "a1prime"]
