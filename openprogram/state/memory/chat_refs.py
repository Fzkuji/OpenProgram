"""Memory cites the chat it came from.

A Topic footnote names the message a claim rests on, and the chat history
is the record of that message: memory keeps no copy of it. The reference
is ``openprogram/<session-id>/<message-id>`` inside the Runtime and in
retrieval results, and the footnote links it as ``/s/<session-id>#<message-id>``,
the chat page's own address, so following a citation opens the chat.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, unquote

PROVIDER = "openprogram"
_PART = r"[A-Za-z0-9][A-Za-z0-9_.:@%-]*"
_REF = re.compile(rf"{PROVIDER}/(?P<session>{_PART})/(?P<message>{_PART})")
_LINK = re.compile(rf"/s/(?P<session>{_PART})#(?P<message>{_PART})")
# What a chat-model memory edit cites when it names no message: the
# user message that asked for the edit.
CURRENT = "/s/current"

# Roles whose words are the conversation. Tool calls and results are a
# turn's machinery; runtime-scheduled turns are nobody's words.
_ROLES = ("user", "assistant")
RUNTIME_SOURCES = frozenset({"job_followup", "merge_turn", "process_exit"})


def ref(session_id: str, message_id: str) -> str:
    return f"{PROVIDER}/{session_id}/{message_id}"


def split(value: str) -> tuple[str, str] | None:
    """(session id, message id) of a reference, or None if it is not one."""
    match = _REF.fullmatch(str(value or "").strip())
    if match is None:
        return None
    return unquote(match.group("session")), unquote(match.group("message"))


def link(value: str) -> str:
    """The footnote link target for a reference."""
    parts = split(value)
    if parts is None:
        raise ValueError(f"invalid chat reference: {value}")
    session_id, message_id = parts
    return f"/s/{quote(session_id, safe='-_.:@')}#{quote(message_id, safe='-_.:@')}"


def from_target(target: str) -> str | None:
    """The reference a footnote link target names, or None.

    Accepts the link form and the bare reference, which is what a writer
    is shown beside each message and may paste as it is.
    """
    value = str(target or "").strip()
    match = _LINK.fullmatch(value)
    if match is not None:
        return ref(unquote(match.group("session")), unquote(match.group("message")))
    return value if split(value) is not None else None


def _text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"
            and block.get("text")
        )
    return ""


def is_runtime_turn(message: dict[str, Any]) -> bool:
    """A turn the runtime scheduled, not one anybody said.

    A sub-agent's completion notice and a merge prompt are written as
    user-role rows so the model has something to answer, and the chat
    marks them ``display="runtime"``. The reply carries the same
    ``source`` as the trigger, so this covers both halves of the turn.
    A posted always-on report is the runtime's too: it is routine output,
    and what the owner says back to it is what memory learns from.
    """
    return (
        message.get("display") == "runtime"
        or message.get("source") in RUNTIME_SOURCES
        or bool(message.get("always_on_report"))
    )


def citable(message: dict[str, Any]) -> bool:
    """Whether memory may rest a claim on this chat message.

    What a person said and what the assistant replied, from a speaker
    whose authority lets their words become memory.
    """
    return refusal(message) is None


def refusal(message: dict[str, Any]) -> str | None:
    """Why memory may not rest a claim on this message, or None if it may."""
    from openprogram.state.authority import (
        has_capability,
        is_legacy_message,
        normalize_authority,
    )

    from .policy import source_matches_execution, source_refusal

    if message.get("role") not in _ROLES or is_runtime_turn(message):
        return "it is not something a person said or the assistant replied"
    if not source_matches_execution(message):
        return source_refusal(message)
    if not _text(message).strip() or not str(message.get("id") or "").strip():
        return "it has no text"
    authority = normalize_authority(message)
    if not authority:
        # Only history from before authority existed lacks it, and that is
        # the local owner's own conversation.
        if is_legacy_message(message):
            return None
        return "its speaker may not be a source for memory"
    if has_capability(authority, "memory.source.append"):
        return None
    return "its speaker may not be a source for memory"


def observed_at(value: Any) -> str | None:
    """A session row's Unix stamp as ISO 8601 in the machine's own zone."""
    if value is None or value == "":
        return None
    try:
        return datetime.fromtimestamp(
            float(value), timezone.utc
        ).astimezone().isoformat()
    except (TypeError, ValueError):
        return str(value).strip() or None


class ChatLookup:
    """Reads cited messages from the session store, one session at a time.

    A commit checks every new reference; caching per session keeps that
    to one read of each conversation.
    """

    def __init__(self, store: Any | None = None):
        self._store = store
        self._sessions: dict[str, dict[str, tuple[int, dict[str, Any]]]] = {}
        self._order: dict[str, list[dict[str, Any]]] = {}

    def _db(self) -> Any:
        if self._store is None:
            from openprogram.state.store.current import current_store

            self._store = current_store()
        return self._store

    def _messages(self, session_id: str) -> dict[str, tuple[int, dict[str, Any]]]:
        if session_id not in self._sessions:
            try:
                rows = self._db().get_messages(session_id) or []
            except Exception:  # noqa: BLE001 - an unreadable chat cites nothing
                rows = []
            self._order[session_id] = rows
            self._sessions[session_id] = {
                str(row.get("id")): (index, row)
                for index, row in enumerate(rows) if row.get("id")
            }
        return self._sessions[session_id]

    def message(self, value: str) -> dict[str, Any] | None:
        parts = split(value)
        if parts is None:
            return None
        found = self._messages(parts[0]).get(parts[1])
        return found[1] if found else None

    def _retracted(self, session_id: str, message_id: str) -> dict[str, Any] | None:
        """The message a rewind retracted, as it was stored, or None."""
        try:
            pair = self._db()._open(session_id)  # noqa: SLF001 - the raw nodes
            if pair is None:
                return None
            index = pair[1]
            if message_id not in set(index.meta.get("rewind_hidden_nodes") or ()):
                return None
            node = index.nodes_by_id.get(message_id)
            if node is None:
                return None
            from openprogram.state.store.session._msg_adapter import _node_to_msg

            return _node_to_msg(node, session_id)
        except Exception:  # noqa: BLE001 - read as deleted
            return None

    def citable(self, value: str) -> bool:
        message = self.message(value)
        return message is not None and citable(message)

    def refusal(self, value: str) -> str | None:
        """Why this reference may not be cited, or None if it may."""
        message = self.message(value)
        if message is None:
            return "the chat no longer holds it"
        return refusal(message)

    def excerpt(self, value: str, *, before: int = 2, after: int = 2) -> dict[str, Any]:
        """The cited message with the conversation around it.

        ``deleted`` when the chat no longer holds it: the memory stays,
        the original is gone.
        """
        parts = split(value)
        if parts is None:
            raise ValueError(f"invalid chat reference: {value}")
        session_id, message_id = parts
        found = self._messages(session_id).get(message_id)
        if found is None:
            retracted = self._retracted(session_id, message_id)
            if retracted is not None:
                # A rewind took it out of the conversation in place; it is
                # still stored, so the memory still shows what it rests on.
                return {
                    "ref": value, "session_id": session_id, "message_id": message_id,
                    "retracted": True,
                    "turns": [{
                        "message_id": message_id, "role": retracted.get("role"),
                        "speaker": retracted.get("speaker_display") or retracted.get("role"),
                        "at": observed_at(retracted.get("timestamp")),
                        "text": _text(retracted), "cited": True,
                    }],
                }
            return {"ref": value, "session_id": session_id, "deleted": True}
        index, _message = found
        rows = self._order[session_id]
        window = [
            row for row in rows[max(0, index - before):index + after + 1]
            if row.get("role") in _ROLES and not is_runtime_turn(row)
            and _text(row).strip()
        ]
        return {
            "ref": value,
            "session_id": session_id,
            "message_id": message_id,
            "turns": [
                {
                    "message_id": str(row.get("id")),
                    "role": row.get("role"),
                    "speaker": row.get("speaker_display") or row.get("role"),
                    "at": observed_at(row.get("timestamp")),
                    "text": _text(row),
                    "cited": str(row.get("id")) == message_id,
                }
                for row in window
            ],
        }


def render_excerpt(excerpt: dict[str, Any]) -> str:
    """An excerpt as Markdown, the cited turn marked."""
    if excerpt.get("deleted"):
        return "Original session deleted. The memory remains available."
    lines = ["This message was retracted by a rewind. It is shown as it was."] if excerpt.get("retracted") else []
    for turn in excerpt["turns"]:
        mark = "**→** " if turn["cited"] else ""
        when = f" · {turn['at'][:16].replace('T', ' ')}" if turn.get("at") else ""
        lines.append(f"{mark}**{turn['speaker']}**{when}\n\n{turn['text']}")
    return "\n\n---\n\n".join(lines)
