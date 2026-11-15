"""Attachments: instructions a tool call brought along, sent beside its result.

When a call is due a tool guide (``programs._authoring.guides``) or reaches
a directory with an ``AGENTS.md`` the request does not carry yet
(``runtime.context.agents_md``), that text is not joined into the tool's
own result. It is recorded on the result as
``ToolResultMessage.details["attachment"]``:
``{"text", "paths", "guides"}``, the text bounded once when it was made.

Every adapter sends a group of consecutive tool results, then one message
with the group's attachments (``attachment_message``): a text block after
the ``tool_result`` blocks of the same user message on Anthropic and
Bedrock, a user message after the results elsewhere. The record lives on
the result in history, so a request built from history sends the same
message the turn sent.

The record is also the ledger of what the model has been shown: the
``AGENTS.md`` files a request's messages already attach
(``attached_paths``) are not attached again. A branch reads its own
history, a compaction drops what it summarized (the next call that
reaches the directory attaches the file again), and a restarted Worker
reads the same history, so nothing is kept in memory.
"""
from __future__ import annotations

from typing import Any, Iterable

from ..types import TextContent, UserMessage

#: The ``details`` key of an attachment record.
ATTACHMENT = "attachment"


def record(details: Any) -> dict[str, Any] | None:
    """The well-formed attachment record in ``details``, or None."""
    if not isinstance(details, dict):
        return None
    entry = details.get(ATTACHMENT)
    if not isinstance(entry, dict) or not isinstance(entry.get("text"), str) or not entry["text"]:
        return None
    return entry


def with_attachment(details: Any, text: str, *, paths: Iterable[str] = (),
                    guides: Iterable[str] = (), front: bool = False) -> dict[str, Any] | None:
    """``details`` with ``text`` added to its attachment record (after what
    is there, or in front of it with ``front``)."""
    base = dict(details) if isinstance(details, dict) else {}
    if not text:
        return base or (details if isinstance(details, dict) else None)
    current = record(base) or {"text": "", "paths": [], "guides": []}
    parts = [text, current["text"]] if front else [current["text"], text]
    base[ATTACHMENT] = {
        "text": "\n\n".join(part for part in parts if part),
        "paths": list(dict.fromkeys([*(current.get("paths") or []), *paths])),
        "guides": list(dict.fromkeys([*(current.get("guides") or []), *guides])),
    }
    return base


def _field(message: Any, name: str) -> Any:
    return message.get(name) if isinstance(message, dict) else getattr(message, name, None)


def attached_paths(messages: Any) -> set[str]:
    """The ``AGENTS.md`` paths the tool results in ``messages`` (objects or
    their dict form) attach."""
    seen: set[str] = set()
    for message in messages or ():
        if _field(message, "role") != "toolResult":
            continue
        entry = record(_field(message, "details"))
        if entry:
            seen.update(str(path) for path in entry.get("paths") or () if path)
    return seen


def attachment_text(results: Iterable[Any]) -> str:
    """The text one group of tool results sends after the results."""
    texts = [entry["text"] for entry in (record(getattr(m, "details", None)) for m in results) if entry]
    return "\n\n".join(texts)


def attachment_message(results: list[Any]) -> Any:
    """The message sent after a group of tool results, or None.

    It is an ``AttachmentMessage``: a ``UserMessage`` an adapter may fold
    into the message that carries the results (Anthropic, Bedrock)."""
    text = attachment_text(results)
    if not text:
        return None
    return AttachmentMessage(content=[TextContent(text=text)],
                             timestamp=getattr(results[-1], "timestamp", 0) or 0)


def with_attachment_messages(messages: list[Any]) -> list[Any]:
    """``messages`` with each group of consecutive tool results followed by
    its attachment message."""
    out: list[Any] = []
    group: list[Any] = []
    for message in messages:
        if getattr(message, "role", None) == "toolResult":
            group.append(message)
            out.append(message)
            continue
        if group:
            extra = attachment_message(group)
            if extra is not None:
                out.append(extra)
            group = []
        out.append(message)
    if group:
        extra = attachment_message(group)
        if extra is not None:
            out.append(extra)
    return out


class AttachmentMessage(UserMessage):
    """A user message holding the attachments of the tool results before it."""


__all__ = [
    "ATTACHMENT", "AttachmentMessage", "attached_paths", "attachment_message",
    "attachment_text", "record", "with_attachment", "with_attachment_messages",
]
