"""Rewind actions: list points, plan and apply."""
from __future__ import annotations

import asyncio
import json
import uuid


async def handle_rewind_list(ws, cmd: dict):
    """List available rewind points for the session."""
    session_id = (cmd.get("session_id") or "").strip()
    if not session_id:
        await ws.send_text(json.dumps({
            "type": "rewind_points",
            "data": {"session_id": session_id or None, "points": [],
                     "error": "No session_id provided"},
        }))
        return
    limit = cmd.get("limit", 10)
    try:
        from openprogram.interfaces.server import server as _s
        from openprogram.runtime.conversation.rewind import list_rewind_points
        loop = asyncio.get_event_loop()
        points = await loop.run_in_executor(
            None, lambda: list_rewind_points(
                session_id, limit, recover=not _s._is_run_active(session_id),
            ),
        )
        await ws.send_text(json.dumps({
            "type": "rewind_points",
            "data": {"session_id": session_id, "points": points},
        }, default=str))
    except Exception as e:
        await ws.send_text(json.dumps({
            "type": "rewind_points",
            "data": {"session_id": session_id, "points": [],
                     "error": f"{type(e).__name__}: {e}"},
        }))


async def handle_rewind(ws, cmd: dict):
    """Rewind code + conversation to a chosen point."""
    session_id = (cmd.get("session_id") or "").strip()
    target_msg_id = (cmd.get("target_msg_id") or "").strip()
    if not session_id or not target_msg_id:
        await ws.send_text(json.dumps({
            "type": "rewind_result",
            "data": {"session_id": session_id or None,
                     "error": "session_id and target_msg_id are required"},
        }))
        return
    from openprogram.interfaces.server import server as _s
    phase = (cmd.get("phase") or "plan").strip().lower()
    reservation = f"rewind_{uuid.uuid4().hex}"
    if phase == "apply":
        refusal = await asyncio.to_thread(
            lambda: _s.reserve_operation(
                session_id, "rewind", reservation,
                scope=rewind_scope((cmd.get("mode") or "code_and_conversation").strip()),
                key=rewind_slot_key(cmd.get("idempotency_key")),
            ),
        )
        if refusal is not None:
            await _send_run_active(ws, _s, session_id, refusal)
            return
    try:
        from openprogram.runtime.conversation.rewind import (
            list_rewind_points,
            plan_rewind,
            rewind_to,
        )
        loop = asyncio.get_event_loop()
        if target_msg_id.startswith("__by_index__"):
            idx = int(target_msg_id.removeprefix("__by_index__"))
            points = await loop.run_in_executor(
                None, lambda: list_rewind_points(session_id),
            )
            if idx < 1 or idx > len(points):
                await ws.send_text(json.dumps({
                    "type": "rewind_result",
                    "data": {"session_id": session_id,
                             "error": f"Invalid index {idx}. Available: 1-{len(points)}"},
                }))
                return
            target_msg_id = points[idx - 1]["msg_id"]
        mode = (cmd.get("mode") or "code_and_conversation").strip()
        if phase == "plan":
            recover = not await asyncio.to_thread(_s._is_run_active, session_id)
            result = await loop.run_in_executor(
                None, lambda: plan_rewind(
                    session_id, target_msg_id, mode=mode, recover=recover,
                ),
            )
        elif phase == "apply":
            idempotency_key = (cmd.get("idempotency_key") or "").strip()
            plan_hash = (cmd.get("plan_hash") or "").strip()
            if not idempotency_key or not plan_hash:
                await ws.send_text(json.dumps({
                    "type": "rewind_result",
                    "data": {
                        "session_id": session_id,
                        "status": "error",
                        "error": "apply requires idempotency_key and plan_hash",
                    },
                }))
                return
            result = await loop.run_in_executor(
                None, lambda: rewind_to(
                    session_id,
                    target_msg_id,
                    idempotency_key=idempotency_key,
                    expected_plan_hash=plan_hash,
                    mode=mode,
                ),
            )
        else:
            await ws.send_text(json.dumps({
                "type": "rewind_result",
                "data": {
                    "session_id": session_id,
                    "status": "error",
                    "error": f"unknown rewind phase {phase!r}",
                },
            }))
            return
        # The transaction moves HEAD only after every file action verifies.
        # Mirror that committed CAS, including a valid move to ``None``.
        await asyncio.to_thread(mirror_rewound_head, _s, session_id, result)
        await ws.send_text(json.dumps({
            "type": "rewind_result",
            "data": {**result, "session_id": session_id},
        }, default=str))
    except Exception as e:
        await ws.send_text(json.dumps({
            "type": "rewind_result",
            "data": {"session_id": session_id,
                     "error": f"{type(e).__name__}: {e}"},
        }))
    finally:
        if phase == "apply":
            await asyncio.to_thread(_s._release_run_reservation, session_id, reservation)


