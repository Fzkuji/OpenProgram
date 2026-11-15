"""REST routes for ContextGit chat operations — retry / edit / checkout /
copy into a new session.

Kept separate from ``server.py`` so that:

  * server.py doesn't keep growing past 3k lines
  * the DAG-editing endpoints are in one place with their shared fork
    helper, not scattered alongside unrelated routes
  * tests can register just this router against a minimal FastAPI app

These routes reach back into server.py for the live conversation dict
and the run-active predicate via lazy imports (see the handlers below).
That avoids an import cycle while still letting the routes sit in their
own module. Globals like ``_sessions`` aren't moved out of
server.py because doing so would touch every other site that uses them,
and this refactor is scoped to the ContextGit surface.

See docs/reference/design/context/overview.html for semantics (retry = fork with same
content, edit = fork with new content, checkout = pure HEAD move).
"""
from __future__ import annotations

import logging
import threading
import uuid
import json

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

_log = logging.getLogger(__name__)


router = APIRouter()


def is_checkout_target(node) -> bool:
    """A HEAD/checkout/fork target is a CHAIN-level turn. On disk those
    carry ``caller`` of "ROOT" (user turns, ROOT-hung code records) or
    "" (reply nodes); a node whose caller is another call lives inside
    an Agent method's execution subtree and is not a conversation
    branch. Mirrored by ``_isChainTurn`` in dag/render/inspector.ts."""
    return getattr(node, "caller", None) in (None, "", "ROOT")


