"""DagSessionDB: named branches + token stats + delete_branch_tail."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from openprogram.state.store.session.nodes import Call, ROLE_CODE
from openprogram.state.store import SessionStore as DagSessionDB
from openprogram.state.store import SessionNodeWriter


@pytest.fixture
def db(tmp_path: Path) -> DagSessionDB:
    return DagSessionDB(tmp_path / "sessions-git")


def _append(db, sess, mid, *, role="user", parent=None, content="x",
            input_tokens=None, output_tokens=None, model=None,
            cache_read=None, cache_write=None):
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
    if cache_write is not None:
        msg["cache_write_tokens"] = cache_write
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


def test_token_stats_hit_rate_counts_what_was_written_to_the_cache(db):
    """input_tokens leaves out both the cached and the newly cached tokens,
    so the prompt is input + cache_read + cache_write."""
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "u1", role="user")
    _append(db, "s1", "a1", role="assistant", parent="u1",
            input_tokens=100, model="claude-opus", cache_write=800)
    _append(db, "s1", "u2", role="user", parent="a1")
    _append(db, "s1", "a2", role="assistant", parent="u2",
            input_tokens=100, model="claude-opus", cache_read=800, cache_write=200)
    stats = db.get_branch_token_stats("s1", head_id="a2")
    assert stats["cache_hit_rate"] == 800 / (200 + 800 + 1000)


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


def test_carry_branch_entry_follows_the_extended_tip(db):
    # A turn moves the tip; the name, its lock and the counter move with
    # it, so the next turn neither forgets the user's name nor restarts
    # the Stage-2 thresholds at 1.
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "u1")
    _append(db, "s1", "a1", role="assistant", parent="u1")
    db.set_branch_name("s1", "a1", "mine", name_locked=True)
    db.bump_branch_turns("s1", "a1")
    _append(db, "s1", "u2", parent="a1")
    _append(db, "s1", "steer", parent="u2")
    _append(db, "s1", "a2", role="assistant", parent="steer")
    # The record moved with each message the turn wrote; carrying at the
    # turn's end finds it already there.
    assert db.carry_branch_entry("s1", "a2") is False
    assert db.get_branch_meta("s1", "a1") == {}
    meta = db.get_branch_meta("s1", "a2")
    assert meta["name"] == "mine" and meta["name_locked"] is True
    assert db.bump_branch_turns("s1", "a2") == 2
    assert {b["head_msg_id"]: b["name"] for b in db.list_branches("s1")} == {"a2": "mine"}


def test_carry_branch_entry_leaves_a_fork_point_alone(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "u1")
    _append(db, "s1", "a1", role="assistant", parent="u1")
    _append(db, "s1", "u2", parent="a1")
    _append(db, "s1", "a2", role="assistant", parent="u2")
    # Legacy data: the fork point still holds the entry an older turn left.
    # (A write addressed to a1 now goes to the branch that moved past it,
    # so the old record is planted on disk as an older build left it.)
    pair = db._open("s1")

    def _plant(meta):
        meta["branches"] = {**(meta.get("branches") or {}), "a1": {"name": "old"}}
        return True, None

    db._update_meta(pair[0], pair[1], _plant)
    _append(db, "s1", "u3", parent="a1")
    _append(db, "s1", "a3", role="assistant", parent="u3")
    assert db.carry_branch_entry("s1", "a3") is False
    assert db.get_branch_meta("s1", "a1")["name"] == "old"
    assert db.get_branch_meta("s1", "a3") == {}


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
    from openprogram.state.store.session.session_log import SessionLog
    log = SessionLog(db._session_dir("s1"))

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
        seen_branches = False
        while not stop.is_set():
            has_branches = bool(log.read_meta().get("branches"))
            if seen_branches and not has_branches:
                torn.append("meta lost its branches")
            seen_branches = seen_branches or has_branches

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
    # on disk under the session write lock, so naming never drops the
    # archive flag.
    meta = db.get_branch_meta("s1", "n1")
    assert meta["archived"] is True
    assert meta["name"].startswith("auto-")
    on_disk = log.read_meta()
    assert on_disk["branches"]["n1"]["archived"] is True


# Every branch-record write is a read-modify-write of the durable record
# under the session write lock. A writer that changed the cached record
# and wrote the whole cache later lost its change to a writer that, in
# between, replaced the cache with what was on disk: close_agent archived
# a running agent, the turn it cut off finished and recorded its token
# count, and the archive vanished (agent-collaboration.md, archiving).


def test_archive_survives_concurrent_session_writes(db):
    import threading

    db.create_session("s1", agent_id="a")
    tips = [f"t{i}" for i in range(60)]
    _append(db, "s1", "root")
    for tip in tips:
        _append(db, "s1", tip, role="assistant", parent="root")
    stop = threading.Event()
    errors: list[str] = []

    def turn_writer():
        i = 0
        while not stop.is_set():
            try:
                db.update_session("s1", last_prompt_tokens=i)
                db.bump_branch_turns("s1", tips[i % len(tips)])
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")
            i += 1

    def archiver():
        for tip in tips:
            db.set_branch_meta("s1", tip, archived=True, archived_at=time.time())

    writers = [threading.Thread(target=turn_writer) for _ in range(2)]
    for t in writers:
        t.start()
    archiver()
    stop.set()
    for t in writers:
        t.join(10)
    assert errors == []
    lost = [tip for tip in tips if not db.get_branch_meta("s1", tip).get("archived")]
    assert lost == []
    fresh = DagSessionDB(db.root_path)
    assert [tip for tip in tips if not fresh.get_branch_meta("s1", tip).get("archived")] == []


def test_branch_writes_from_two_stores_merge(tmp_path):
    """Two stores on one folder stand for two processes: neither's cached
    copy of the branch records may overwrite what the other wrote."""
    import threading

    root = tmp_path / "sessions-git"
    a = DagSessionDB(root)
    a.create_session("s1", agent_id="a")
    _append(a, "s1", "root")
    tips = [f"t{i}" for i in range(40)]
    for tip in tips:
        _append(a, "s1", tip, role="assistant", parent="root")
    b = DagSessionDB(root)
    b.get_branch_meta("s1", "root")

    def archive():
        for tip in tips:
            a.set_branch_meta("s1", tip, archived=True)

    def name():
        for tip in tips:
            b.set_branch_name("s1", tip, f"n-{tip}", auto_named=True)

    threads = [threading.Thread(target=archive), threading.Thread(target=name)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    fresh = DagSessionDB(root)
    for tip in tips:
        entry = fresh.get_branch_meta("s1", tip)
        assert entry.get("archived") is True, (tip, entry)
        assert entry.get("name") == f"n-{tip}", (tip, entry)


def test_late_branch_writes_do_not_resurrect_or_unarchive(db):
    db.create_session("s1", agent_id="a")
    _append(db, "s1", "u1")
    _append(db, "s1", "a1", role="assistant", parent="u1")
    _append(db, "s1", "a2", role="assistant", parent="u1")
    db.set_branch_meta("s1", "a1", archived=True)
    db.set_branch_meta("s1", "a1", archived=False)
    db.set_branch_name("s1", "a1", "late", auto_named=True)
    assert db.get_branch_meta("s1", "a1")["archived"] is True
    # A deleted branch's record stays gone when a late writer arrives.
    assert db.delete_branch_tail("s1", "a2") == 1
    assert db.bump_branch_turns("s1", "a2") == 0
    db.set_branch_name("s1", "a2", "late", auto_named=True)
    assert db.get_branch_meta("s1", "a2") == {}
    # A missing conversation is not created by a branch write.
    db.set_branch_name("ghost", "x", "late")
    db.bump_branch_turns("ghost", "x")
    db.set_branch_meta("ghost", "x", archived=True)
    assert db.get_session("ghost") is None
