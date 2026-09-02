"""REST routes for ContextGit chat operations — retry / edit / checkout.

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

See docs/reference/design/context/overview.md for semantics (retry = fork with same
content, edit = fork with new content, checkout = pure HEAD move).
"""
from __future__ import annotations

import threading
import time
import uuid
import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse


router = APIRouter()


def is_checkout_target(node) -> bool:
    """A HEAD/checkout/fork target is a CHAIN-level turn. On disk those
    carry ``caller`` of "ROOT" (user turns, ROOT-hung code records) or
    "" (reply nodes); a node whose caller is another call lives inside
    an @agentic_function's execution subtree and is not a conversation
    branch. Mirrored by ``_isChainTurn`` in dag/render/inspector.ts."""
    return getattr(node, "caller", None) in (None, "", "ROOT")


def _fork_user_turn_and_run(session_id: str, pivot_id: str, new_content: str | None) -> dict:
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
    from . import server as _srv  # lazy — avoids circular import

    # Reject while a run is active. The UI also greys the buttons, but
    # defense in depth: forking mid-run would orphan the in-flight
    # assistant reply against a HEAD that's about to move.
    if _srv._is_run_active(session_id):
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
        from openprogram.agent.session_db import default_db as _db_for_retry
        msgs = _db_for_retry().get_messages(session_id)
        pivot = next((m for m in msgs if m.get("id") == pivot_id), None)
        if pivot is None:
            return {"__error__": ("unknown msg", 404)}

        # Walk up to the nearest user message. For retry clicked on
        # an assistant reply, that's the user turn above it.
        by_id = {m.get("id"): m for m in msgs}
        cur = pivot
        while cur is not None and cur.get("role") != "user":
            cur = by_id.get(cur.get("predecessor"))
        if cur is None:
            return {"__error__": ("no user message to fork from", 400)}
        src_user = cur

        # If retry (not edit) and the previous turn never produced an
        # assistant reply (timed out / errored / was force-stopped),
        # rerun the SAME user message instead of forking a sibling. The
        # "old branch" doesn't really exist — there's nothing on it but
        # the user's question — so forking just litters the DAG with
        # empty placeholder branches. Edit always forks because the
        # user explicitly changed the prompt.
        has_assistant_child = any(
            m.get("predecessor") == src_user.get("id") and m.get("role") == "assistant"
            for m in msgs
        )
        if new_content is None and not has_assistant_child:
            new_msg_id = src_user.get("id")
            new_user = src_user
        else:
            new_msg_id = str(uuid.uuid4())[:8]
            new_user = {
                "role": "user",
                "id": new_msg_id,
                "content": new_content if new_content is not None
                           else src_user.get("content", ""),
                "timestamp": time.time(),
                # Sibling of src_user: same parent. A first-turn retry
                # forks at ROOT explicitly — a ROOT-level node without
                # a predecessor is rejected by the store (Decision 1).
                "predecessor": src_user.get("predecessor") or "ROOT",
                # Lineage breadcrumbs (future tooling / debugging).
                "forked_from": src_user.get("id"),
            }
            from openprogram.agent.authority import local_owner_authority
            new_user.update(local_owner_authority())
            if src_user.get("display"):
                new_user["display"] = src_user["display"]
            if new_content is not None:
                new_user["edit_of"] = src_user.get("id")

    # Reserve before any DAG/HEAD mutation. Admission and persistence both
    # remain inside this ownership window.
    if not _srv._try_reserve_run(session_id, new_msg_id):
        return {"__error__": (
            "a run is currently active — wait for it to finish or stop it first",
            409,
        )}

    # Retry/edit uses the same durable Agent admission as WS chat. The
    # forked message id is transport/DAG provenance only; execution_id is
    # minted by the canonical store and returned after admission.
    try:
        from openprogram.agent.authority import local_owner_authority
        from openprogram.agent.production_driver import CanonicalAgentAdapter
        from openprogram.agent.dispatcher.types import TurnRequest

        adapter = CanonicalAgentAdapter(
            event_sink=lambda env: _srv._broadcast(json.dumps(env, default=str)),
        )
        request = TurnRequest(
            session_id=session_id,
            user_text=str(new_user.get("content") or ""),
            agent_id=(new_user.get("agent_id") or "main"),
            source="web",
            user_msg_id=new_msg_id,
            user_already_persisted=True,
        )
        admission = adapter.admit(
            request,
            trusted_actor=local_owner_authority(),
            user_message_id=new_msg_id,
            assistant_message_id=None,
            config_snapshot_ref=f"session:{session_id}",
        )
    except Exception as exc:
        _srv._release_run_reservation(session_id, new_msg_id)
        return {"__error__": (f"retry admission failed: {type(exc).__name__}: {exc}", 500)}

    try:
        if new_content is not None or has_assistant_child:
            _srv._append_msg(conv, new_user)
        _srv._save_session(session_id)
    except BaseException as exc:
        adapter.fail_admission(admission, reason_code="user_persist_failed")
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
            _active, result = await adapter.activate(
                admission, on_activated=_publish_activation,
            )
            return result

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
        adapter.fail_admission(admission, reason_code="agent_runner_error")
        _srv._release_run_reservation(session_id, new_msg_id)
        try:
            _srv._emit_running_task_event(
                session_id,
                cleared_msg_id=new_msg_id,
                cleared_execution_id=admission.execution_id,
            )
        except Exception:
            pass
        return {"__error__": (f"retry activation failed: {type(exc).__name__}: {exc}", 500)}
    with _srv._running_tasks_lock:
        task = _srv._running_tasks.get(session_id)
        if task and task.get("msg_id") == new_msg_id:
            task["execution_id"] = admission.execution_id
            task["status_version"] = admission.status_version
    if not _srv._activate_run_reservation(session_id, new_msg_id, worker):
        adapter.fail_admission(admission, reason_code="agent_runner_error")
        _srv._release_run_reservation(session_id, new_msg_id)
        try:
            _srv._emit_running_task_event(
                session_id,
                cleared_msg_id=new_msg_id,
                cleared_execution_id=admission.execution_id,
            )
        except Exception:
            pass
        return {"__error__": ("retry execution reservation was lost before startup", 500)}
    _srv._emit_running_task_event(session_id)
    try:
        worker.start()
    except BaseException as exc:
        adapter.fail_admission(admission, reason_code="agent_runner_error")
        if _srv._finish_owned_run(session_id, new_msg_id):
            try:
                _srv._emit_running_task_event(
                    session_id,
                    cleared_msg_id=new_msg_id,
                    cleared_execution_id=admission.execution_id,
                )
            except Exception:
                pass
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
    result = _fork_user_turn_and_run(session_id, pivot_id, new_content=None)
    if "__error__" in result:
        msg, code = result["__error__"]
        return JSONResponse(content={"error": msg}, status_code=code)
    return JSONResponse(content=result)


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
    result = _fork_user_turn_and_run(session_id, pivot_id, new_content=str(new_content))
    if "__error__" in result:
        msg, code = result["__error__"]
        return JSONResponse(content={"error": msg}, status_code=code)
    return JSONResponse(content=result)