def _fork_user_turn_and_run(
    session_id: str, pivot_id: str, new_content: str | None,
    attachments: list[dict] | None = None,
) -> dict:
    """Shared engine for retry / edit.

    Finds the nearest user-message ancestor of ``pivot_id``, creates a
    sibling user message at the same position in the DAG (same
    ``predecessor``), sets that as HEAD, and kicks off execution. The
    old turn + its assistant subtree stay reachable as a sibling
    branch.

    ``new_content=None`` → retry (reuse the original content).
    A string → edit (use the new content).

    Returns a dict that the caller JSON-encodes. Errors are signalled
    via the ``__error__`` key so the caller can produce the right
    status code without raising.
    """
    from openprogram.interfaces.server import server as _srv  # lazy — avoids circular import

    # Reject while the selected branch runs. The UI also greys the
    # buttons, but defense in depth: forking mid-run would orphan the
    # in-flight assistant reply against a HEAD that's about to move. A
    # turn on another branch keeps running beside the retry.
    # A turn that has already sent its terminal frame is only finishing up:
    # an edit or retry fired on seeing that frame waits for the slot to be
    # given back, as a send does.
    from openprogram.interfaces.server.server_runtime.session_lifecycle import (
        selected_run_blocks,
    )
    if selected_run_blocks(session_id):
        return {"__error__": (
            "a run is currently active — wait for it to finish or stop it first",
            409,
        )}

    with _srv._sessions_lock:
        conv = _srv._sessions.get(session_id)
        if conv is None:
            return {"__error__": ("unknown conv", 404)}
        # conv["messages"] only holds the current head's linear chain.
        # Retry/edit commonly target a sibling that is by definition
        # off-chain, so look up against the full SessionDB DAG.
        from openprogram.runtime.context.session_db import default_db as _db_for_retry
        msgs = _db_for_retry().get_messages(session_id)
        # Which message the new turn sends is decided in the runtime, so
        # every surface (``--print`` too) forks the same way.
        from openprogram.runtime.conversation.branch_fork import ForkError, plan_fork
        from openprogram.runtime.permissions.authority import local_owner_authority
        try:
            plan = plan_fork(msgs, pivot_id, new_content, authority=local_owner_authority())
        except ForkError as exc:
            return {"__error__": (str(exc), exc.status)}
        src_user, new_user, new_msg_id = plan.source, plan.message, plan.msg_id
        has_assistant_child = plan.new_branch and new_content is None

    # Reserve the selected branch before any DAG/HEAD mutation. Admission
    # and persistence both remain inside this ownership window.
    refusal = _srv.reserve_operation(
        session_id, "edit" if new_content is not None else "retry", new_msg_id,
    )
    if refusal is not None:
        return {"__error__": (refusal["error"], 409), "code": refusal.get("code"),
                "kind": refusal.get("kind")}

    # Retry/edit uses the same durable Agent admission as WS chat. The
    # forked message id is transport/DAG provenance only; execution_id is
    # minted by the canonical store and returned after admission.
    try:
        from openprogram.runtime.permissions.authority import local_owner_authority
        from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
        from openprogram.runtime.conversation.turn_input import build_turn_request

        adapter = CanonicalAgentAdapter(
            event_sink=lambda env: _srv._broadcast(json.dumps(env, default=str)),
        )
        from openprogram.runtime.conversation.session_config import load_session_run_config

        dispatch_attachments = None
        if attachments:
            from openprogram.interfaces.server.ws_actions.chat import _persist_attachments, _attachments_for_dispatch
            new_user["content"] = _persist_attachments(
                session_id, attachments, str(new_user.get("content") or ""),
            )
            dispatch_attachments = _attachments_for_dispatch(attachments)
            if dispatch_attachments:
                # Kept so later requests send them again, after the text
                # (``store.session.media``).
                from openprogram.state.store.session.media import records_for_attachments
                media = records_for_attachments(
                    _db_for_retry()._session_dir(session_id), dispatch_attachments, 1)  # noqa: SLF001
                if media:
                    new_user["media"] = media
        elif new_content is None and new_user.get("media"):
            # A retry sends the pictures its message sent.
            from openprogram.state.store.session.media import attachments_from_records
            dispatch_attachments = attachments_from_records(
                new_user["media"], _db_for_retry()._session_dir(session_id)) or None  # noqa: SLF001
        authority = local_owner_authority()
        # A retry or an edit makes a new branch that starts with a copy of
        # the settings the branch it leaves from has now (model, effort,
        # permission, Sandbox, Agent), never the settings the old turn ran
        # with: a permission tightened since then stays tightened.
        run_config = load_session_run_config(session_id)
        from openprogram.state.store.session.branch_config import branch_identity
        branch_fields = {}
        if new_content is not None or has_assistant_child:
            branch_fields = {"branch_id": branch_identity(session_id, new_msg_id),
                             "branch_from": src_user.get("predecessor") or "ROOT"}
        request = build_turn_request(
            session_id,
            str(new_user.get("model_input") or new_user.get("content") or ""),
            # The Agent, model, effort, Fast, tools and permission are the
            # branch's the retry starts from, filled at admission
            # (freeze_branch_request); a new fork copies them once.
            agent_id=None,
            source="web",
            msg_id=new_msg_id,
            authority=authority,
            permission={"permission_mode": None},
            run_config=("additional_working_dirs",),
            run_cfg=run_config,
            user_already_persisted=True,
            attachments=dispatch_attachments,
            **branch_fields,
        )
        admission = adapter.admit(
            request,
            trusted_actor=authority,
            user_message_id=new_msg_id,
            assistant_message_id=f"{new_msg_id}_reply",
            config_snapshot_ref=f"session:{session_id}",
        )
    except Exception as exc:
        _srv._release_run_reservation(session_id, new_msg_id)
        return {"__error__": (f"retry admission failed: {type(exc).__name__}: {exc}", 500)}

    try:
        if new_content is not None or has_assistant_child:
            _srv._append_msg(conv, new_user, strict=True)
        _srv._save_session(session_id)
    except BaseException as exc:
        adapter.fail_admission(admission, reason_code="user_persist_failed", failure_text=f"{type(exc).__name__}: {exc}")
        _srv._release_run_reservation(session_id, new_msg_id)
        return {"__error__": (f"retry persistence failed: {type(exc).__name__}: {exc}", 500)}

    with _srv._running_tasks_lock:
        task = _srv._running_tasks.get(session_id)
        if task and task.get("msg_id") == new_msg_id:
            task["execution_id"] = admission.execution_id
            task["status_version"] = admission.status_version

    def _run_canonical():
        def _publish_activation(active):
            with _srv._running_tasks_lock:
                task = _srv._running_tasks.get(session_id)
                if task and task.get("execution_id") == active.admission.execution_id:
                    task["status_version"] = active.status_version
            _srv._emit_running_task_event(session_id)

        async def _activate():
            activated = await adapter.activate(
                admission, on_activated=_publish_activation,
            )
            # ``None``: a stop reached the retry before it ran; no result.
            return activated[1] if activated is not None else None

        import asyncio
        try:
            asyncio.run(_activate())
        finally:
            if _srv._finish_owned_run(session_id, new_msg_id):
                _srv._emit_running_task_event(
                    session_id,
                    cleared_msg_id=new_msg_id,
                    cleared_execution_id=admission.execution_id,
                )

    try:
        worker = threading.Thread(target=_run_canonical, args=(), kwargs={}, daemon=True)
    except BaseException as exc:
        adapter.fail_admission(admission, reason_code="agent_runner_error", failure_text=f"{type(exc).__name__}: {exc}")
        _srv._release_run_reservation(session_id, new_msg_id)
        _srv._emit_running_task_event(
            session_id,
            cleared_msg_id=new_msg_id,
            cleared_execution_id=admission.execution_id,
        )
        return {"__error__": (f"retry activation failed: {type(exc).__name__}: {exc}", 500)}
    with _srv._running_tasks_lock:
        task = _srv._running_tasks.get(session_id)
        if task and task.get("msg_id") == new_msg_id:
            task["execution_id"] = admission.execution_id
            task["status_version"] = admission.status_version
    if not _srv._activate_run_reservation(session_id, new_msg_id, worker):
        adapter.fail_admission(
            admission, reason_code="agent_runner_error",
            failure_text="retry execution reservation was lost before startup",
        )
        _srv._release_run_reservation(session_id, new_msg_id)
        _srv._emit_running_task_event(
            session_id,
            cleared_msg_id=new_msg_id,
            cleared_execution_id=admission.execution_id,
        )
        return {"__error__": ("retry execution reservation was lost before startup", 500)}
    _srv._emit_running_task_event(session_id)
    try:
        worker.start()
    except BaseException as exc:
        adapter.fail_admission(admission, reason_code="agent_runner_error", failure_text=f"{type(exc).__name__}: {exc}")
        if _srv._finish_owned_run(session_id, new_msg_id):
            _srv._emit_running_task_event(
                session_id,
                cleared_msg_id=new_msg_id,
                cleared_execution_id=admission.execution_id,
            )
        _srv._release_run_reservation(session_id, new_msg_id)
        return {"__error__": (f"retry activation failed: {type(exc).__name__}: {exc}", 500)}

    return {
        "session_id": session_id,
        "msg_id": new_msg_id,
        "forked_from": src_user.get("id"),
        "execution_id": admission.execution_id,
    }


