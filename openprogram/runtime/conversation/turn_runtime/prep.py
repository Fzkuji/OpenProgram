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

from openprogram.runtime.events import emit_safe
from openprogram.runtime.agent.turn_request import _InheritParent
from openprogram.runtime.conversation.turn_runtime.titles import _default_title

if TYPE_CHECKING:
    from openprogram.runtime.conversation.turn_runtime.types import EventCallback
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.turn_runtime.turn_writer import TurnWriter

_log = logging.getLogger(__name__)

RUNTIME_SOURCES = frozenset({"job_followup", "merge_turn", "process_exit"})


def is_runtime_prompt(req: "TurnRequest") -> bool:
    """Whether the turn's prompt was written by the runtime, not a person.

    Such a prompt is still sent to the model, but the chat does not draw it
    as a message: a restart resume, for one, reads as the interrupted reply
    simply carrying on."""
    return bool(req.source in RUNTIME_SOURCES or req.goal_trigger
                or req.restart_resume)


def _own_branch_tip(session: dict, req: "TurnRequest") -> str | None:
    """The tip of the branch the turn was admitted on, when that branch is
    recorded and is not the selected one; ``None`` reads the selected head.

    A turn keeps its branch: a message, a Goal continuation or a sub-agent's
    return admitted on one branch continues it even after the person
    switched to another."""
    branch = getattr(req, "branch_id", None)
    if not branch or branch == session.get("active_branch_id"):
        return None
    ref = (session.get("branch_refs") or {}).get(branch)
    if not isinstance(ref, dict) or ref.get("status") == "aborted":
        return None
    return ref.get("head_id") or None


