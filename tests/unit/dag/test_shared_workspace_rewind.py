"""User-visible branches share files; rewind withdraws on the same branch."""
import pytest
from pathlib import Path

from tests.unit.dag.test_rewind_modes import Session, store, _append, _apply
from openprogram.runtime.conversation.rewind import plan_rewind, rewind_to
from openprogram.runtime.conversation.rewind_marks import undo_rewind, redo_rewind, take_rewind_note


def test_rewind_keeps_branch_identity_and_hides_withdrawn_versions(store, tmp_path):
    file = tmp_path / "code.py"
    file.write_text("zero")
    session = Session(store, "s")
    session.turn_edits((file, "one"))
    session.turn_edits((file, "two"))
    store.set_branch_name("s", "a2", "My work")
    store.update_session("s", active_branch_id="work", branch_refs={"work": {"branch_id": "work", "head_id": "a2"}})
    _, result = _apply("s", "u2")
    assert result["status"] == "committed"
    assert store.get_session("s")["active_branch_id"] == "work"
    assert set(store.get_session("s")["branch_refs"]) == {"work"}
    assert {m["id"] for m in store.get_messages("s")} == {"u1", "a1"}
    assert [(b["head_msg_id"], b["name"]) for b in store.list_branches("s")] == [("a1", "My work")]
    assert store.transcript_rows("s", ["u2", "a2"]) == []
    assert store.transcript_children("s", "a1") == []
    _append(store, "s", "u3", "user", "a1")
    _append(store, "s", "a3", "assistant", "u3")
    store.set_head("s", "a3")
    store.carry_branch_entry("s", "a3")
    assert [(b["head_msg_id"], b["name"]) for b in store.list_branches("s")] == [("a3", "My work")]
    assert file.read_text() == "one"


def test_rewind_preserves_an_existing_sibling_and_shared_prefix(store, tmp_path):
    file = tmp_path / "code.py"
    file.write_text("zero")
    session = Session(store, "s")
    session.turn_edits((file, "one"))
    session.turn_edits((file, "two"))
    _append(store, "s", "other-u", "user", "a1")
    _append(store, "s", "other-a", "assistant", "other-u")
    _, result = _apply("s", "u1", "conversation")
    assert result["status"] == "committed"
    visible = {m["id"] for m in store.get_messages("s")}
    assert visible == {"u1", "a1", "other-u", "other-a"}
    assert {b["head_msg_id"] for b in store.list_branches("s")} == {"ROOT", "other-a"}
    assert file.read_text() == "two"


def test_rewind_undo_redo_do_not_create_visible_siblings(store, tmp_path):
    session = Session(store, "s")
    session.turn_edits()
    session.turn_edits()
    _, result = _apply("s", "u2")
    take_rewind_note("s")
    _append(store, "s", "u3", "user", "a1")
    _append(store, "s", "a3", "assistant", "u3")
    store.set_head("s", "a3")
    assert undo_rewind("s")["status"] == "committed"
    assert {b["head_msg_id"] for b in store.list_branches("s")} == {"a2"}
    assert redo_rewind("s")["status"] == "committed"
    assert {b["head_msg_id"] for b in store.list_branches("s")} == {"a3"}


def test_rewind_conflict_does_not_partially_restore_or_withdraw(store, tmp_path):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_text("zero"); b.write_text("zero")
    session = Session(store, "s")
    session.turn_edits((a, "one"), (b, "one"))
    other = Session(store, "other")
    other.turn_edits((a, "other"))
    plan = plan_rewind("s", "u1")
    result = rewind_to("s", "u1")
    assert plan["status"] != "ready" and result["status"] != "committed"
    assert (a.read_text(), b.read_text()) == ("other", "one")
    assert store.get_session("s")["head_id"] == "a1"