@router.post("/api/chat/retry")
async def post_chat_retry(body: dict = None):
    """Retry the user turn at or above ``msg_id``.

    Non-destructive: forks a sibling user message with the SAME content,
    runs it, sets HEAD to the new turn. Old turn + assistant subtree
    stay in the DAG, reachable via ``< N / M >``.
    """
    if body is None:
        return JSONResponse(content={"error": "no body"}, status_code=400)
    session_id = body.get("session_id")
    pivot_id = body.get("msg_id")
    if not session_id or not pivot_id:
        return JSONResponse(
            content={"error": "session_id and msg_id required"}, status_code=400,
        )
    # Same blocking store and admission work as edit; keep it off the loop.
    result = await run_in_threadpool(
        _fork_user_turn_and_run, session_id, pivot_id, new_content=None,
    )
    if "__error__" in result:
        msg, code = result["__error__"]
        return JSONResponse(content={"error": msg, **{key: result[key] for key in ("code", "kind")
                                                      if result.get(key)}}, status_code=code)
    return JSONResponse(content=result)


REWIND_MODES = ("code_and_conversation", "conversation", "code")


def _rewind_plan(session_id: str, msg_id: str, mode: str) -> dict:
    """The dry run behind the Rewind dialog. Writes nothing, and leaves crash
    recovery alone while a run owns the session."""
    from openprogram.interfaces.server import server as _srv
    from openprogram.runtime.conversation.rewind import plan_rewind

    plan = plan_rewind(
        session_id, msg_id, mode=mode, recover=not _srv._is_run_active(session_id),
    )
    return {key: plan.get(key) for key in (
        "status", "mode", "files", "summary", "has_changes", "skipped",
        "code_available", "code_unavailable_reason", "checkpoint", "conflicts",
        "unavailable", "plan_hash", "plan_hashes", "idempotency_key", "user_text",
        "attachments", "linked_impacts", "turns_reverted", "error", "error_code",
    )}


def _with_reserved_run(session_id: str, action, *, scope: str = "branch",
                       key: str | None = None) -> dict:
    """Run a rewind as the selected branch's foreground operation.

    ``scope="conversation"`` for a rewind that restores files: refused while
    another branch runs or waits on an approval, with ``blocking`` naming
    that branch and the ids a Stop of it needs. A repeat carrying the
    ``key`` of the rewind that holds the slot waits for it and then returns
    its outcome (``reserve_operation``)."""
    from openprogram.interfaces.server import server as _srv

    reservation = f"rewind_{uuid.uuid4().hex}"
    refusal = _srv.reserve_operation(session_id, "rewind", reservation, scope=scope, key=key)
    if refusal is not None:
        return {"__error__": (refusal["error"], 409),
                **{key: refusal[key] for key in ("code", "kind", "blocking") if key in refusal}}
    try:
        return action()
    finally:
        _srv._release_run_reservation(session_id, reservation)