def prepare_turn(
    *,
    db,
    req: "TurnRequest",
    writer: "TurnWriter",
    user_msg_id: str,
    on_event: "EventCallback",
    assistant_msg_id: str | None = None,
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
        # Normal append — walk the turn's own branch: the selected one
        # unless the person selected another since it was admitted.
        from openprogram.runtime.context.compaction.persistence import rendered_history
        own_tip = _own_branch_tip(session, req)
        history = (rendered_history(db, req.session_id, head_id=own_tip) if own_tip
                   else rendered_history(db, req.session_id)) \
            or db.get_messages(req.session_id)
    elif req.branch_from is None:
        # Root-level fork — LLM starts with empty history.
        history = []
    else:
        # Sibling fork — history is the branch ending at the explicit
        # parent. LLM sees what existed up to the fork point, not
        # what's currently on the active branch.
        history = db.get_branch(req.session_id, req.branch_from)

    from openprogram.state.memory.policy import bind_history, stamp
    policy_history = list(history)
    if req.spawn_caller:
        caller_session = req.spawned_from_session or req.session_id
        caller_rows = [row for row in db.get_messages(caller_session) if row.get("id") == req.spawn_caller]
        policy_history.extend(caller_rows or [{"memory_policy": {}}])
    bind_history(req, policy_history)

    # 2. Persist user message immediately (so a crash mid-stream still
    #    leaves the user's input recorded). Resolve predecessor:
    #      INHERIT_PARENT → tail of active branch, or NULL if empty
    #      explicit None  → NULL (root-level fork)
    #      explicit str   → that string (sibling fork)
    if isinstance(req.branch_from, _InheritParent):
        if history:
            user_caller_id = history[-1].get("id")
        else:
            user_caller_id = _own_branch_tip(session, req) or session.get("head_id")
    else:
        user_caller_id = req.branch_from
    # Memory prefetch belongs to THIS turn, not to the system prompt
    # (dag/overview.md §7). Recall it here, where the user node id is known,
    # so it can be stamped on the node for replay; the agent loop renders it
    # as a prefix block inside the wire user message. Recomputing it per LLM
    # call would be pointless — it only ever varies with the user's input.
    memory_prefetch = ""
    recall_query = req.user_text if req.recall_query is None else req.recall_query
    if recall_query:
        try:
            from openprogram.state.authority import normalize_authority
            from openprogram.state.memory import get_backend

            # The tier of the turn being prepared, resolved here and passed
            # down. The backend must not re-derive it: by the time recall
            # runs, a pairing could have changed, and the answer belongs to
            # the request as it was authorized.
            memory_prefetch = get_backend().search(
                recall_query,
                session_id=req.session_id,
                tier=normalize_authority(req).get("authority_tier"),
            ) or ""
        except Exception as exc:
            from openprogram.state.memory.policy import require_or_empty
            require_or_empty(exc)
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
    user_msg.update(stamp(req))
    if req.spawn_caller and req.spawned_from_session:
        # A cross-session fork has two independent relations: predecessor is
        # the exact target-session context node, while caller identifies the
        # source-session node that requested the work.  Keep the node id on
        # the graph edge and persist only its session namespace as metadata.
        user_msg["caller"] = req.spawn_caller
        user_msg["spawned_from_session"] = req.spawned_from_session
    from openprogram.state.authority import normalize_authority, stamp_schema
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
    if is_runtime_prompt(req):
        user_msg["display"] = "runtime"
    # What kind of row this is must not be read back from its text.
    if req.message_origin:
        user_msg["origin"] = req.message_origin
    elif req.speaker_kind in ("owner", "human") and not is_runtime_prompt(req):
        user_msg["origin"] = "person"
    if req.restart_resume:
        # The chat draws this turn's reply as the rest of the reply the
        # restart cut off, with no row for the prompt (Codex likewise keeps
        # a recovered turn under its original one).
        user_msg["trigger"] = "restart"
    if req.notice:
        user_msg["notice"] = req.notice
    if req.starts_goal:
        # The message that sets a Goal; the chat labels it Goal.
        user_msg["starts_goal"] = True
    if req.automation:
        # Still drawn as the message it is; a reader of the history can tell
        # that nobody typed it (always_on/pending.py).
        user_msg["automation"] = req.automation
    # The date the turn is sent on goes beside its message, not in the system
    # prompt, so a conversation that crosses midnight keeps its cached prefix.
    # What goes beside the message is stored on its node as it happens
    # (``context.prompt_assembly.turn_context``): the date, a rewind made before this turn,
    # the memory it recalled (replay reproduces the exact wire message from
    # the node) and a scheduled run's note, sent after the recalled memory.
    from openprogram.runtime.context.prompt_assembly import turn_context as _turn_context
    from openprogram.runtime.context.prompt_assembly.components import current_date_note
    current_date = current_date_note()
    beside = _turn_context.put([], _turn_context.ATTACHMENT, "date", current_date)
    beside = _turn_context.put(beside, _turn_context.ATTACHMENT, "memory_prefetch", memory_prefetch or "")
    beside = _turn_context.put(beside, _turn_context.ATTACHMENT, "run_note", req.run_note or "")
    if not req.restart_resume:
        # The reply this message follows was stopped: the note is written
        # now, onto this message, so every later request sends it unchanged.
        beside = _turn_context.put(beside, _turn_context.ATTACHMENT, "interrupted",
                                   _interrupted_note(db, req, user_msg_id, user_caller_id))
    from openprogram.runtime.conversation.turn_runtime.loop_runner import owns_conversation_contract
    if owns_conversation_contract(req):
        # A rewind is told to the conversation's own next turn. It is taken
        # now, onto this message, so a turn that fails and is sent again
        # still carries it.
        from openprogram.runtime.conversation.rewinds.rewind_marks import take_rewind_note
        if req.user_already_persisted:
            stored = (db.get_messages_by_id(req.session_id, [user_msg_id]) or [{}])[0] \
                if hasattr(db, "get_messages_by_id") else {}
            earlier = _turn_context.entry_text(_turn_context.entries(stored), "rewind")
        else:
            earlier = ""
        beside = _turn_context.put(beside, _turn_context.ATTACHMENT, "rewind",
                                   "\n\n".join(t for t in (earlier, take_rewind_note(req.session_id)) if t))
    user_msg.update(_turn_context.node_metadata(beside))
    turn_settings = getattr(req, "turn_settings", None)
    if turn_settings:
        # What this turn ran with and where each value came from, frozen at
        # admission: the conversation's own record of the turn's settings.
        user_msg["turn_settings"] = turn_settings
        if turn_settings.get("model_changed_from"):
            # The branch follows its Agent and the Agent's model changed:
            # one quiet row above this message says so.
            user_msg["model_change"] = {"from": turn_settings["model_changed_from"],
                                        "to": turn_settings.get("model"),
                                        "follows": "agent"}
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
        # The images themselves, kept so later requests send them again
        # after the text, where the turn put them (``store.session.media``).
        try:
            from openprogram.state.store.session.files.media import records_for_attachments
            # The text block comes first when the turn has text (loop_runner).
            media = records_for_attachments(db._session_dir(req.session_id), req.attachments,  # noqa: SLF001
                                            1 if req.user_text else 0)
        except Exception:  # noqa: BLE001 - the turn goes on without the copy
            _log.warning("user message media not kept for session %s", req.session_id, exc_info=True)
            media = []
        if media:
            user_msg["media"] = media
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
        from openprogram.state.store.session.nodes import Call as _RCall, ROLE_USER as _RU
        from openprogram.state.store import SessionNodeWriter as _GShim0
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
                # A runtime trigger (agent notice, merge) draws as a
                # quiet row live too, not as a bubble the user typed.
                "display": user_msg.get("display"),
                "notice": user_msg.get("notice"),
                "trigger": user_msg.get("trigger"),
                "model_change": user_msg.get("model_change"),
            },
        })
    else:
        # Caller already wrote the user msg + emitted ack (webui
        # path). Make sure history reflects that — load from DB if
        # the caller didn't pass a history_override.
        if req.history_override is None:
            from openprogram.runtime.context.compaction.persistence import rendered_history
            history = rendered_history(
                db, req.session_id, head_id=user_msg_id,
            ) or history

    # A prompt that starts a turn is a point its branch can rewind to, on the
    # selected branch or beside it (another branch, a scheduled run's own).
    # A turn with no point of its own records its file edits in the point it
    # belongs to: its spawner's, or the nearest earlier prompt on its branch.
    from openprogram.state.store.snapshot.checkpoint.helpers import (
        capture_prompt_checkpoint,
        join_prompt_checkpoint,
    )
    spawned = req.source == "agent_spawn" or bool(req.spawn_caller or req.spawned_from_session)
    if (
        req.source != "agent_spawn"
        and not is_runtime_prompt(req)
        and not req.restart_resume
        and (getattr(req, "advance_head", True) or not spawned)
    ):
        capture_prompt_checkpoint(db, req.session_id, user_msg_id, assistant_msg_id)
    elif assistant_msg_id:
        join_prompt_checkpoint(
            db, req.session_id, assistant_msg_id, spawned=spawned,
            # A job names its caller's session even when that is this one.
            spawned_by=(
                req.spawn_caller
                if req.spawned_from_session in (None, "", req.session_id) else None
            ),
            ancestors=lambda: [
                message["id"] for message in reversed(db.get_branch(req.session_id, user_msg_id))
                if message.get("id")
            ],
        )

    # 事件层 tap：无论哪条持久化路径（webui 先存 / dispatcher 自己存），
    # "用户消息提交了"都成立，所以放在分支外。
    emit_safe(
        "user.prompt_submitted", "user",
        {"msg_id": user_msg_id, "chars": len(req.user_text or "")},
        {"session": req.session_id},
    )

    if req.user_already_persisted:
        # The caller stored the message before this turn recalled memory or
        # was given its note; later turns render both from the node.
        sent_before = {key: value for key, value in (
            ("turn_settings", getattr(req, "turn_settings", None)),
            ("model_change", user_msg.get("model_change")),) if value}
        db.merge_node_metadata(req.session_id, user_msg_id, {
            **stamp(req), **sent_before, **_turn_context.node_metadata(beside)})
    _record_last_branch(db, req)
    return session, history