def mirror_rewound_head(_s, session_id: str, result: dict) -> None:
    """Mirror a committed rewind's head and re-measure its context; this
    takes the conversation's lock, so callers run it off the event loop.

    The request that applied the rewind does it before it frees the slot a
    repeat waits on. A repeat (``replayed``) reports the same outcome and
    mirrors only while the stored head is still the one the rewind left: a
    conversation that has moved on since is never pulled back.
    """
    if not result.get("head_changed"):
        return
    head_id = result.get("new_head_id")
    if result.get("replayed"):
        from openprogram.runtime.context.session_db import default_db
        if (default_db().get_session(session_id) or {}).get("head_id") != head_id:
            return
    _s._set_active_head(session_id, head_id)
    _s.refresh_context_stats(session_id)


def rewind_slot_key(idempotency_key) -> str | None:
    """The slot key of a rewind apply: a repeat with the same idempotency
    key joins the rewind holding the slot and returns its outcome."""
    key = (idempotency_key or "").strip() if isinstance(idempotency_key, str) else ""
    return f"rewind:{key}" if key else None


def rewind_scope(mode: str) -> str:
    """A rewind that restores files holds the whole conversation; one that
    moves only the conversation holds its own branch."""
    return "branch" if mode == "conversation" else "conversation"


async def _send_run_active(ws, _s, session_id: str, refusal: dict | None = None) -> None:
    refusal = refusal or {"code": "run_active", "error": _s.RUN_ACTIVE_ERROR}
    await ws.send_text(json.dumps({
        "type": "rewind_result",
        "data": {"session_id": session_id, **refusal},
    }, default=str))


async def handle_rewind_marks(ws, cmd: dict):
    """Send the Undo and Redo marks for the transcript at the current HEAD."""
    from openprogram.runtime.conversation.rewind_marks import list_rewind_marks

    session_id = (cmd.get("session_id") or "").strip()
    marks = await asyncio.to_thread(list_rewind_marks, session_id) if session_id else []
    await ws.send_text(json.dumps({
        "type": "rewind_marks",
        "data": {"session_id": session_id or None, "marks": marks},
    }, default=str))


async def _handle_switch(ws, cmd: dict, *, undo: bool) -> None:
    from openprogram.interfaces.server.routes.chat_branches import _switch_rewind

    session_id = (cmd.get("session_id") or "").strip()
    result = await asyncio.to_thread(
        _switch_rewind, session_id, cmd.get("edge_id"), cmd.get("expected_head_id"), undo,
    )
    if "__error__" in result:
        message, _code = result["__error__"]
        result = {"status": "refused", "code": "run_active", "error": message}
    await ws.send_text(json.dumps({
        "type": "rewind_switch_result",
        "data": {**result, "session_id": session_id, "action": "undo" if undo else "redo"},
    }, default=str))


async def handle_rewind_undo(ws, cmd: dict):
    await _handle_switch(ws, cmd, undo=True)


async def handle_rewind_redo(ws, cmd: dict):
    await _handle_switch(ws, cmd, undo=False)