def _apply_rewind(
    session_id: str, msg_id: str, plan_hash: str, idempotency_key: str, mode: str,
) -> dict:
    """Do exactly the confirmed plan on the current branch; recovery history
    is not exposed as a sibling when the user continues."""
    from openprogram.interfaces.server import server as _srv
    from openprogram.runtime.conversation.rewind import rewind_to
    from openprogram.runtime.conversation.rewind_marks import list_rewind_marks

    def apply() -> dict:
        result = rewind_to(
            session_id, msg_id,
            idempotency_key=idempotency_key,
            expected_plan_hash=plan_hash,
            mode=mode,
        )
        if result.get("status") != "committed":
            if result.get("error") == "stale_plan":
                return {"__error__": ("Files changed since you asked to rewind. Rewind again.", 409)}
            return {
                "__error__": (result.get("error") or "Could not restore the files", 409),
                "status": result.get("status"),
            }
        mirror_rewound_head(_srv, session_id, result)
        result["marks"] = list_rewind_marks(session_id)
        return result

    from openprogram.interfaces.server.ws_actions.chat.rewind import (
        mirror_rewound_head, rewind_scope, rewind_slot_key,
    )
    return _with_reserved_run(session_id, apply, scope=rewind_scope(mode),
                              key=rewind_slot_key(idempotency_key))


def _switch_rewind(session_id: str, edge_id, expected_head_id, undo: bool) -> dict:
    from openprogram.interfaces.server import server as _srv
    from openprogram.runtime.conversation.rewind_marks import redo_rewind, undo_rewind

    def apply() -> dict:
        result = (undo_rewind if undo else redo_rewind)(
            session_id, edge_id=edge_id, expected_head_id=expected_head_id,
        )
        if result.get("head_changed"):
            _srv._set_active_head(session_id, result.get("new_head_id"))
            _srv.refresh_context_stats(session_id)
        return result

    return _with_reserved_run(session_id, apply)


@router.post("/api/chat/rewind_plan")
async def post_chat_rewind_plan(body: dict = None):
    """What rewinding to a user message would do, for the Rewind dialog."""
    body = body or {}
    session_id, msg_id = body.get("session_id"), body.get("msg_id")
    mode = body.get("mode") or "code_and_conversation"
    if not session_id or not msg_id:
        return JSONResponse(content={"error": "session_id and msg_id required"}, status_code=400)
    if mode not in REWIND_MODES:
        return JSONResponse(content={"error": f"unknown mode {mode!r}"}, status_code=400)
    return JSONResponse(content=await run_in_threadpool(_rewind_plan, session_id, msg_id, mode))


@router.post("/api/chat/rewind_points")
async def post_chat_rewind_points(body: dict = None):
    """Every message the conversation can rewind to, newest first."""
    from openprogram.interfaces.server import server as _srv
    from openprogram.runtime.conversation.rewind import list_rewind_points

    session_id = (body or {}).get("session_id")
    if not session_id:
        return JSONResponse(content={"error": "session_id required"}, status_code=400)
    points = await run_in_threadpool(
        # Whether a turn runs is read under the conversation's lock too.
        lambda: list_rewind_points(session_id, None, recover=not _srv._is_run_active(session_id)),
    )
    return JSONResponse(content={"session_id": session_id, "points": points})


@router.post("/api/chat/rewind_marks")
async def post_chat_rewind_marks(body: dict = None):
    """Rewound here / Rewind undone rows for the transcript at the current HEAD."""
    from openprogram.runtime.conversation.rewind_marks import list_rewind_marks

    session_id = (body or {}).get("session_id")
    if not session_id:
        return JSONResponse(content={"error": "session_id required"}, status_code=400)
    marks = await run_in_threadpool(list_rewind_marks, session_id)
    return JSONResponse(content={"session_id": session_id, "marks": marks})


