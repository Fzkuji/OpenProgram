"""cron tool — schedule recurring agent tasks.

Persists cron entries to a JSON file. A separate daemon (not shipped in
this commit) is responsible for actually waking up and firing them.
Ships the registration surface first so agents / users can describe
schedules now, and we can land the executor in a follow-up.

Storage: ``$OPENPROGRAM_CRON_PATH`` or ``~/.openprogram/cron/schedule.json``.
Each entry is ``{"id", "cron", "prompt", "created_at", "notes"}``. ``id`` is
a stable 8-char hex slug so the agent can delete by a name it knows.

Actions:

  create  add a new schedule — requires ``cron`` expression + ``prompt``
  list    return all schedules
  delete  remove a schedule by id
  get     read a single schedule by id

Cron expression is validated loosely (five whitespace-separated fields);
we don't parse the wildcard semantics here — that belongs to the
executor. Keeping validation lenient means users can use any standard
cron dialect (Vixie, Quartz-ish, @daily macros) and the daemon decides
what it supports.

Credit: shape follows openclaw / hermes cron tools; execution layer
is deferred.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any

from .._helpers import read_string_param


NAME = "cron"

DEFAULT_CRON_ENV = "OPENPROGRAM_CRON_PATH"
DEFAULT_REL_PATH = "cron/schedule.json"

DESCRIPTION = (
    "Register / list / delete recurring agent tasks. Entries are persisted "
    "to a JSON file; the companion `openprogram cron-worker` process fires "
    "each `prompt` when its `cron` expression matches. Creating an entry "
    "only schedules it — if no worker is running, entries accumulate until "
    "one starts."
)


SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "delete", "get"],
                "description": "What to do.",
            },
            "cron": {
                "type": "string",
                "description": "5-field cron expression, e.g. `0 9 * * *`. Required for create.",
            },
            "prompt": {
                "type": "string",
                "description": "The prompt / task the daemon should hand to a fresh agent when the schedule fires. Required for create.",
            },
            "notes": {
                "type": "string",
                "description": "Optional free-form notes for your own reference.",
            },
            "id": {
                "type": "string",
                "description": "Entry id. Required for delete / get.",
            },
        },
        "required": ["action"],
    },
}


_CRON_RE = re.compile(r"^\s*(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")
_CRON_MACROS = {"@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight", "@hourly", "@reboot"}


def _valid_cron(expr: str) -> bool:
    if expr.strip().lower() in _CRON_MACROS:
        return True
    return bool(_CRON_RE.match(expr))


def _resolve_path() -> str:
    override = os.environ.get(DEFAULT_CRON_ENV)
    if override:
        return os.path.abspath(override)
    return os.path.expanduser(f"~/.openprogram/{DEFAULT_REL_PATH}")


def _load(path: str) -> list[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if isinstance(data, dict) and isinstance(data.get("entries"), list):
        return [e for e in data["entries"] if isinstance(e, dict)]
    return []


def _save(path: str, entries: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"entries": entries}, f, indent=2, ensure_ascii=False)


def _mint_id() -> str:
    return uuid.uuid4().hex[:8]


def execute(
    action: str | None = None,
    cron: str | None = None,
    prompt: str | None = None,
    notes: str | None = None,
    id: str | None = None,
    **kw: Any,
) -> str:
    action = action or read_string_param(kw, "action", "op")
    cron_expr = cron or read_string_param(kw, "cron", "schedule", "expression")
    prompt = prompt or read_string_param(kw, "prompt", "task", "text")
    notes = notes or read_string_param(kw, "notes", "note", "description")
    entry_id = id or read_string_param(kw, "id", "entry_id", "slug")

    if not action:
        return "Error: `action` is required (create / list / delete / get)."
    action = action.lower()

    path = _resolve_path()
    entries = _load(path)

    if action == "list":
        if not entries:
            return f"No cron entries in `{path}`."
        lines = [f"Cron entries in `{path}`:"]
        for e in entries:
            line = f"- `{e.get('id','?')}`  {e.get('cron','?')}  → {(e.get('prompt') or '')[:80]}"
            if e.get("notes"):
                line += f"   _({e['notes']})_"
            lines.append(line)
        return "\n".join(lines)

    if action == "get":
        if not entry_id:
            return "Error: `id` is required for get."
        for e in entries:
            if e.get("id") == entry_id:
                return json.dumps(e, indent=2, ensure_ascii=False)
        return f"Error: no entry with id {entry_id!r}."

    if action == "delete":
        if not entry_id:
            return "Error: `id` is required for delete."
        keep = [e for e in entries if e.get("id") != entry_id]
        if len(keep) == len(entries):
            return f"Error: no entry with id {entry_id!r}."
        _save(path, keep)
        return f"Deleted cron entry {entry_id!r} from `{path}`."

    if action == "create":
        if not cron_expr:
            return "Error: `cron` expression is required for create."
        if not prompt:
            return "Error: `prompt` is required for create."
        if not _valid_cron(cron_expr):
            return (
                f"Error: {cron_expr!r} doesn't look like a cron expression "
                "(want 5 fields like `0 9 * * *`, or a macro like `@daily`)."
            )
        new_entry = {
            "id": _mint_id(),
            "cron": cron_expr.strip(),
            "prompt": prompt,
            "notes": notes or "",
            "created_at": int(time.time()),
        }
        entries.append(new_entry)
        _save(path, entries)
        return (
            f"Created cron entry `{new_entry['id']}` in `{path}`:\n"
            f"  schedule: {new_entry['cron']}\n"
            f"  prompt:   {prompt[:160]}\n"
            "Start the worker in another shell to fire entries:\n"
            "  openprogram cron-worker            # run until Ctrl+C\n"
            "  openprogram cron-worker --list     # show which entries match now"
        )

    return f"Error: unknown action {action!r}. Expected create / list / delete / get."


__all__ = ["NAME", "SPEC", "execute", "DESCRIPTION"]
