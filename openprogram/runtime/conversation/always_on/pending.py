"""Reports still waiting for the user, across every always-on conversation
and every conversation a scheduled task reports into.

The inbox (the Inbox tab of the Tasks page, the count on the sidebar's Tasks
row and the top of the Activity panel) lists them in two groups:

- **approval** reports wait for a decision: one stays until a person writes
  after it in that conversation or in a channel conversation the report was
  copied to. A turn nobody typed (a Goal's continuation, a job notice, a
  handoff, a reminder) answers nothing, and a report stays listed after its
  Agent's always-on is turned off.
- **finding** and **done** reports are news: one stays until the user looks
  at the conversation after it arrived (``seen_at``, set by
  ``mark_seen`` when a client opens the conversation, or at once when the
  conversation was on screen as the report came in).

A scheduled task's result (a report with ``task_id``) is news like a
finding: it is also a message in the chat, and it stays in the inbox until
the user opens that conversation. Its item carries the task's id and title,
so the inbox shows it came from a scheduled task, not from an Agent.

Reports older than ``MAX_AGE_DAYS`` drop out either way, so a question
answered somewhere else does not wait forever.

The windows read the inbox again when :data:`store.INBOX_TOPIC` is
signalled: a report is written or marked seen (``store``), or a person wrote
in a conversation (:func:`person_wrote`), which can answer a decision.
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


def person_wrote(session_id: str, message: dict[str, Any]) -> None:
    """A message was added to ``session_id``: if a person wrote it, a
    decision waiting there (or in a conversation it was copied from) may be
    answered."""
    if _written_by_a_person(message):
        from openprogram.host._platform import changes
        changes.signal(store.INBOX_TOPIC, session_id)


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
    fresh = [row for row in rows if (row.get("at") or 0) >= oldest]
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
    from openprogram.runtime import scheduler_link
    from openprogram.runtime.conversation.always_on.runtime import owner_conversation

    now = time.time() if now is None else now
    oldest = now - MAX_AGE_DAYS * 86400
    found: list[dict[str, Any]] = []
    done: set[str] = set()
    try:
        tasks = {str(task.get("id")): task for task in scheduler_link.scheduled_tasks()}
    except Exception:
        _log.warning("scheduled tasks not read for the inbox", exc_info=True)
        tasks = {}

    def collect(session: dict[str, Any], agent_id: str, agent_name: str) -> None:
        sid = str(session["id"])
        done.add(sid)
        for row in _waiting(sid, store.load_reports(sid, REPORTS_PER_CONVERSATION), oldest):
            task_id = row.get("task_id")
            task = tasks.get(str(task_id)) if task_id else None
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
                "task_id": str(task_id) if task_id else None,
                "task_title": ((task or {}).get("title") or row.get("title") or "") if task_id else None,
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
    # A scheduled task reports into the conversation it was set in, of any Agent.
    for sid in sorted({str(task.get("session_id")) for task in tasks.values() if task.get("session_id")} - done):
        try:
            session = _db().get_session(sid)
        except Exception:
            continue
        if not owner_conversation(session):
            continue
        agent_id = str(session.get("agent_id") or "main")
        spec = manager.get(agent_id)
        collect(session, agent_id, (spec.name or spec.id) if spec is not None else agent_id)
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
