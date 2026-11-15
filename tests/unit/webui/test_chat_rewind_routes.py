"""Rewind to here: the button's two server steps.

/api/chat/rewind_plan names the files the replies after a user message
changed and whether they can be put back; /api/chat/rewind applies exactly
the plan the user confirmed. Edit never touches files.
"""
import asyncio
import json

from openprogram.interfaces.server.routes import chat_branches as routes


def _body(response):
    return json.loads(response.body)


def test_rewind_plan_reports_rows_and_skips_recovery_during_a_run(monkeypatch):
    from openprogram.interfaces.server import server
    from openprogram.runtime.conversation import rewind
    seen = {}
    monkeypatch.setattr(server, "_is_run_active", lambda sid: True)

    def plan(sid, msg, **kw):
        seen.update(kw)
        return {
            "status": "ready", "mode": kw["mode"],
            "files": [{"path": "/w/a.py", "action": "restore", "additions": 1, "deletions": 2}],
            "plan_hash": "sha256:p", "idempotency_key": "k",
        }
    monkeypatch.setattr(rewind, "plan_rewind", plan)
    response = asyncio.run(routes.post_chat_rewind_plan({"session_id": "s", "msg_id": "u", "mode": "code"}))
    assert seen == {"mode": "code", "recover": False}
    body = _body(response)
    assert body["files"][0]["action"] == "restore"
    assert body["plan_hash"] == "sha256:p"


def test_rewind_plan_rejects_an_unknown_mode():
    response = asyncio.run(routes.post_chat_rewind_plan({"session_id": "s", "msg_id": "u", "mode": "x"}))
    assert response.status_code == 400


def _reserve(monkeypatch, order, allowed=True, scopes=None):
    from openprogram.interfaces.server import server

    def reserve(sid, kind, owner, *, branch_id=None, scope="branch", key=None):
        order.append("reserve")
        if scopes is not None:
            scopes.append((kind, scope))
        return None if allowed else {"code": "run_active", "kind": "reply",
                                     "error": "A reply is being written on this branch; try again after it ends."}

    monkeypatch.setattr(server, "reserve_operation", reserve)
    monkeypatch.setattr(server, "_release_run_reservation", lambda sid, r: order.append("release"))


def test_rewind_applies_the_confirmed_plan_and_moves_head(monkeypatch):
    from openprogram.interfaces.server import server
    from openprogram.runtime.conversation import rewind, rewind_marks as marks
    order = []
    _reserve(monkeypatch, order)
    monkeypatch.setattr(marks, "list_rewind_marks", lambda sid: [])
    monkeypatch.setattr(server, "_set_active_head", lambda sid, head: order.append(("head", head)))
    monkeypatch.setattr(server, "refresh_context_stats", lambda sid: None)
    monkeypatch.setattr(rewind, "rewind_to", lambda sid, msg, **kw: order.append(("rewind", msg, kw)) or {
        "status": "committed", "head_changed": True, "new_head_id": "before-u",
        "turns_reverted": 1, "user_text": "make notes",
    })
    response = asyncio.run(routes.post_chat_rewind({
        "session_id": "s", "msg_id": "u", "plan_hash": "sha256:p", "idempotency_key": "k",
    }))
    assert response.status_code == 200
    body = _body(response)
    assert body["status"] == "committed" and body["new_head_id"] == "before-u"
    assert body["user_text"] == "make notes" and body["mode"] == "code_and_conversation"
    assert order == [
        "reserve",
        ("rewind", "u", {"idempotency_key": "k", "expected_plan_hash": "sha256:p", "mode": "code_and_conversation"}),
        ("head", "before-u"),
        "release",
    ]


def _repeat_a_rewind(monkeypatch, stored_head):
    """The same Rewind sent twice: the second finds the first's outcome."""
    from openprogram.interfaces.server import server
    from openprogram.runtime.context import session_db
    from openprogram.runtime.conversation import rewind, rewind_marks as marks

    class _Store:
        def get_session(self, session_id):
            return {"head_id": stored_head}

    moves: list = []
    answers = iter([False, True])
    _reserve(monkeypatch, [])
    monkeypatch.setattr(marks, "list_rewind_marks", lambda sid: [])
    monkeypatch.setattr(session_db, "default_db", lambda: _Store())
    monkeypatch.setattr(server, "_set_active_head", lambda sid, head: moves.append(head))
    monkeypatch.setattr(server, "refresh_context_stats", lambda sid: moves.append("stats"))
    monkeypatch.setattr(rewind, "rewind_to", lambda sid, msg, **kw: {
        "status": "committed", "head_changed": True, "new_head_id": "before-u",
        "turns_reverted": 1, "user_text": "make notes", "replayed": next(answers),
    })
    request = {"session_id": "s", "msg_id": "u", "plan_hash": "sha256:p", "idempotency_key": "k"}
    first = asyncio.run(routes.post_chat_rewind(dict(request)))
    del moves[:]
    second = asyncio.run(routes.post_chat_rewind(dict(request)))
    return first, second, moves


