"""Reports still waiting for the user, across every always-on conversation.

The pending inbox (the sidebar's Pending page and the top of the Activity
panel) lists them in two groups:

- **approval** reports wait for a decision: one stays until a person writes
  after it in that conversation or in a channel conversation the report was
  copied to. A turn nobody typed (a Goal's continuation, a job notice, a
  handoff, a reminder) answers nothing, and a report stays listed after its
  Agent's always-on is turned off.
- **finding** and **done** reports are news: one stays until the user looks
  at the conversation after it arrived (``seen_at``, set by
  ``mark_seen`` when a client opens the conversation, or at once when the
  conversation was on screen as the report came in).

Reports older than ``MAX_AGE_DAYS`` drop out either way, so a question
answered somewhere else does not wait forever.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from openprogram.runtime.conversation.always_on import delivery, store

_log = logging.getLogger(__name__)

MAX_AGE_DAYS = 7
REPORTS_PER_CONVERSATION = 20
SESSIONS_PER_AGENT = 500


def _db():
    from openprogram.runtime.context.session_db import default_db
    return default_db()


def _written_by_a_person(message: dict[str, Any]) -> bool:
    if message.get("role") != "user":
        return False
    if message.get("steering"):
        # Typed into a running turn, so it carries that turn's speaker.
        return True
    return (message.get("display") != "runtime" and not message.get("automation")
            and message.get("speaker_kind") != "runtime")


def _last_user_message_at(session_id: str) -> float:
    """When a person last wrote on the conversation's main chain; 0 if never."""
    try:
        chain = _db().get_branch(session_id)
    except Exception:
        return 0.0
    for message in reversed(chain):
        if _written_by_a_person(message):
            at = message.get("timestamp") or message.get("created_at") or 0
            try:
                return float(at)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _waiting(session_id: str, rows: list[dict[str, Any]], oldest: float) -> list[dict[str, Any]]:
    # A scheduled task's result is a message in the chat, not an inbox item.
    fresh = [row for row in rows if (row.get("at") or 0) >= oldest and not row.get("task_id")]
    if not fresh:
        return []
    answered: dict[str, float] = {}

    def last_answer(row: dict[str, Any]) -> float:
        for sid in (session_id, *delivery.mirror_sessions(row)):
            if sid not in answered:
                answered[sid] = _last_user_message_at(sid)
        return max(answered[sid] for sid in (session_id, *delivery.mirror_sessions(row)))

    waiting = []
    for row in fresh:
        if row.get("kind") == "approval":
            if last_answer(row) < (row.get("at") or 0):
                waiting.append(row)
        elif not row.get("seen_at"):
            waiting.append(row)
    return waiting


def items(now: float | None = None) -> list[dict[str, Any]]:
    """Every report still waiting for the user, newest first."""
    from openprogram.runtime.agent_registry import manager
    from openprogram.runtime.conversation.always_on.runtime import owner_conversation

    now = time.time() if now is None else now
    oldest = now - MAX_AGE_DAYS * 86400
    found: list[dict[str, Any]] = []

    def collect(session: dict[str, Any], agent_id: str, agent_name: str) -> None:
        sid = str(session["id"])
        for row in _waiting(sid, store.load_reports(sid, REPORTS_PER_CONVERSATION), oldest):
            found.append({
                "session_id": sid,
                "session_title": session.get("title") or "",
                "agent_id": agent_id,
                "agent_name": agent_name,
                "report_id": row.get("id"),
                "kind": row.get("kind") or "finding",
                "title": row.get("title") or "",
                "body": row.get("body") or "",
                "at": row.get("at") or 0,
            })

    for spec in manager.list_all():
        # Turned off is not answered: an Agent that ever ran always-on keeps
        # its state document, and one that never did has nothing to scan.
        if not (spec.always_on or {}).get("enabled") and not store.load_agent_state(spec.id):
            continue
        try:
            sessions = _db().list_sessions(agent_id=spec.id, limit=SESSIONS_PER_AGENT)
        except Exception:
            continue
        for session in sessions:
            if owner_conversation(session):
                collect(session, spec.id, spec.name or spec.id)
    found.sort(key=lambda item: item["at"], reverse=True)
    return found


def mark_seen(session_id: str, now: float | None = None) -> bool:
    """The user looked at the conversation: its news reports are read.
    True when a report changed."""
    rows = store.load_reports(session_id, REPORTS_PER_CONVERSATION)
    unseen = [row for row in rows if not row.get("seen_at") and row.get("kind") != "approval"]
    if not unseen:
        return False
    at = time.time() if now is None else now
    for row in unseen:
        store.update_report(session_id, row["id"], seen_at=at)
    return True
