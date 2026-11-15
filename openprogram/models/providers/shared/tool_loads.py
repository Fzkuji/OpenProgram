"""Load records: how a deferred tool becomes available, as data in history.

Loading a deferred tool is written into the result of the call that loaded
it, as ``ToolResultMessage.details["loads"]``: one ``{"name", "definition"}``
per tool, the definition being the tool's ``{"name", "description",
"parameters"}`` at the moment it was loaded. A provider that loads a
searched tool from the search's own result (the Responses
``tool_search_output``) sends that definition as it was stored, so an
aged, truncated or re-rendered result text and a later schema change
leave the bytes already sent alone.

The record is a durable detail of a tool result (``durable_details``),
with the attachment sent after it and where its whole text is kept: the
tool node keeps them in its metadata, and rendering history puts them back
on the ``ToolResultMessage``.
"""
from __future__ import annotations

import re
from typing import Any

#: The ``details`` key of a load record.
LOADS = "loads"

#: The ``details`` key of a search's note: what a ``tool_search`` call says
#: beyond the definitions it loaded (a requested tool is already callable, a
#: name is unknown, nothing matched). A provider that sends a search's result
#: as definitions alone sends the note beside it (``search_note``).
NOTE = "note"

#: The ``details`` keys a tool node keeps beside its result text: the load
#: record, a search's note, the attachment sent after the result
#: (``shared.tool_attachments``) and where a bounded result's whole text is
#: (``OUTPUT``).
DURABLE_DETAIL_KEYS = (LOADS, NOTE, "attachment", "output")

_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def valid_tool_name(name: Any) -> bool:
    """Whether ``name`` can name a loaded tool on every provider."""
    return isinstance(name, str) and bool(_TOOL_NAME_RE.match(name))


def tool_definition(tool: Any) -> dict[str, Any]:
    """The provider-neutral definition of ``tool`` as a load record keeps it."""
    return {
        "name": tool.name,
        "description": tool.description or "",
        "parameters": dict(tool.parameters or {}),
    }


def load_records(details: Any) -> list[dict[str, Any]]:
    """The well-formed ``{"name", "definition"}`` records in ``details``.

    A call that was stopped or never ran (``details["stopped"]``) loaded
    nothing, whatever else its details hold."""
    if not isinstance(details, dict) or details.get("stopped"):
        return []
    records: list[dict[str, Any]] = []
    for entry in details.get(LOADS) or ():
        if not isinstance(entry, dict) or not valid_tool_name(entry.get("name")):
            continue
        definition = entry.get("definition")
        if (not isinstance(definition, dict)
                or definition.get("name") != entry["name"]
                or not isinstance(definition.get("parameters"), dict)):
            continue
        records.append(entry)
    return records


def search_note(details: Any) -> str:
    """The note of the search ``details`` belongs to, or ""."""
    if not isinstance(details, dict) or details.get("stopped"):
        return ""
    note = details.get(NOTE)
    return note.strip() if isinstance(note, str) else ""


def loaded_names(details: Any) -> list[str]:
    """Names of the tools ``details`` records as loaded, in load order."""
    return list(dict.fromkeys(entry["name"] for entry in load_records(details)))


def carried_records(message: Any) -> list[dict[str, Any]]:
    """The tools a compaction summary carries past what it covers.

    A summary renders as a message (user, or assistant for one written
    before summaries became user input) whose ``carried_tools`` are
    ``{"name", "definition"}`` records, most recently used first; the
    definition is the one the history first sent, or absent when the
    history kept none.
    """
    records: list[dict[str, Any]] = []
    for entry in getattr(message, "carried_tools", None) or ():
        if isinstance(entry, dict) and valid_tool_name(entry.get("name")):
            records.append(entry)
    return records


def loaded_tools(messages: Any) -> list[str]:
    """Names of the deferred tools loaded in a request's ``messages``.

    The one reading of the loaded set: every load record of a tool result
    in the messages, and the names a compaction summary among them carries,
    in the order they appear. Nothing else holds it, so a request loads what
    its own messages loaded: a nested run reads its own messages, a branch
    its own history, and a resumed turn the history it resumes.
    """
    names: dict[str, None] = {}
    for message in messages or ():
        role = getattr(message, "role", None)
        if role in ("assistant", "user"):
            for entry in carried_records(message):
                names.setdefault(entry["name"], None)
        elif role == "toolResult" and not getattr(message, "is_error", False):
            for name in loaded_names(getattr(message, "details", None)):
                names.setdefault(name, None)
    return list(names)


def loaded_records(messages: Any) -> dict[str, dict[str, Any]]:
    """``{name: definition}`` for each tool ``messages`` loaded, the
    definition being the first one sent for it: from a tool result's load
    record, or from what a compaction summary carries. A carried name whose
    summary kept no definition maps to ``{}``. Same order as
    :func:`loaded_tools`."""
    found: dict[str, dict[str, Any]] = {}
    for message in messages or ():
        role = getattr(message, "role", None)
        if role in ("assistant", "user"):
            for entry in carried_records(message):
                definition = entry.get("definition")
                found.setdefault(entry["name"], dict(definition) if isinstance(definition, dict) else {})
        elif role == "toolResult" and not getattr(message, "is_error", False):
            for entry in load_records(getattr(message, "details", None)):
                found.setdefault(entry["name"], dict(entry["definition"]))
    return found


def result_loaded(messages: Any) -> list[str]:
    """Names loaded by tool results' own records, leaving out what only a
    compaction summary carries (the Anthropic ``defer_loading`` items)."""
    names: dict[str, None] = {}
    for message in messages or ():
        if getattr(message, "role", None) == "toolResult" and not getattr(message, "is_error", False):
            for name in loaded_names(getattr(message, "details", None)):
                names.setdefault(name, None)
    return list(names)


def durable_details(details: Any) -> dict[str, Any] | None:
    """The part of a tool result's ``details`` its tool node keeps, or None."""
    if not isinstance(details, dict):
        return None
    kept = {key: details[key] for key in DURABLE_DETAIL_KEYS if details.get(key)}
    return kept or None