@router.post("/api/chat/rewind")
async def post_chat_rewind(body: dict = None):
    """Apply the plan /api/chat/rewind_plan showed, in the chosen mode."""
    body = body or {}
    session_id, msg_id = body.get("session_id"), body.get("msg_id")
    plan_hash, idempotency_key = body.get("plan_hash"), body.get("idempotency_key")
    mode = body.get("mode") or "code_and_conversation"
    if not all(isinstance(v, str) and v for v in (session_id, msg_id, plan_hash, idempotency_key)):
        return JSONResponse(
            content={"error": "session_id, msg_id, plan_hash and idempotency_key required"},
            status_code=400,
        )
    if mode not in REWIND_MODES:
        return JSONResponse(content={"error": f"unknown mode {mode!r}"}, status_code=400)
    result = await run_in_threadpool(_apply_rewind, session_id, msg_id, plan_hash, idempotency_key, mode)
    if "__error__" in result:
        message, code = result["__error__"]
        # A refusal names what runs (``kind``) and, for a rewind that
        # restores files, the branch in the way with the ids to stop it.
        return JSONResponse(content={
            "error": message, "status": result.get("status"),
            **{key: result[key] for key in ("code", "kind", "blocking") if key in result},
        }, status_code=code)
    return JSONResponse(content={
        "status": result.get("status"),
        "mode": result.get("mode") or mode,
        "new_head_id": result.get("new_head_id"),
        "head_changed": bool(result.get("head_changed")),
        "turns_reverted": result.get("turns_reverted", 0),
        "user_text": result.get("user_text", ""),
        "restored_paths": result.get("total_restored_paths") or [],
        "skipped": result.get("skipped") or [],
        "marks": result.get("marks") or [],
        "errors": [str(error) for error in result.get("errors") or []],
    })


async def _switch_response(body: dict | None, undo: bool):
    body = body or {}
    session_id = body.get("session_id")
    if not session_id:
        return JSONResponse(content={"error": "session_id required"}, status_code=400)
    result = await run_in_threadpool(
        _switch_rewind, session_id, body.get("edge_id"), body.get("expected_head_id"), undo,
    )
    if "__error__" in result:
        message, code = result["__error__"]
        return JSONResponse(content={"error": message}, status_code=code)
    return JSONResponse(content=result, status_code=200 if result.get("status") == "committed" else 409)


@router.post("/api/chat/rewind_undo")
async def post_chat_rewind_undo(body: dict = None):
    """Bring back the messages a rewind left. Files stay as they are."""
    return await _switch_response(body, undo=True)


@router.post("/api/chat/rewind_redo")
async def post_chat_rewind_redo(body: dict = None):
    """Go back to the rewound conversation. Files stay as they are."""
    return await _switch_response(body, undo=False)


@router.post("/api/chat/edit")
async def post_chat_edit(body: dict = None):
    """Edit a user message: fork with new content and re-run.

    Same non-destructive behavior as retry — the old turn stays
    accessible as a sibling. Difference: the new sibling's content is
    whatever the user typed in the edit box.
    """
    if body is None:
        return JSONResponse(content={"error": "no body"}, status_code=400)
    session_id = body.get("session_id")
    pivot_id = body.get("msg_id")
    new_content = body.get("content")
    if not session_id or not pivot_id or new_content is None:
        return JSONResponse(
            content={"error": "session_id, msg_id, content required"},
            status_code=400,
        )
    attachments = body.get("attachments")
    if attachments is not None and (
        not isinstance(attachments, list) or len(attachments) > 20
        or any(not isinstance(item, dict) or not isinstance(item.get("data"), str)
               or item.get("type") not in ("image", "document") for item in attachments)
    ):
        return JSONResponse(content={"error": "Invalid attachment payload (maximum 20 files)"}, status_code=400)
    if not isinstance(new_content, str) or (not new_content.strip() and not attachments):
        return JSONResponse(content={"error": "Message content or attachments required"}, status_code=400)
    # File persistence and admission perform blocking disk IO. Keep the event
    # loop available for other sessions while the reservation owns this edit.
    result = await run_in_threadpool(
        _fork_user_turn_and_run, session_id, pivot_id,
        new_content=new_content, attachments=attachments,
    )
    if "__error__" in result:
        msg, code = result["__error__"]
        return JSONResponse(content={"error": msg}, status_code=code)
    return JSONResponse(content=result)


