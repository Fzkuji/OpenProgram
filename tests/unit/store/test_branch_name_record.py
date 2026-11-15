"""A branch's name lives in one record, and switching branches keeps it.

Two branches, A and B, with B selected. Renaming A writes its record; the
branch list shows the name, and switching to A keeps the name and its lock.
A name or another branch's record written between the switch's own read and
its HEAD write is kept too. Runs on the real store.
"""
from __future__ import annotations

import pytest

from openprogram.state.store.session.session_store import SessionStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: s)
    import openprogram.state.store.session.session_store as ss_mod
    monkeypatch.setattr(ss_mod.shared, "_default_store", s)
    yield s
    s.close()


@pytest.fixture
def srv(monkeypatch):
    from openprogram.interfaces.server import server as _s
    monkeypatch.setattr(_s, "_sessions", {})
    monkeypatch.setattr(_s, "_msg_cache", type(_s._msg_cache)())
    return _s


def _two_branches(store, srv, sid="s1"):
    """A: u0 -> a1 -> u2 -> a3; B: a1 -> u4 -> a5, B selected."""
    store.create_session(sid, "main", title="t")
    prev = ""
    for mid, role in (("u0", "user"), ("a1", "assistant"), ("u2", "user"), ("a3", "assistant")):
        store.append_message(sid, {"id": mid, "role": role, "content": mid,
                                   "predecessor": prev, "agent_id": "main"})
        prev = mid
    store.bump_branch_turns(sid, "a3")
    branch_a = store.get_session(sid)["active_branch_id"]
    assert srv._set_active_head(sid, "a1")
    prev = "a1"
    for mid, role in (("u4", "user"), ("a5", "assistant")):
        store.append_message(sid, {"id": mid, "role": role, "content": mid,
                                   "predecessor": prev, "agent_id": "main"})
        prev = mid
    session = store.get_session(sid)
    assert session["head_id"] == "a5" and session["active_branch_id"] != branch_a
    return branch_a


def _listed(store, sid, tip):
    return next(row for row in store.list_branches(sid) if row["head_msg_id"] == tip)


def test_rename_of_unselected_branch_is_listed_and_survives_checkout(store, srv):
    branch_a = _two_branches(store, srv)
    store.set_branch_name("s1", "a3", "Alpha", name_locked=True)
    assert _listed(store, "s1", "a3")["name"] == "Alpha"

    assert srv._set_active_head("s1", "a3", branch_id=branch_a)
    record = store.get_branch_meta("s1", "a3")
    assert record["name"] == "Alpha" and record["name_locked"] is True
    assert record["turns"] == 1
    assert _listed(store, "s1", "a3")["name"] == "Alpha"
    # Renaming the selected branch now and switching back keeps the record too.
    store.set_branch_name("s1", "a3", "Alpha 2", name_locked=True)
    assert srv._set_active_head("s1", "a5")
    assert srv._set_active_head("s1", "a3", branch_id=branch_a)
    assert store.get_branch_meta("s1", "a3")["name"] == "Alpha 2"


def test_checkout_keeps_records_written_after_its_read(store, srv, monkeypatch):
    branch_a = _two_branches(store, srv)
    real_cas = store.compare_and_set_head

    def late_writes(*args, **kwargs):
        # Written after the switch read the conversation, before its CAS.
        store.set_branch_name("s1", "a3", "Alpha", name_locked=True)
        store.set_branch_name("s1", "a5", "Beta", auto_named=True)
        return real_cas(*args, **kwargs)

    monkeypatch.setattr(store, "compare_and_set_head", late_writes)
    assert srv._set_active_head("s1", "a3", branch_id=branch_a)
    assert store.get_branch_meta("s1", "a3")["name"] == "Alpha"
    assert store.get_branch_meta("s1", "a5")["name"] == "Beta"
    names = {row["head_msg_id"]: row["name"] for row in store.list_branches("s1")}
    assert names["a3"] == "Alpha" and names["a5"] == "Beta"


def test_whole_branch_maps_in_a_head_update_are_not_written(store, srv):
    _two_branches(store, srv)
    stale = store.get_session("s1")
    store.set_branch_name("s1", "a3", "Alpha", name_locked=True)
    assert store.compare_and_set_head(
        "s1", "a5", "a5",
        meta_update={"branches": stale.get("branches") or {}, "branch_refs": stale["branch_refs"]},
    )
    assert store.get_branch_meta("s1", "a3")["name"] == "Alpha"


def test_background_run_carries_the_task_branch_name(store, srv):
    _two_branches(store, srv)
    branch_id = store.reserve_task_branch("s1", task_id="t1", msg_id="u6",
                                          agent_id="main", label={"name": "Nightly"})
    assert "label" not in store.get_session("s1")["branch_refs"][branch_id]
    store.append_message("s1", {"id": "u6", "role": "user", "content": "go",
                                "predecessor": "a5", "agent_id": "main"}, advance_head=False)
    store.append_message("s1", {"id": "a7", "role": "assistant", "content": "done",
                                "predecessor": "u6", "agent_id": "main"}, advance_head=False)
    ref = store.get_session("s1")["branch_refs"][branch_id]
    assert ref["head_id"] == "a7"
    assert _listed(store, "s1", "a7")["name"] == "Nightly"


def test_parked_label_is_read_and_put_back_when_the_tip_has_no_record(store, srv):
    branch_a = _two_branches(store, srv)
    refs = store.get_session("s1")["branch_refs"]
    store.delete_branch_name("s1", "a3")
    store.update_session("s1", branch_refs={**refs, branch_a: {**refs[branch_a], "label": {"name": "Old"}}})
    assert _listed(store, "s1", "a3")["name"] == "Old"
    assert srv._set_active_head("s1", "a3", branch_id=branch_a)
    assert store.get_branch_meta("s1", "a3")["name"] == "Old"


# ── One branch through a turn: the record follows its tip ────────────

def _finalize(store, sid, reply):
    """What finalize_turn does to the branch record at the turn's end."""
    from openprogram.runtime.conversation.turn_runtime.titles import maybe_auto_name_branch
    store.carry_branch_entry(sid, reply)
    maybe_auto_name_branch(store, sid, reply)


def test_a_named_branch_still_counts_its_turns(store, monkeypatch):
    from openprogram.runtime.conversation import side_work

    monkeypatch.setattr(side_work, "allowed", lambda: False)
    store.create_session("s1", "main", title="t")
    prev = ""
    for n in range(3):
        u, a = f"u{2 * n}", f"a{2 * n + 1}"
        store.append_message("s1", {"id": u, "role": "user", "content": u,
                                    "predecessor": prev, "agent_id": "main"})
        store.append_message("s1", {"id": a, "role": "assistant", "content": a,
                                    "predecessor": u, "agent_id": "main"})
        _finalize(store, "s1", a)
        if n == 0:
            store.set_branch_name("s1", a, "Mine", name_locked=True)
        prev = a
    assert store.get_branch_meta("s1", "a5")["turns"] == 3


def test_automatic_name_never_lands_on_a_name_the_person_gave(store):
    store.create_session("s1", "main", title="t")
    store.append_message("s1", {"id": "u0", "role": "user", "content": "u0",
                                "predecessor": "", "agent_id": "main"})
    store.set_branch_name("s1", "u0", "Mine", name_locked=True)
    store.set_branch_name("s1", "u0", "Robot", auto_named=True)
    assert store.get_branch_meta("s1", "u0")["name"] == "Mine"
