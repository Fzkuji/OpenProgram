"""Runtime state and reports of always-on conversations.

A conversation whose Agent is always-on keeps its own wake state and
reports next to its messages: ``<session dir>/always_on.json`` and
``always_on_reports.jsonl``, written atomically under a per-conversation
lock, and one row per firing in ``always_on_wake_log.jsonl``. The Agent keeps what all its conversations share in
``<agent dir>/always_on/``: ``state.json`` (when always-on was turned on,
which decides the conversations that take part, and the check-in spending
of the day) and ``preferences.json`` (what the user told it to report or
leave out). Handoff chains, which span conversations, live in one file
for the worker (``<state dir>/always_on/handoffs.json``). None touches ``agent.json``, so wakes never bump the
configuration revision or conflict with an open editor draft.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from openprogram.host._platform.filesystem import replace_file

MAX_REPORTS = 200
# Over a week of half-hourly firings, enough for the weekly health read.
MAX_WAKE_LOG = 500
TITLE_MAX = 200
BODY_MAX = 8000
LINKS_MAX = 10
REPORT_KINDS = ("finding", "approval", "done")
PREFERENCES_MAX = 50
PREFERENCE_MAX = 500

#: The pending inbox may have changed (``pending.py``): every window reads it
#: again (``host/_platform/changes.py``). Keyed by the conversation.
INBOX_TOPIC = "inbox"

_locks: dict[str, threading.RLock] = {}
_locks_master = threading.Lock()


def shows_anything(text: Any) -> bool:
    """Whether any of the text would be visible; spaces and zero-width marks are not."""
    return any(not char.isspace() and unicodedata.category(char) not in {"Cc", "Cf"} for char in str(text or ""))


def lock(key: str) -> threading.RLock:
    with _locks_master:
        return _locks.setdefault(key, threading.RLock())


def directory(session_id: str) -> Path | None:
    """The conversation's directory; None when the conversation is gone."""
    from openprogram.runtime.context.session_db import default_db
    folder = default_db()._session_dir(session_id)  # noqa: SLF001 — beside todos.json
    return folder if folder.exists() else None


def _agent_state_path(agent_id: str) -> Path:
    from openprogram.runtime.agent_registry.manager import agent_dir
    return agent_dir(agent_id) / "always_on" / "state.json"


