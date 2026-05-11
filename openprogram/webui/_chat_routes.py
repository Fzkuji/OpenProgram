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

See docs/design/contextgit.md for semantics (retry = fork with same
content, edit = fork with new content, checkout = pure HEAD move).
"""
from __future__ import annotations

import threading
import time
import uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from openprogram.contextgit import advance_head


router = APIRouter()


def _fork_user_turn_and_run(session_id: str, pivot_id: str, new_content: str | None) -> dict:
    """Shared engine for retry / edit.

    Finds the nearest user-message ancestor of ``pivot_id``, creates a
    sibling user message at the same position in the DAG (same
    ``parent_id``), sets that as HEAD, and kicks off execution. The
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
            cur = by_id.get(cur.get("parent_id"))
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
            m.get("parent_id") == src_user.get("id") and m.get("role") == "assistant"
            for m in msgs
        )
        if new_content is None and not has_assistant_child:
            new_msg_id = src_user.get("id")
            new_user = src_user
            conv["head_id"] = new_msg_id
            try:
                from openprogram.agent.session_db import default_db
                default_db().set_head(session_id, new_msg_id)
            except Exception:
                pass
        else:
            new_msg_id = str(uuid.uuid4())[:8]
            new_user = {
                "role": "user",
                "id": new_msg_id,
                "content": new_content if new_content is not None
                           else src_user.get("content", ""),
                "timestamp": time.time(),
                # Sibling of src_user: same parent.
                "parent_id": src_user.get("parent_id"),
                # Lineage breadcrumbs (future tooling / debugging).
                "forked_from": src_user.get("id"),
            }
            if src_user.get("display"):
                new_user["display"] = src_user["display"]
            if new_content is not None:
                new_user["edit_of"] = src_user.get("id")

            advance_head(conv, new_user)   # append + move HEAD

    _srv._save_session(session_id)

    # Kick off the run against the new user message. Same dispatch
    # logic as POST /api/chat.
    parsed = _srv._parse_chat_input(new_user["content"] or "")
    if parsed["action"] == "run":
        threading.Thread(
            target=_srv._execute_in_context,
            args=(session_id, new_msg_id, "run"),
            kwargs={"func_name": parsed["function"], "kwargs": parsed["kwargs"]},
            daemon=True,
        ).start()
    else:
        threading.Thread(
            target=_srv._execute_in_context,
            args=(session_id, new_msg_id, "query"),
            kwargs={"query": parsed["raw"]},
            daemon=True,
        ).start()

    return {
        "session_id": session_id,
        "msg_id": new_msg_id,
        "forked_from": src_user.get("id"),
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
    cur = db.conn.execute(
        "SELECT 1 FROM messages WHERE id=? AND session_id=?",
        (target_id, session_id),
    )
    if not cur.fetchone():
        return JSONResponse(content={"error": "unknown msg"}, status_code=404)
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
    return JSONResponse(content={"session_id": session_id, "head_id": target_id})