def _record_last_branch(db, req: "TurnRequest") -> None:
    """Remember the branch a person's message was last written to
    (``last_branch_id``): where an entry that names no branch, and a
    conversation opened afresh, continue. A background turn (a scheduled
    run, a check-in) is not a person's message and leaves it alone."""
    branch = getattr(req, "branch_id", None)
    if not branch or getattr(req, "background", False):
        return
    try:
        if (db.get_session(req.session_id) or {}).get("last_branch_id") != branch:
            db.update_session(req.session_id, last_branch_id=branch)
    except Exception:  # noqa: BLE001 - the selected branch stays the default
        _log.debug("last branch not recorded for %s", req.session_id, exc_info=True)


def _interrupted_note(db, req: "TurnRequest", user_msg_id: str, parent_id: Optional[str]) -> str:
    """The note this message carries when the reply it follows was stopped.

    A message stored before this turn keeps the note it already has (a turn
    sent again sends the same bytes); otherwise the note comes from the
    reply the message follows on its own branch, so a message on a new
    branch from a stopped reply carries it and a retry of the message
    before it does not (``turn_context.interruption_note``).
    """
    from openprogram.runtime.context.prompt_assembly import turn_context as _turn_context

    if not hasattr(db, "get_messages_by_id"):
        return ""
    try:
        if req.user_already_persisted:
            stored = (db.get_messages_by_id(req.session_id, [user_msg_id]) or [{}])[0]
            kept = _turn_context.entry_text(_turn_context.entries(stored), "interrupted")
            if kept or not isinstance(stored, dict):
                return kept
            parent_id = stored.get("predecessor") or parent_id
        if not parent_id:
            return ""
        parent = (db.get_messages_by_id(req.session_id, [parent_id]) or [None])[0]
        return _turn_context.interruption_note(_turn_context.reply_interruption(parent))
    except Exception:  # noqa: BLE001 - the turn goes out without the note
        _log.debug("interruption note not read for %s", req.session_id, exc_info=True)
        return ""
