"""Turn preparation — pipeline steps 1-2 (dispatcher-split).

Step 1: ensure the session row exists and resolve the LLM history for
this turn (history_override / INHERIT_PARENT branch walk / explicit
branch_from fork). Step 2: resolve the user node's predecessor, recall
the memory prefetch, build the user_msg dict, seed the ROOT node and
persist the user message through the TurnWriter.

Called once per turn by ``_process_turn_once`` in ``__init__.py``;
returns the (session, history) pair the rest of the pipeline consumes.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional, TYPE_CHECKING

from openprogram.events import emit_safe
from openprogram.agent.dispatcher.types import _InheritParent
from openprogram.agent.dispatcher.titles import _default_title

if TYPE_CHECKING:
    from openprogram.agent.dispatcher.types import EventCallback, TurnRequest
    from openprogram.agent.dispatcher.turn_writer import TurnWriter

_log = logging.getLogger(__name__)


def prepare_turn(
    *,
    db,
    req: "TurnRequest",
    writer: "TurnWriter",
    user_msg_id: str,
    on_event: "EventCallback",
) -> tuple[dict, list[dict]]:
    """Steps 1-2 of the turn pipeline. Returns ``(session, history)``."""
    # 1. Ensure session exists. Load history along the *active branch*
    #    (parent-walked from head_id) instead of the full append log,
    #    so retried / forked branches don't pollute the LLM context.
    session = db.get_session(req.session_id)
    if session is None:
        db.create_session(
            req.session_id, req.agent_id,
            title=_default_title(req),
            source=req.source,
            channel=req.source if req.source in {"wechat", "telegram", "discord", "slack"} else None,
            peer_display=req.peer_display,
            peer_id=req.peer_id,
        )
        session = db.get_session(req.session_id) or {}
    if req.history_override is not None:
        history = list(req.history_override)
    elif isinstance(req.branch_from, _InheritParent):
        # Normal append — walk the active branch.
        from openprogram.context.persistence import rendered_history
        history = rendered_history(db, req.session_id) \
            or db.get_messages(req.session_id)
    elif req.branch_from is None:
        # Root-level fork — LLM starts with empty history.
        history = []
    else:
        # Sibling fork — history is the branch ending at the explicit
        # parent. LLM sees what existed up to the fork point, not
        # what's currently on the active branch.
        history = db.get_branch(req.session_id, req.branch_from)

    # 2. Persist user message immediately (so a crash mid-stream still
    #    leaves the user's input recorded). Resolve predecessor:
    #      INHERIT_PARENT → tail of active branch, or NULL if empty
    #      explicit None  → NULL (root-level fork)
    #      explicit str   → that string (sibling fork)
    if isinstance(req.branch_from, _InheritParent):
        if history:
            user_caller_id = history[-1].get("id")
        else:
            user_caller_id = session.get("head_id")
    else:
        user_caller_id = req.branch_from
    # Memory prefetch belongs to THIS turn, not to the system prompt
    # (dag/overview.md §7). Recall it here, where the user node id is known,
    # so it can be stamped on the node for replay; the agent loop renders it
    # as a prefix block inside the wire user message. Recomputing it per LLM
    # call would be pointless — it only ever varies with the user's input.
    memory_prefetch = ""
    if req.user_text:
        try:
            from openprogram.agent.authority import normalize_authority
            from openprogram.memory import get_backend

            # The tier of the turn being prepared, resolved here and passed
            # down. The backend must not re-derive it: by the time recall
            # runs, a pairing could have changed, and the answer belongs to
            # the request as it was authorized.
            memory_prefetch = get_backend().search(
                req.user_text,
                session_id=req.session_id,
                tier=normalize_authority(req).get("authority_tier"),
            ) or ""
        except Exception:
            memory_prefetch = ""
    user_msg: dict[str, Any] = {
        "id": user_msg_id,
        "role": "user",
        "content": req.user_text,
        "timestamp": time.time(),
        "predecessor": user_caller_id,
        "source": req.source,
        "peer_display": req.peer_display,
        "peer_id": req.peer_id,
        "speaker_id": req.speaker_id,
        "speaker_display": req.speaker_display,
        # Stamp which agent this turn was sent to so the UI can render
        # per-agent avatar / label / colour. Same field on assistant
        # below — the pair lets the UI tag both halves of a turn.
        "agent_id": req.agent_id,
    }
    if req.spawn_caller and req.spawned_from_session:
        # A cross-session fork has two independent relations: predecessor is
        # the exact target-session context node, while caller identifies the
        # source-session node that requested the work.  Keep the node id on
        # the graph edge and persist only its session namespace as metadata.
        user_msg["caller"] = req.spawn_caller
        user_msg["spawned_from_session"] = req.spawned_from_session
    from openprogram.agent.authority import normalize_authority, stamp_schema
    user_msg.update(normalize_authority(req))
    # Marks the node as written by a build that records authority, so a
    # later reader can tell "this turn was never attributed" from "this
    # turn predates attribution" instead of granting both the benefit of
    # the doubt.
    stamp_schema(user_msg)
    # System-internal triggers — job_followup auto-notification,
    # merge prompt assembly — write a user-role node so the LLM
    # treats it as a turn, but they're NOT chats the human typed.
    # Mark display="runtime" so the chat panel renders them as a
    # quiet system marker (transparent surface, robot avatar) rather
    # than as a regular blue You-bubble that makes it look like the
    # user sent two messages in a row.
    #
    # Note: ``agent_spawn`` (the sub-agent's own first user msg) is
    # intentionally NOT in this set. When the user checks out the
    # sub branch, they want to see the prompt that started it — it's
    # the natural "You" message on that branch's HEAD path. On main,
    # the linear_history walk doesn't reach it (it's only on the sub
    # branch chain), so leaving it visible doesn't pollute main.
    if req.source in {"job_followup", "merge_turn"}:
        user_msg["display"] = "runtime"
    if memory_prefetch:
        # Replay reproduces the exact wire user message from the node.
        user_msg["memory_prefetch"] = memory_prefetch
    # Persist a lightweight attachment manifest (count + media types)
    # so /resume + the search picker can show "[2 images]" badges
    # without re-loading the base64 blobs. Full data still goes to
    # the LLM via the in-context UserMessage but doesn't need to live
    # in SessionDB rows — that would bloat the FTS5 index with base64.
    if req.attachments:
        manifest = []
        for att in req.attachments:
            if isinstance(att, dict):
                manifest.append({
                    "type": att.get("type"),
                    "media_type": att.get("media_type"),
                    "size_b64": len(att.get("data") or ""),
                })
        user_msg["extra"] = json.dumps({"attachments": manifest},
                                         default=str)
    # Ensure ROOT node exists (session DAG root). Idempotent.
    #
    # ``advance_head=False``: seeding the graph root is not a turn
    # extending the conversation, so it must not move head — the head
    # policy belongs to the TurnWriter alone (context/compaction.md §5).
    # With the shim's default the seed set head to "ROOT", which an
    # ordinary turn then overwrote milliseconds later and nobody
    # noticed; a session whose FIRST turn is a spawn turn writes
    # nothing head-advancing after it, so head stayed parked on ROOT.
    _ROOT_ID = "ROOT"
    try:
        from openprogram.context.nodes import Call as _RCall, ROLE_USER as _RU
        from openprogram.store import SessionNodeWriter as _GShim0
        if not db.message_exists(req.session_id, _ROOT_ID):
            _GShim0(db, req.session_id, advance_head=False).append(_RCall(
                id=_ROOT_ID, created_at=time.time(), role=_RU,
                output="", metadata={"display": "root"},
            ))
    except Exception:
        # Without a root the session graph is disconnected, so this is a
        # real defect even though the turn can still proceed.
        _log.warning(
            "failed to seed ROOT node for session %s",
            req.session_id, exc_info=True,
        )

    if not req.user_already_persisted:
        # Write the user node through the TurnWriter — the one object
        # allowed to move this turn's head (turn_writer.py).
        writer.persist_user(user_msg_id, user_msg, user_caller_id)
        on_event({
            "type": "chat_ack",
            "data": {"session_id": req.session_id, "msg_id": user_msg_id},
        })
        # Broadcast the inbound user message itself so any UI tailing
        # this session (web sidebar transcript, TUI mirror) shows it
        # in real time — without this, channel-sourced messages
        # (wechat / discord) only appeared after the LLM started
        # replying. Carries source + peer_display so the UI can label
        # it appropriately and dedup against optimistic renders.
        on_event({
            "type": "chat_response",
            "data": {
                "type": "user_message",
                "session_id": req.session_id,
                "msg_id": user_msg_id,
                "content": req.user_text or "",
                "source": req.source,
                "peer_display": req.peer_display,
                "timestamp": user_msg.get("timestamp"),
                "predecessor": user_msg.get("predecessor"),
            },
        })
    else:
        # Caller already wrote the user msg + emitted ack (webui
        # path). Make sure history reflects that — load from DB if
        # the caller didn't pass a history_override.
        if req.history_override is None:
            from openprogram.context.persistence import rendered_history
            history = rendered_history(
                db, req.session_id, head_id=user_msg_id,
            ) or history

    # 事件层 tap：无论哪条持久化路径（webui 先存 / dispatcher 自己存），
    # "用户消息提交了"都成立，所以放在分支外。
    emit_safe(
        "user.prompt_submitted", "user",
        {"msg_id": user_msg_id, "chars": len(req.user_text or "")},
        {"session": req.session_id},
    )

    return session, history