def test_rewind_ignores_snapshot_paths_not_written_by_the_suffix(store, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("a0")
    b.write_text("b0")
    session = Session(store, "s")
    session.turn_edits((a, "a1"))
    session.turn_edits((b, "b1"))
    a.write_text("human work")
    plan, result = _apply("s", "u2")
    assert result["status"] == "committed"
    assert [row["path"] for row in plan["files"]] == [str(b)]
    assert (a.read_text(), b.read_text()) == ("human work", "b0")


def test_later_sibling_edit_blocks_rewind_even_in_the_same_session(store, tmp_path):
    file = tmp_path / "code"
    file.write_text("zero")
    session = Session(store, "s")
    session.turn_edits((file, "one"))
    session.turn_edits((file, "two"))
    store.set_head("s", "a1")
    session.head = "a1"
    session.turn_edits((file, "sibling"))
    store.set_head("s", "a2")
    plan = plan_rewind("s", "u2")
    assert plan["status"] == "blocked"
    assert plan["skipped"][0]["reason"] == "changed_since_edit"
    assert rewind_to("s", "u2")["status"] == "blocked"
    assert file.read_text() == "sibling"
    _, result = _apply("s", "u2", "conversation")
    assert result["status"] == "committed"
    assert {b["head_msg_id"] for b in store.list_branches("s")} == {"a1", "a3"}


def test_copy_preserves_write_provenance_and_protects_later_changes(store, tmp_path):
    from openprogram.runtime.context.branch_session import branch_session
    file = tmp_path / "code"
    file.write_text("zero")
    session = Session(store, "s")
    session.turn_edits((file, "one"))
    copied = branch_session("s", "a1", store=store)
    from openprogram.runtime.conversation.rewind import list_rewind_points
    prompt = list_rewind_points(copied)[0]["msg_id"]
    assert plan_rewind(copied, prompt)["has_changes"] is True
    file.write_text("human")
    assert rewind_to(copied, prompt)["status"] == "blocked"
    assert file.read_text() == "human"


def test_withdrawal_survives_store_reload_and_undo_restores_rows(store, tmp_path):
    from openprogram.state.store.session.session_store import SessionStore
    session = Session(store, "s")
    session.turn_edits()
    session.turn_edits()
    _apply("s", "u2")
    reopened = SessionStore(store.root_path)
    assert reopened.get_messages_by_id("s", ["u2", "a2"]) == []
    assert reopened.get_branch("s", "a2") == []
    assert {b["head_msg_id"] for b in reopened.list_branches("s")} == {"a1"}
    assert undo_rewind("s")["status"] == "committed"
    assert {m["id"] for m in reopened.get_messages("s")} == {"u1", "a1", "u2", "a2"}


def _checkout_server(monkeypatch, store):
    from openprogram.interfaces.server import server
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    for key, value in {"_is_run_active": lambda sid: False, "_save_session": lambda sid: None,
                       "_invalidate_messages": lambda sid: None, "refresh_context_stats": lambda sid: None,
                       "_sessions": {}}.items():
        monkeypatch.setattr(server, key, value)


def test_http_checkout_rejects_withdrawn_version_and_clears_reads_on_switch(store, tmp_path, monkeypatch):
    import asyncio
    from openprogram.interfaces.server.routes.chat_branches import post_chat_checkout
    from openprogram.state.store.snapshot import read_tracking
    _checkout_server(monkeypatch, store)
    file = tmp_path / "code"
    file.write_text("current")
    session = Session(store, "s")
    session.turn_edits()
    session.turn_edits()
    _append(store, "s", "ub", "user", "a1")
    _append(store, "s", "ab", "assistant", "ub")
    _apply("s", "u2")
    response = asyncio.run(post_chat_checkout({"session_id": "s", "msg_id": "a2"}))
    assert response.status_code == 409
    assert store.get_session("s")["head_id"] == "a1"
    assert store.get_deepest_leaf("s", "u2") is None
    read_tracking.mark_seen(str(file), session_id="s")
    response = asyncio.run(post_chat_checkout({"session_id": "s", "msg_id": "ab"}))
    assert response.status_code == 200
    assert read_tracking.check_fresh(str(file), session_id="s") == read_tracking.NEVER_READ
    assert take_rewind_note("s").strip().startswith("The user selected another conversation version")
    assert file.read_text() == "current"


def test_switching_and_rewinding_siblings_preserves_each_identity_and_name(store, monkeypatch):
    import asyncio
    from openprogram.interfaces.server.routes.chat_branches import post_chat_checkout
    from openprogram.interfaces.server.ws_actions.branch import handle_checkout_branch
    from tests.unit.store.test_session_branch_consistency import FakeWS
    _checkout_server(monkeypatch, store)
    session = Session(store, "s")
    session.turn_edits()
    session.turn_edits()
    store.set_branch_name("s", "a2", "A", name_locked=True, turns=7)
    _append(store, "s", "ub", "user", "a1")
    _append(store, "s", "ab", "assistant", "ub")
    store.set_branch_name("s", "ab", "B", name_locked=True)
    _apply("s", "u2")
    a_id = store.get_session("s")["active_branch_id"]
    _append(store, "s", "u4", "user", "a1")
    _append(store, "s", "a4", "assistant", "u4")
    store.set_head("s", "a4")
    # The record followed each message; the turn's end carry finds it there.
    store.carry_branch_entry("s", "a4")
    assert store.get_branch_meta("s", "a4")["name"] == "A"
    assert store.get_branch_meta("s", "a4")["turns"] == 7
    assert store.get_branch_meta("s", "a4")["name_locked"] is True
    ws = FakeWS()
    asyncio.run(handle_checkout_branch(ws, {"session_id": "s", "head_msg_id": "ab"}))
    b_id = store.get_session("s")["active_branch_id"]
    assert b_id != a_id
    _apply("s", "ub")
    state = store.get_session("s")
    assert state["active_branch_id"] == b_id
    assert state["branch_refs"][a_id]["head_id"] == "a4"
    assert state["branch_refs"][b_id]["head_id"] == "a1"
    assert asyncio.run(post_chat_checkout({"session_id": "s", "msg_id": "a4"})).status_code == 200
    assert store.get_session("s")["active_branch_id"] == a_id
    assert {b["name"] for b in store.list_branches("s")} == {"A", "B"}


@pytest.mark.parametrize("target", ["u1", "u2"])
def test_converged_rewinds_keep_selectable_identities_names_and_own_undo(store, monkeypatch, target):
    import asyncio
    from openprogram.interfaces.server.routes.chat_branches import post_chat_checkout
    from openprogram.runtime.conversation.rewind_marks import list_rewind_marks
    _checkout_server(monkeypatch, store)
    session = Session(store, "s")
    session.turn_edits()
    session.turn_edits()
    _append(store, "s", "ub", "user", "a1")
    _append(store, "s", "ab", "assistant", "ub")
    store.set_branch_name("s", "a2", "A", name_locked=True)
    store.set_branch_name("s", "ab", "B", name_locked=True)
    _apply("s", target)
    a_id = store.get_session("s")["active_branch_id"]
    assert asyncio.run(post_chat_checkout({"session_id": "s", "msg_id": "ab"})).status_code == 200
    _apply("s", "ub" if target == "u2" else "u1")
    b_id = store.get_session("s")["active_branch_id"]
    head = "a1" if target == "u2" else "ROOT"
    rows = store.list_branches("s")
    assert {(b["branch_id"], b["head_msg_id"], b["name"]) for b in rows} == {(a_id, head, "A"), (b_id, head, "B")}
    assert asyncio.run(post_chat_checkout({"session_id": "s", "msg_id": head, "branch_id": a_id})).status_code == 200
    assert store.get_session("s")["active_branch_id"] == a_id
    assert len(list_rewind_marks("s")) == 1
    assert undo_rewind("s")["status"] == "committed"
    assert store.get_session("s")["head_id"] == "a2"
    assert {(b["branch_id"], b["name"]) for b in store.list_branches("s")} == {(a_id, "A"), (b_id, "B")}


def test_preview_on_a_cannot_rewind_b(store, monkeypatch):
    import asyncio
    from openprogram.interfaces.server.routes.chat_branches import post_chat_checkout
    _checkout_server(monkeypatch, store)
    session = Session(store, 's'); session.turn_edits(); session.turn_edits()
    _append(store, 's', 'ub', 'user', 'a1'); _append(store, 's', 'ab', 'assistant', 'ub')
    store.set_branch_name('s', 'a2', 'A'); store.set_branch_name('s', 'ab', 'B')
    _apply('s', 'u2'); a = store.get_session('s')['active_branch_id']
    asyncio.run(post_chat_checkout({'session_id':'s', 'msg_id':'ab'}))
    _apply('s', 'ub'); b = store.get_session('s')['active_branch_id']
    asyncio.run(post_chat_checkout({'session_id':'s', 'msg_id':'a1', 'branch_id':a}))
    plan = plan_rewind('s', 'u1', mode='conversation')
    asyncio.run(post_chat_checkout({'session_id':'s', 'msg_id':'a1', 'branch_id':b}))
    result = rewind_to('s', 'u1', mode='conversation', idempotency_key=plan['idempotency_key'], expected_plan_hash=plan['plan_hash'])
    assert result['status'] == 'aborted', (result, store.get_session('s')['branch_refs'])


def test_rewind_cas_rejects_same_head_other_branch(store):
    from openprogram.state.store.session.rewind_recovery import cas_for_intent
    session = Session(store, "s")
    session.turn_edits()
    store.update_session("s", active_branch_id="B")
    intent = {"expected_head_id": "a1", "target_head_id": "ROOT",
              "source_branch_id": "A", "target_branch_id": "A",
              "expected_active_branch_id": "A"}
    assert not cas_for_intent(store, "s", intent, "a1", "ROOT")
    assert store.get_session("s")["head_id"] == "a1"
    assert store.get_session("s")["active_branch_id"] == "B"
