"""A branch's record follows the branch through a turn.

One branch; a turn is under way (its message written, its reply not yet)
when the person renames the branch on the tip they see and picks another
model for it. Whether that lands before the reply, while the reply's
append holds the conversation lock, after the reply, or after the turn
ended, the branch keeps one record at its new tip holding the name, the
lock, the turn count from before plus this turn, and its own settings.
Runs on the real store with a real thread for the write under the lock.
"""
from __future__ import annotations

import threading

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


def _finalize(store, sid, reply):
    """What finalize_turn does to the branch record at the turn's end."""
    from openprogram.runtime.conversation.turn_runtime.titles import maybe_auto_name_branch
    store.carry_branch_entry(sid, reply)
    maybe_auto_name_branch(store, sid, reply)


def _write(store, sid, tip):
    """A person's writes to the branch as they saw it: a rename on the
    tip on screen and a model pick on the branch."""
    branch = store.get_session(sid)["active_branch_id"]
    store.set_branch_name(sid, tip, "Mid-turn", name_locked=True)
    store.update_session(sid, model_override="other-model", expected_branch_id=branch)


@pytest.mark.parametrize("when", ["before_reply", "during_reply", "after_reply", "after_finalize"])
def test_branch_record_follows_a_turn_whatever_is_written_in_between(store, monkeypatch, when):
    from openprogram.runtime.conversation import side_work
    from openprogram.state.store.session import branch_config

    monkeypatch.setattr(side_work, "allowed", lambda: False)
    sid = "s1"
    store.create_session(sid, "main", title="t")

    def add(mid, role, prev):
        store.append_message(sid, {"id": mid, "role": role, "content": mid,
                                   "predecessor": prev, "agent_id": "main"})

    add("u0", "user", "")
    add("a1", "assistant", "u0")
    _finalize(store, sid, "a1")
    branch = store.get_session(sid)["active_branch_id"]
    assert store.get_branch_meta(sid, "a1")["turns"] == 1

    add("u2", "user", "a1")
    seen = store.get_session(sid)["head_id"]  # the tip on screen mid-turn
    assert seen == "u2"
    if when == "before_reply":
        _write(store, sid, seen)
        add("a3", "assistant", "u2")
    elif when == "during_reply":
        # The writes reach the store while the reply's append holds the
        # conversation lock, so they land right after it, on the old tip.
        real = branch_config.advance_branch
        writer: list[threading.Thread] = []

        def advance(meta, *args, **kwargs):
            if not writer:
                thread = threading.Thread(target=_write, args=(store, sid, seen))
                writer.append(thread)
                thread.start()
                thread.join(0.2)
            return real(meta, *args, **kwargs)

        monkeypatch.setattr(branch_config, "advance_branch", advance)
        add("a3", "assistant", "u2")
        writer[0].join(10)
        assert not writer[0].is_alive()
        monkeypatch.setattr(branch_config, "advance_branch", real)
    else:
        add("a3", "assistant", "u2")
        if when == "after_reply":
            _write(store, sid, seen)
    _finalize(store, sid, "a3")
    if when == "after_finalize":
        _write(store, sid, seen)

    session = store.get_session(sid)
    assert session["active_branch_id"] == branch
    record = store.get_branch_meta(sid, "a3")
    assert record["name"] == "Mid-turn" and record["name_locked"] is True
    assert record["turns"] == 2
    assert set(store.branch_entries(sid)) == {"a3"}
    assert session["model_override"] == "other-model"
    assert session["branch_refs"][branch]["config"]["model_override"] == "other-model"
    rows = store.list_branches(sid)
    assert [(row["head_msg_id"], row.get("branch_id"), row["name"]) for row in rows] == [
        ("a3", branch, "Mid-turn")]
