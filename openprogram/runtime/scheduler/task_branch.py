"""The branch a scheduled task works on.

A task that runs a prompt is an agent of its own inside its conversation.
The first run opens a branch off the conversation's head; the branch's
meta entry records the task (``scheduled_task_id``), and every later run
continues at that branch's tip, so a task never holds more than one branch.
The entry moves with the tip the way a branch name does.

Each run starts clean: the model gets the task and the last result that said
something, not the branch's whole history and not the main conversation. A
run with nothing new answers ``QUIET_MARK``; it stays on the branch and tells
nobody. A run that could not do its check answers ``UNABLE_MARK`` and the
reason, which counts as a failed run (``task_runs``).
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Optional

TASK_FIELD = "scheduled_task_id"
QUIET_MARK = "[nothing new]"
UNABLE_MARK = "[could not check]"
LAST_RESULT_CHARS = 2000

_QUIET_RE = re.compile(r"^\s*[\[【]?\s*(nothing new|无新内容|没有新内容)\s*[\]】]?\s*[.。!！]?\s*$", re.I)
_UNABLE_RE = re.compile(r"^\s*\[could not check\]\s*", re.I)


@dataclass(frozen=True)
class Placement:
    """Where one run goes: the node it continues from and its message id."""
    msg_id: str
    parent: Optional[str]
    tip: Optional[str]
    #: The task's branch record; the run resolves its settings from it.
    branch_id: Optional[str] = None


def _db() -> Any:
    from openprogram.runtime.context.session_db import default_db
    return default_db()


def _entries(db: Any, session_id: str, task_id: str) -> list[str]:
    """The nodes holding this task's entry, newest first. More than one only
    while a run is on its way or after a run was cut off."""
    entries = db.branch_entries(session_id)
    known = {str(row.get("id")) for row in db.get_messages(session_id)}
    rows = [(float(entry.get("updated_at") or 0), head) for head, entry in entries.items()
            if entry.get(TASK_FIELD) == task_id and head in known]
    return [head for _at, head in sorted(rows, reverse=True)]


def bound_tip(session_id: str, task_id: str, db: Any = None) -> str | None:
    """The tip of the branch this task works on, if it has one yet."""
    heads = _entries(db or _db(), session_id, task_id)
    return heads[0] if heads else None


def is_quiet(text: str | None) -> bool:
    """Nothing to tell the user: the quiet mark, or no reply at all."""
    from openprogram.runtime.conversation.always_on.store import shows_anything

    return not shows_anything(text) or bool(_QUIET_RE.match(text or ""))


def unable_reason(text: str | None) -> str | None:
    """The reason a run gave for not doing its check; None if it did it."""
    match = _UNABLE_RE.match(text or "")
    return (text or "")[match.end():].strip() if match else None


def judged_text(session_id: str, reply: str, last_round: str) -> str:
    """The text a quiet or unable mark is looked for in.

    An always-on Agent is told to say what it is about to do, so its reply
    can open with a progress line from an earlier round; the mark is then
    the whole of its last round."""
    if not last_round or last_round == reply:
        return reply
    from openprogram.runtime.conversation.always_on import agent_for_session

    return last_round if agent_for_session(session_id) else reply


def _last_round(message: dict[str, Any]) -> str:
    """A saved reply's last model round: its last text block, if it kept blocks."""
    texts = [str(block["text"]) for block in message.get("blocks") or []
             if isinstance(block, dict) and block.get("type") == "text" and block.get("text")]
    return texts[-1] if texts else ""


def last_result(session_id: str, tip: str | None, db: Any = None) -> str:
    """The latest reply on the branch that said something."""
    from openprogram.state.attachments import find_markers

    if not tip:
        return ""
    for message in reversed((db or _db()).get_branch(session_id, tip) or []):
        if message.get("role") != "assistant":
            continue
        content = str(message.get("content") or "").strip()
        judged = judged_text(session_id, content, _last_round(message))
        quiet = is_quiet(judged) and not find_markers(content)
        if content and not quiet and unable_reason(judged) is None:
            return content[:LAST_RESULT_CHARS]
    return ""


def prepare(session_id: str, task: dict[str, Any], db: Any = None, *,
            agent_id: str | None = None) -> Placement:
    """Pick where this run goes and move the task's entry onto its message,
    so a run cut off halfway is still the branch's newest node.

    The branch also has its own settings record (``reserve_task_branch``):
    the first run copies the selected branch's settings with the task's
    Agent, and the run is admitted with that record's Agent, model and
    permission whatever branch the user has selected."""
    db = db or _db()
    tip = bound_tip(session_id, str(task["id"]), db)
    session = db.get_session(session_id) or {}
    parent = tip or session.get("head_id") or None
    msg_id = uuid.uuid4().hex
    fields: dict[str, Any] = {TASK_FIELD: str(task["id"])}
    existing = db.get_branch_meta(session_id, tip) if tip else {}
    name = existing.get("name") or str(task.get("title") or "")
    for key in ("name_locked", "auto_named", "turns"):
        if key in existing:
            fields[key] = existing[key]
    db.set_branch_name(session_id, msg_id, name, **fields)
    reserve = getattr(db, "reserve_task_branch", None)
    branch_id = None
    if reserve is not None:
        branch_id = reserve(session_id, task_id=str(task["id"]), msg_id=msg_id,
                agent_id=agent_id or (task.get("execution") or {}).get("agent_id") or task.get("agent_id"),
                label=db.get_branch_meta(session_id, msg_id) or name or None)
    return Placement(msg_id=msg_id, parent=parent, tip=tip,
                     branch_id=branch_id if isinstance(branch_id, str) else None)


def _preference_note(agent_id: str | None) -> str:
    """An always-on Agent's report preferences, which its scheduled runs follow too."""
    from openprogram.runtime.agent_registry import manager
    from openprogram.runtime.conversation.always_on import store

    spec = manager.get(agent_id) if agent_id else None
    if spec is None or not (spec.always_on or {}).get("enabled"):
        return ""
    return store.preference_note(store.load_preferences(spec.id))


def run_note(session_id: str, placement: Placement, db: Any = None, *,
             agent_id: str | None = None) -> str:
    """What the model is told beside the task; the branch shows only the task."""
    previous = last_result(session_id, placement.tip, db)
    parts = []
    if previous:
        parts.append("Your previous result, to compare with. Do not repeat what it already said:\n"
                     + previous)
    preferences = _preference_note(agent_id)
    if preferences:
        parts.append(preferences + "\nWhat they asked to leave out counts as nothing new.")
    parts.append(f"If there is nothing new worth telling the user, reply with exactly {QUIET_MARK} "
                 "and nothing else; the user is then not disturbed.")
    parts.append(f"If you could not do the check (a page would not open, a tool failed), reply with "
                 f"{UNABLE_MARK} and the reason in one sentence, in the language of the task.")
    parts.append("Anything else you reply is sent to the user as a chat message: write one short "
                 "message saying what changed, in the language of the task, with the links they need.")
    return "\n\n".join(parts)


def finish(session_id: str, task: dict[str, Any], placement: Placement | None,
           tip: str | None, db: Any = None) -> None:
    """Keep one entry: the task's, on the branch's new tip."""
    db = db or _db()
    task_id = str(task["id"])
    if tip:
        db.carry_branch_entry(session_id, tip)
    heads = _entries(db, session_id, task_id)
    for stale in heads[1:]:
        db.delete_branch_name(session_id, stale)