def test_a_repeated_rewind_answers_as_the_first_did(monkeypatch):
    first, second, moves = _repeat_a_rewind(monkeypatch, stored_head="before-u")
    assert first.status_code == second.status_code == 200
    assert _body(second) == _body(first) and _body(first)["head_changed"] is True
    # The head is still where the rewind left it: mirroring it again changes nothing.
    assert moves == ["before-u", "stats"]


def test_a_repeated_rewind_does_not_pull_back_a_conversation_that_moved_on(monkeypatch):
    first, second, moves = _repeat_a_rewind(monkeypatch, stored_head="a-later-reply")
    assert _body(second) == _body(first)
    assert moves == []


def test_a_stale_plan_rewinds_nothing(monkeypatch):
    from openprogram.interfaces.server import server
    from openprogram.runtime.conversation import rewind
    _reserve(monkeypatch, [])
    monkeypatch.setattr(server, "_set_active_head", lambda *a: (_ for _ in ()).throw(AssertionError("no head move")))
    monkeypatch.setattr(rewind, "rewind_to", lambda *a, **kw: {"status": "aborted", "error": "stale_plan"})
    response = asyncio.run(routes.post_chat_rewind({
        "session_id": "s", "msg_id": "u", "plan_hash": "sha256:old", "idempotency_key": "k",
    }))
    assert response.status_code == 409
    assert "Rewind again" in _body(response)["error"]


def test_rewind_waits_for_a_running_turn(monkeypatch):
    order = []
    _reserve(monkeypatch, order, allowed=False)
    response = asyncio.run(routes.post_chat_rewind({
        "session_id": "s", "msg_id": "u", "plan_hash": "sha256:p", "idempotency_key": "k",
    }))
    assert response.status_code == 409
    assert order == ["reserve"]
    body = _body(response)
    assert (body["code"], body["kind"]) == ("run_active", "reply")


def test_only_a_rewind_that_restores_files_holds_the_whole_conversation(monkeypatch):
    from openprogram.interfaces.server import server
    from openprogram.runtime.conversation import rewind, rewind_marks as marks
    scopes: list = []
    _reserve(monkeypatch, [], scopes=scopes)
    monkeypatch.setattr(marks, "list_rewind_marks", lambda sid: [])
    monkeypatch.setattr(server, "_set_active_head", lambda *a: None)
    monkeypatch.setattr(rewind, "rewind_to", lambda *a, **kw: {"status": "committed"})
    for mode in ("code_and_conversation", "code", "conversation"):
        asyncio.run(routes.post_chat_rewind({
            "session_id": "s", "msg_id": "u", "plan_hash": "p", "idempotency_key": "k", "mode": mode,
        }))
    assert scopes == [("rewind", "conversation"), ("rewind", "conversation"), ("rewind", "branch")]


def test_a_refused_file_rewind_names_the_branch_to_stop(monkeypatch):
    from openprogram.interfaces.server import server
    blocking = {"branch_id": "branch_b", "execution_id": "exec-b", "status_version": 4,
                "op_kind": "reply", "branch_name": "nightly"}
    monkeypatch.setattr(server, "reserve_operation", lambda *a, **kw: {
        "code": "branch_running", "kind": "reply", "blocking": blocking,
        "error": "Branch “nightly” is running. Rewinding files now could undo its work; stop it, then rewind.",
    })
    response = asyncio.run(routes.post_chat_rewind({
        "session_id": "s", "msg_id": "u", "plan_hash": "p", "idempotency_key": "k",
    }))
    assert response.status_code == 409
    body = _body(response)
    assert body["code"] == "branch_running" and body["blocking"] == blocking
    assert "nightly" in body["error"]


def test_undo_reserves_the_run_and_moves_head(monkeypatch):
    from openprogram.interfaces.server import server
    from openprogram.runtime.conversation import rewind_marks as marks
    order = []
    _reserve(monkeypatch, order)
    monkeypatch.setattr(server, "_set_active_head", lambda sid, head: order.append(head))
    monkeypatch.setattr(server, "refresh_context_stats", lambda sid: None)
    monkeypatch.setattr(marks, "undo_rewind", lambda sid, **kw: {
        "status": "committed", "head_changed": True, "new_head_id": "tail",
    })
    response = asyncio.run(routes.post_chat_rewind_undo({"session_id": "s"}))
    assert response.status_code == 200
    assert order == ["reserve", "tail", "release"]


def test_rewind_needs_the_plan_it_was_shown():
    response = asyncio.run(routes.post_chat_rewind({"session_id": "s", "msg_id": "u", "plan_hash": "x"}))
    assert response.status_code == 400


def test_edit_leaves_files_alone(monkeypatch):
    from openprogram.runtime.conversation import rewind
    monkeypatch.setattr(rewind, "rewind_to", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no rewind")))
    monkeypatch.setattr(routes, "_fork_user_turn_and_run", lambda *a, **kw: {"msg_id": "new"})
    response = asyncio.run(routes.post_chat_edit({
        "session_id": "s", "msg_id": "u", "content": "edited",
        "restore_files": {"plan_hash": "sha256:p", "idempotency_key": "k"},
    }))
    assert response.status_code == 200