@router.post("/api/chat/checkout")
async def post_chat_checkout(body: dict = None):
    """Move the conversation HEAD to a specific commit.

    Pure display op — nothing re-executes. The UI re-renders the
    linear history from the new HEAD back to root. Used by
    ``< N / M >`` navigation to switch between sibling versions.
    """
    from . import server as _srv

    if body is None:
        return JSONResponse(content={"error": "no body"}, status_code=400)
    session_id = body.get("session_id")
    target_id = body.get("msg_id")
    if not session_id or not target_id:
        return JSONResponse(
            content={"error": "session_id and msg_id required"}, status_code=400,
        )
    # Validate target_id against the FULL DAG, not the current linear
    # chain — that's the whole point of checkout. Looking it up in
    # conv["messages"] (which only holds the head's chain) means
    # off-branch clicks always 404'd.
    from openprogram.agent.session_db import default_db
    db = default_db()
    if not db.message_exists(session_id, target_id):
        return JSONResponse(content={"error": "unknown msg"}, status_code=404)
    # Reject checkout to function-internal nodes — a node whose
    # ``caller`` is another call lives inside an @agentic_function's
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
    db.set_head(session_id, target_id)
    with _srv._sessions_lock:
        conv = _srv._sessions.get(session_id)
        if conv is not None:
            conv["head_id"] = target_id
            # Refresh the in-memory transcript to the new branch's
            # chain so the next read doesn't show the old head's view.
            try:
                conv["messages"] = db.get_branch(session_id) or []
            except Exception:
                pass
    _srv._invalidate_messages(session_id)
    _srv._save_session(session_id)
    # HEAD moved to another chain, so the context the next request carries
    # is a different set of nodes — re-estimate rather than keep showing the
    # measurement taken on the branch we left.
    _srv.refresh_context_stats(session_id)
    return JSONResponse(content={"session_id": session_id, "head_id": target_id})