def _replace_file(path: Path, write) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            write(handle)
        replace_file(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_json(path: Path, data: Any) -> None:
    _replace_file(path, lambda handle: json.dump(data, handle, ensure_ascii=False, indent=2))


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _session_document(session_id: str, name: str):
    from openprogram.runtime.context.session_db import default_db
    return default_db().state_file(session_id, name) if directory(session_id) else None


def _state_path(session_id: str):
    return _session_document(session_id, "always_on.json")


def _reports_path(session_id: str):
    return _session_document(session_id, "always_on_reports.jsonl")


def load_state(session_id: str) -> dict[str, Any]:
    return _read_json(_state_path(session_id))


def update_state(session_id: str, **changes: Any) -> dict[str, Any]:
    with lock(session_id):
        path = _state_path(session_id)
        if path is None:
            raise ValueError(f"conversation {session_id!r} not found")
        state = load_state(session_id)
        before = dict(state)
        state.update(changes)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    if state != before:
        _wakes_changed(session_id)
    return state


def _wakes_changed(key: str) -> None:
    from openprogram.host._platform import changes as _changes
    from openprogram.runtime.agent_registry.manager import ALWAYS_ON_WAKES_TOPIC
    _changes.signal(ALWAYS_ON_WAKES_TOPIC, key)


def _handoffs_path() -> Path:
    from openprogram.state.paths import get_state_dir
    return get_state_dir() / "always_on" / "handoffs.json"


def load_handoffs() -> dict[str, Any]:
    """The removed handoff tool's turns, by user message id, until ``handoff_migration`` moves them."""
    return _read_json(_handoffs_path())


def save_handoffs(chains: dict[str, Any]) -> None:
    with lock("handoffs"):
        _write_json(_handoffs_path(), chains)


def load_agent_state(agent_id: str) -> dict[str, Any]:
    return _read_json(_agent_state_path(agent_id))


def update_agent_state(agent_id: str, **changes: Any) -> dict[str, Any]:
    with lock("agent:" + agent_id):
        state = load_agent_state(agent_id)
        before = dict(state)
        state.update(changes)
        _write_json(_agent_state_path(agent_id), state)
    if state != before:
        _wakes_changed("agent:" + agent_id)
    return state


def spent_today(agent_id: str, today: str) -> float:
    """The Agent's check-in spending on the local day ``today`` (``YYYY-MM-DD``)."""
    spend = load_agent_state(agent_id).get("spend") or {}
    return float(spend.get("usd") or 0.0) if spend.get("date") == today else 0.0


def add_spend(agent_id: str, today: str, usd: float) -> float:
    """Add one check-in's cost to the day's spending; returns the new total.

    A check-in that began before midnight and ended after it must not put the
    finished day back over the new day's count, so its cost is dropped, and
    0.0 returned, once a newer day has been recorded.
    """
    with lock("agent:" + agent_id):
        recorded = (load_agent_state(agent_id).get("spend") or {}).get("date")
        if isinstance(recorded, str) and recorded > today:
            return 0.0
        total = spent_today(agent_id, today) + max(0.0, float(usd or 0.0))
        update_agent_state(agent_id, spend={"date": today, "usd": round(total, 6)})
        return total


def _preferences_path(agent_id: str) -> Path:
    from openprogram.runtime.agent_registry.manager import agent_dir
    return agent_dir(agent_id) / "always_on" / "preferences.json"


def load_preferences(agent_id: str) -> list[dict[str, Any]]:
    """What the user told this Agent to report or leave out, oldest first."""
    rows = _read_json(_preferences_path(agent_id)).get("preferences")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def preference_note(rows: list[dict[str, Any]] | None) -> str:
    """The preference list as a model reads it; empty when there is none."""
    rules = [str(row.get("text") or "").strip() for row in rows or [] if row.get("text")]
    if not rules:
        return ""
    return ("The user told you what to report and what to leave out; follow it:\n"
            + "\n".join(f"- {rule}" for rule in rules))


def add_preference(agent_id: str, text: str) -> dict[str, Any]:
    """Keep one short rule; the same text again returns the saved row."""
    text = " ".join(str(text or "").split())[:PREFERENCE_MAX]
    if not text:
        raise ValueError("A preference needs text")
    with lock("preferences:" + agent_id):
        rows = load_preferences(agent_id)
        for row in rows:
            if str(row.get("text") or "").casefold() == text.casefold():
                return row
        row = {"id": uuid.uuid4().hex[:12], "text": text, "at": time.time()}
        rows = [*rows, row][-PREFERENCES_MAX:]
        _write_json(_preferences_path(agent_id), {"preferences": rows})
        return row


def remove_preference(agent_id: str, preference_id: str) -> bool:
    with lock("preferences:" + agent_id):
        rows = load_preferences(agent_id)
        kept = [row for row in rows if row.get("id") != preference_id]
        if len(kept) == len(rows):
            return False
        _write_json(_preferences_path(agent_id), {"preferences": kept})
        return True


def _wake_log_path(session_id: str) -> Path | None:
    """A real file beside the session log. A logged document is stored whole
    on every write, and this one is written at every firing."""
    folder = directory(session_id)
    return folder / "always_on_wake_log.jsonl" if folder else None


def append_wake_log(session_id: str, entry: dict[str, Any]) -> None:
    """Add one firing's outcome; only the newest ``MAX_WAKE_LOG`` are kept."""
    with lock(session_id):
        path = _wake_log_path(session_id)
        if path is None:
            return
        rows = [*reversed(load_wake_log(session_id, MAX_WAKE_LOG - 1)), entry]
        _replace_file(path, lambda handle: handle.writelines(
            json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def load_wake_log(session_id: str, limit: int = MAX_WAKE_LOG) -> list[dict[str, Any]]:
    """One row per firing, newest first."""
    return _newest_rows(_wake_log_path(session_id), limit)


def load_reports(session_id: str, limit: int = MAX_REPORTS) -> list[dict[str, Any]]:
    """Newest first."""
    return _newest_rows(_reports_path(session_id), limit)


def _newest_rows(path: Any, limit: int) -> list[dict[str, Any]]:
    if path is None or limit <= 0:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return []
    rows = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _clean_links(links: Any) -> list[str]:
    if not isinstance(links, list):
        return []
    return [str(link).strip()[:2000] for link in links if str(link).strip()][:LINKS_MAX]


def report_message_id(report: dict[str, Any]) -> str:
    """The id of the message a report is posted as."""
    return "report_" + str(report["id"])[:20]


def add_report(session_id: str, *, title: str, body: str, links: Any = None,
               kind: str = "finding", deliveries: list[dict[str, Any]] | None = None,
               execution_id: str | None = None, task_id: str | None = None,
               source: str | None = None, attached_files: Any = ()) -> dict[str, Any]:
    # A report is posted as a message from the Agent. A marker in what the
    # model wrote is not a file a tool call recorded, so it reads as the
    # name: only ``attached_files`` get a card (send_file.with_attachments).
    from openprogram.state.attachment_markers import markers_as_names
    attached_files = list(attached_files or ())
    title = markers_as_names(str(title or ""), attached_files).strip()
    body = markers_as_names(str(body or ""), attached_files)
    links = [markers_as_names(str(link), attached_files) for link in links] if isinstance(links, list) else links
    if not shows_anything(title):
        raise ValueError("A report needs a title")
    if kind not in REPORT_KINDS:
        raise ValueError("A report's kind is finding, approval or done")
    report = {
        "id": uuid.uuid4().hex,
        "at": time.time(),
        "kind": kind,
        "title": title[:TITLE_MAX],
        "body": str(body or "").strip()[:BODY_MAX],
        "links": _clean_links(links),
        "session_id": session_id,
    }
    if execution_id:
        # The turn that reported: a check-in counts its own reports by it.
        report["execution_id"] = execution_id
    if source:
        # Where it came from, when that is known: only a check-in sets this.
        report["source"] = source
    if task_id:
        # A scheduled task's result: it lives on that task's branch.
        report["task_id"] = task_id
    if deliveries:
        report["deliveries"] = deliveries
    if attached_files:
        # The files a tool call recorded: the only ones a channel may upload.
        report["attached_files"] = list(attached_files)
    with lock(session_id):
        _write_reports(session_id, [*reversed(load_reports(session_id, MAX_REPORTS - 1)), report])
    return report


def update_report(session_id: str, report_id: str, **changes: Any) -> dict[str, Any] | None:
    """Merge ``changes`` into one stored report; None when it was trimmed away."""
    with lock(session_id):
        rows = list(reversed(load_reports(session_id, MAX_REPORTS)))
        for row in rows:
            if row.get("id") == report_id:
                row.update(changes)
                _write_reports(session_id, rows)
                return row
    return None


def _write_reports(session_id: str, rows: list[dict[str, Any]]) -> None:
    """Oldest first, one JSON object per line."""
    path = _reports_path(session_id)
    if path is None:
        raise ValueError(f"conversation {session_id!r} not found")
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    from openprogram.host._platform import changes
    changes.signal(INBOX_TOPIC, session_id)
