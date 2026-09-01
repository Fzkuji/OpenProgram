"""DagSessionDB: named branches + token stats + delete_branch_tail."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from openprogram.context.nodes import Call, ROLE_CODE
from openprogram.store import SessionStore as DagSessionDB
from openprogram.store import SessionNodeWriter


@pytest.fixture
def db(tmp_path: Path) -> DagSessionDB:
    return DagSessionDB(tmp_path / "sessions-git")


def _append(db, sess, mid, *, role="user", parent=None, content="x",
            input_tokens=None, output_tokens=None, model=None,
            cache_read=None):
    msg = {
        "id": mid, "role": role, "content": content,
        "predecessor": parent, "timestamp": time.time(),
    }
    if input_tokens is not None:
        msg["input_tokens"] = input_tokens
    if output_tokens is not None:
        msg["output_tokens"] = output_tokens
    if model is not None:
        msg["token_model"] = model
    if cache_read is not None:
        msg["cache_read_tokens"] = cache_read
    db.append_message(sess, msg)


# Branch enumeration


def test_list_branches_finds_every_tip(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1", role="user")
    _append(db, "s1", "n2", role="assistant", parent="n1")
    # fork: n3 also branches off n1
    _append(db, "s1", "n3", role="user", parent="n1")
    tips = {b["head_msg_id"] for b in db.list_branches("s1")}
    assert tips == {"n2", "n3"}


def test_list_branches_tip_survives_execution_children(db):
    """A branch tip stays a tip when only execution-layer rows hang off
    it — an attach pointer (spawn return) or a runtime node registers
    under ``children_by_predecessor`` but does not continue the
    conversation. Counting them dropped the branch from the panel the
    moment its head spawned a task."""
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1", role="user")
    _append(db, "s1", "n2", role="assistant", parent="n1")
    # fork branch: u3 → a3 is a second tip
    _append(db, "s1", "u3", role="user", parent="n1")
    _append(db, "s1", "a3", role="assistant", parent="u3")
    # spawn-return attach pointer hangs off a3 (execution layer)
    db.append_message("s1", {
        "id": "att1", "role": "assistant", "content": "",
        "predecessor": "a3", "caller": "a3", "timestamp": time.time(),
        "display": "runtime", "function": "attach",
    })
    tips = {b["head_msg_id"] for b in db.list_branches("s1")}
    assert tips == {"n2", "a3"}


@pytest.mark.parametrize("caller", ["", "ROOT"])
@pytest.mark.parametrize("internal_predecessor", [None, "program-2"])
def test_list_branches_collapses_sequential_top_program_runs(
    db, caller, internal_predecessor,
):
    """Only the leaf of a predecessor-linked Program chain is a tip."""
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "u1", role="user")
    _append(db, "s1", "a1", role="assistant", parent="u1")
    writer = SessionNodeWriter(db, "s1")
    writer.append(Call(
        id="program-1", role=ROLE_CODE, name="gui_agent",
        predecessor="a1", caller=caller, created_at=time.time(),
    ))
    writer.append(Call(
        id="program-2", role=ROLE_CODE, name="gui_agent",
        predecessor="program-1", caller=caller, created_at=time.time() + 1,
    ))
    writer.append(Call(
        id="internal-step", role=ROLE_CODE, name="inspect",
        predecessor=internal_predecessor, caller="program-2",
        created_at=time.time() + 2,
    ))

    tips = {b["head_msg_id"] for b in db.list_branches("s1")}

    assert tips == {"program-2"}


def test_list_branches_keeps_parallel_top_program_leaves(db):
    """Programs sharing one predecessor are alternatives, not a chain."""
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "u1", role="user")
    _append(db, "s1", "a1", role="assistant", parent="u1")
    writer = SessionNodeWriter(db, "s1")
    for index in (1, 2):
        writer.append(Call(
            id=f"program-{index}", role=ROLE_CODE, name="gui_agent",
            predecessor="a1", caller="", created_at=time.time() + index,
        ))

    tips = {b["head_msg_id"] for b in db.list_branches("s1")}

    assert tips == {"program-1", "program-2"}


def test_list_branches_excludes_runtime_context_root(db):
    """Primary-tip fallback cannot promote a non-conversation root."""
    db.create_session("s1", agent_id="a")
    writer = SessionNodeWriter(db, "s1")
    writer.append(Call(
        id="runtime-root", role=ROLE_CODE, name="context/system_prompt",
        predecessor=None, caller="ROOT", created_at=time.time() - 1,
        metadata={"display": "runtime"},
    ))
    _append(db, "s1", "u1", role="user")
    _append(db, "s1", "a1", role="assistant", parent="u1")

    tips = {b["head_msg_id"] for b in db.list_branches("s1")}

    assert tips == {"a1"}


def test_list_branches_does_not_restore_merged_primary_tip(db):
    """Primary-tip handling must preserve the merged-head exclusion."""
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "u1", role="user")
    _append(db, "s1", "a1", role="assistant", parent="u1")
    db.mark_merged("s1", ["a1"])

    assert db.list_branches("s1") == []


def test_spawn_branch_register_head_false_keeps_head(db):
    """A same-session sub-agent spawn must not steal the session head
    (context/compaction.md §5) — the transcript follows the head, and a
    stolen head switched the user's window to the agent's conversation
    mid-run."""
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1", role="user")
    _append(db, "s1", "n2", role="assistant", parent="n1")
    db.spawn_branch("s1", "n2", source="agent_spawn",
                    register_head=False, prompt="hi")
    assert (db.get_session("s1") or {})["head_id"] == "n2"
    # The default still registers the new branch head (cross-session
    # sends and legacy callers).
    root2 = db.spawn_branch("s1", "n2", source="agent_spawn", prompt="hi2")
    assert (db.get_session("s1") or {})["head_id"] == root2


def test_list_branches_includes_named_label(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    _append(db, "s1", "n2", parent="n1")
    db.set_branch_name("s1", "n2", "experiment-a")
    rows = db.list_branches("s1")
    assert rows[0]["head_msg_id"] == "n2"
    assert rows[0]["name"] == "experiment-a"


def test_set_branch_name_is_upsert(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    db.set_branch_name("s1", "n1", "first")
    db.set_branch_name("s1", "n1", "second")  # rename
    rows = db.list_branches("s1")
    assert rows[0]["name"] == "second"


def test_delete_branch_name(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    db.set_branch_name("s1", "n1", "label")
    db.delete_branch_name("s1", "n1")
    # After deletion the user-supplied name is gone. No "main" special-
    # case anymore (branch-naming.md 决策 3): the trunk tip falls back to
    # None, which the badge renders as the id short-hex.
    assert db.list_branches("s1")[0]["name"] is None


# delete_branch_tail


def test_delete_branch_tail_removes_subtree(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    _append(db, "s1", "n2", parent="n1")
    _append(db, "s1", "n3", parent="n2")
    _append(db, "s1", "n4", parent="n2")  # sibling to n3
    deleted = db.delete_branch_tail("s1", "n2")
    assert deleted == 3  # n2 + n3 + n4
    remaining = {m["id"] for m in db.get_messages("s1")}
    assert remaining == {"n1"}


def test_delete_branch_tail_missing_returns_zero(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    assert db.delete_branch_tail("s1", "ghost") == 0


# Token stats


def test_token_stats_sums_along_chain(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "u1", role="user")
    _append(db, "s1", "a1", role="assistant", parent="u1",
            input_tokens=100, output_tokens=20, model="claude-opus",
            cache_read=10)
    _append(db, "s1", "u2", role="user", parent="a1")
    _append(db, "s1", "a2", role="assistant", parent="u2",
            input_tokens=200, output_tokens=30, model="claude-opus",
            cache_read=50)
    stats = db.get_branch_token_stats("s1", head_id="a2")
    assert stats["input_tokens"] == 300
    assert stats["output_tokens"] == 50
    assert stats["cache_read_total"] == 60
    assert stats["messages_counted"] == 2
    # "current" = most recent input
    assert stats["current_tokens"] >= 200
    assert stats["model"] == "claude-opus"


def test_token_stats_filters_by_model(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "u1", role="user")
    _append(db, "s1", "a1", role="assistant", parent="u1",
            input_tokens=100, model="opus")
    _append(db, "s1", "u2", role="user", parent="a1")
    _append(db, "s1", "a2", role="assistant", parent="u2",
            input_tokens=200, model="sonnet")
    stats = db.get_branch_token_stats("s1", head_id="a2", model="sonnet")
    assert stats["input_tokens"] == 200
    assert stats["messages_counted"] == 1


# message_exists


def test_message_exists(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    assert db.message_exists("s1", "n1") is True
    assert db.message_exists("s1", "ghost") is False
    assert db.message_exists("nope", "n1") is False


# Auto-naming state (branch-naming.md): extra fields merge, lock survives,
# per-branch turn counter.


def test_set_branch_name_merges_extra_fields(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    db.set_branch_name("s1", "n1", "auto", auto_named=True, name_gen_count=1)
    meta = db.get_branch_meta("s1", "n1")
    assert meta["name"] == "auto"
    assert meta["auto_named"] is True
    assert meta["name_gen_count"] == 1


def test_set_branch_name_preserves_lock_on_rename(db):
    # A name-only write must NOT wipe a previously set lock.
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    db.set_branch_name("s1", "n1", "user-name", name_locked=True)
    db.set_branch_name("s1", "n1", "renamed-again")  # name only
    meta = db.get_branch_meta("s1", "n1")
    assert meta["name"] == "renamed-again"
    assert meta["name_locked"] is True  # lock survived


def test_get_branch_meta_missing_returns_empty(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    assert db.get_branch_meta("s1", "n1") == {}
    assert db.get_branch_meta("s1", "ghost") == {}


def test_bump_branch_turns_increments(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    assert db.bump_branch_turns("s1", "n1") == 1
    assert db.bump_branch_turns("s1", "n1") == 2
    assert db.get_branch_meta("s1", "n1")["turns"] == 2


def test_bump_branch_turns_coexists_with_name(db):
    # Bumping turns must not clobber the name, and naming must not reset turns.
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    db.set_branch_name("s1", "n1", "x")
    db.bump_branch_turns("s1", "n1")
    db.set_branch_name("s1", "n1", "y")
    meta = db.get_branch_meta("s1", "n1")
    assert meta["name"] == "y"
    assert meta["turns"] == 1


# meta.json has more than one writer: the Stage-2 auto-namer runs on a
# background thread while the job runner archives the same branch
# (agent-collaboration.md §2.6). Staging every write through one shared
# ``meta.json.tmp`` let the two interleave their bytes into it — the
# rename then published invalid JSON (read_meta swallows the decode
# error and returns {}, so a rebuild lost title / head / branches) and
# the loser of the rename raised FileNotFoundError at the caller.


def test_concurrent_meta_writers_never_publish_a_torn_file(db):
    import json as _json
    import threading

    db.create_session("s1", agent_id="a")
    _append(db, "s1", "n1")
    meta_path = db._session_dir("s1") / "meta.json"

    errors: list[str] = []
    torn: list[str] = []
    stop = threading.Event()

    def namer():
        i = 0
        while not stop.is_set():
            try:
                db.set_branch_name("s1", "n1", f"auto-{i}", auto_named=True)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")
            i += 1

    def archiver():
        while not stop.is_set():
            try:
                db.set_branch_meta("s1", "n1", archived=True)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")

    def reader():
        while not stop.is_set():
            try:
                _json.loads(meta_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                torn.append("meta.json missing")
            except _json.JSONDecodeError as e:
                torn.append(f"torn json: {e}")

    threads = [threading.Thread(target=f)
               for f in (namer, archiver, reader)]
    for t in threads:
        t.start()
    time.sleep(1.0)
    stop.set()
    for t in threads:
        t.join(10)

    assert errors == []
    assert torn == []
    # Both writers' fields survive: the entry is merged field by field
    # under the index lock, so naming never drops the archive flag.
    meta = db.get_branch_meta("s1", "n1")
    assert meta["archived"] is True
    assert meta["name"].startswith("auto-")
    on_disk = _json.loads(meta_path.read_text(encoding="utf-8"))
    assert on_disk["branches"]["n1"]["archived"] is True