@router.post("/api/chat/checkout")
async def post_chat_checkout(body: dict = None):
    """Move the conversation HEAD to a specific commit.

    Pure display op — nothing re-executes and no file changes. The UI
    re-renders the linear history from the new HEAD back to root. Used by
    ``< N / M >`` navigation to switch between sibling versions and by the
    graph view's checkout; a message sent next attaches under the new HEAD.

    Switching is a view change: it is never refused because a turn or an
    operation runs on the branch left or the branch entered. Each keeps
    running on its own branch (``switch_branch``). The response carries
    ``previous_head_id``, the HEAD before the move, so a caller can move
    back, and ``active_branch_id``, the branch now selected.
    """
    from openprogram.interfaces.server import server as _srv

    if body is None:
        return JSONResponse(content={"error": "no body"}, status_code=400)
    session_id = body.get("session_id")
    target_id = body.get("msg_id")
    if not session_id or not target_id:
        return JSONResponse(
            content={"error": "session_id and msg_id required"}, status_code=400,
        )
    return await run_in_threadpool(_checkout, _srv, session_id, target_id, body)


def _checkout(_srv, session_id: str, target_id: str, body: dict):
    """Select the branch ending at ``target_id``."""
    from openprogram.runtime.context.session_db import default_db
    db = default_db()
    # Validate target_id against the FULL DAG, not the current linear
    # chain — that's the whole point of checkout. Looking it up in
    # conv["messages"] (which only holds the head's chain) means
    # off-branch clicks always 404'd.
    if not db.message_exists(session_id, target_id):
        return JSONResponse(content={"error": "unknown msg"}, status_code=404)
    # Reject checkout to function-internal nodes — a node whose
    # ``caller`` is another call lives inside an Agent method's
    # execution subtree (LLM exec rows, nested code calls). Those are
    # not conversation branches; switching HEAD into one yields a
    # nonsense transcript mixing internal exec output with the
    # user-visible reply. Chain-level turns carry ``caller`` of "ROOT"
    # (user turns, ROOT-hung code records) or "" (reply nodes) — on
    # disk: 0001-u caller='ROOT' pred='ROOT', 0002-l caller=''
    # pred=<user>. The old gate keyed on ``predecessor`` — the CHAIN
    # edge since the parent/called_by rename — and so rejected every
    # turn except the first.
    _node = None
    try:
        for _n in db.get_nodes(session_id):
            if _n.id == target_id:
                _node = _n
                break
    except Exception:
        _node = None
    if _node is not None and not is_checkout_target(_node):
        return JSONResponse(
            content={"error": "function-internal node is not a checkout target"},
            status_code=400,
        )
    session = db.get_session(session_id) or {}
    hidden = set(session.get("rewind_hidden_nodes") or []) | set(session.get("rewind_hidden_heads") or [])
    if target_id in hidden:
        return JSONResponse(content={"error": "This version was withdrawn by Rewind; use Undo rewind to recover it."}, status_code=409)
    switched = _srv.switch_branch(session_id, target_id, branch_id=body.get("branch_id"))
    if not switched["ok"]:
        return JSONResponse(content={"error": switched["error"], "code": switched["code"]}, status_code=409)
    _srv._save_session(session_id)
    # HEAD moved to another chain, so the context the next request carries
    # is a different set of nodes — re-estimate rather than keep showing the
    # measurement taken on the branch we left.
    _srv.refresh_context_stats(session_id)
    return JSONResponse(content={
        "session_id": session_id,
        "head_id": target_id,
        "previous_head_id": switched["previous_head_id"],
        "active_branch_id": switched["active_branch_id"],
    })


@router.post("/api/chat/branch_session")
async def post_chat_branch_session(body: dict = None):
    """Copy a conversation into a NEW session up to ``msg_id``.

    Body ``{"session_id", "msg_id"?, "title"?}``; without ``msg_id`` the
    copy ends at HEAD, or at the last finished turn while one runs ("Copy
    conversation" in the ⋯ menu). Unlike checkout this never touches the
    source session and works while a turn runs there.
    Responds with the ``branch_session_result`` data:
    ``{"session_id", "msg_id", "ok", "new_session_id", "error"?, "code"?}``
    — 200 on success, 404 for an unknown session or message, 400 for a
    message that is not a conversation turn or is still being written.
    """
    from openprogram.interfaces.server.ws_actions.branch import run_branch_session

    body = body or {}
    data = await run_in_threadpool(
        run_branch_session, body.get("session_id"), body.get("msg_id"), body.get("title"), body.get("branch_id"),
    )
    if data.get("ok"):
        return JSONResponse(content=data)
    status = {
        "unknown_session": 404,
        "unknown_message": 404,
        "internal": 500,
        "broken_chain": 500,
    }.get(data.get("code") or "", 400)
    return JSONResponse(content=data, status_code=status)
