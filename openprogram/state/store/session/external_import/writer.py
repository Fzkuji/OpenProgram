"""Write parsed turns into a session as the DAG a native turn produces.

Per turn: a user node chained after the previous reply (``caller=ROOT``),
one llm reply node holding the reply text plus the ordered ``blocks`` /
``tool_calls`` the chat renders, and one code node per tool call hanging
off the reply (``caller=<reply id>``). Ids are derived from the source,
so writing the same turn twice is a no-op.

Every node is stamped ``imported_from`` — the chat uses it to label the
conversation, and the context renderer uses it to replay the other
agent's tool calls as text, since those tools do not exist here.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Optional

from openprogram.state.store.session.nodes import Call, ROLE_CODE, ROLE_LLM, ROLE_USER

from .model import ExternalSession, ToolBlock, Turn

_ROOT_ID = "ROOT"
_UNSAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")


def node_prefix(agent: str, source_id: str) -> str:
    digest = hashlib.sha1(f"{agent}:{source_id}".encode("utf-8")).hexdigest()[:10]
    return f"imp{digest}"


def user_id(prefix: str, index: int) -> str:
    return f"{prefix}_{index:04d}"


def reply_id(prefix: str, index: int) -> str:
    return f"{user_id(prefix, index)}_reply"


def _tool_node_id(reply: str, call_id: str, taken: set[str]) -> str:
    base = f"{reply}_t_{_UNSAFE_ID.sub('_', call_id)[:80]}"
    node_id, n = base, 1
    while node_id in taken:
        n += 1
        node_id = f"{base}_{n}"
    taken.add(node_id)
    return node_id


def _input_text(arguments: Any) -> str:
    return json.dumps(arguments, ensure_ascii=False, default=str)


def _blocks(turn: Turn) -> tuple[list[dict], list[dict]]:
    """The reply's ordered ``blocks`` and slim ``tool_calls`` lists."""
    blocks: list[dict] = []
    calls: list[dict] = []
    for block in turn.blocks:
        if isinstance(block, ToolBlock):
            call = {
                "tool": block.name,
                "tool_call_id": block.call_id,
                "input": _input_text(block.arguments),
                "result": block.result,
                "is_error": block.is_error,
            }
            blocks.append({"type": "tool", **call})
            calls.append({"id": block.call_id, **call})
        else:
            blocks.append(dict(block))
    return blocks, calls


def with_pictures(store, session_id: str, turn: Turn, text: Optional[str] = None) -> tuple[str, list[dict]]:
    """``text`` (``turn``'s own by default) with each picture's placeholder made an
    attachment marker of its bytes, kept in the conversation's ``media/`` as a picture
    sent here is, and the message's ``metadata.media`` (so a turn here sends the
    pictures again). A picture with no placeholder of its own follows the one before
    it; one whose bytes the transcript lacks, or that cannot be kept, stays as it was."""
    text = turn.user_text if text is None else text
    if not any(data for data, _mime, _place in turn.images):
        return text, []
    from pathlib import Path
    from openprogram.state.attachment_markers import format_marker
    from openprogram.state.store.session.files import media as _media
    try:
        session_dir = store._session_dir(session_id)  # noqa: SLF001 - the conversation's own folder
    except Exception:  # noqa: BLE001
        return text, []
    kept: list[dict] = []
    cursor = 0  # where the picture before ended in ``text``
    for data, mime, place in turn.images:
        at = text.find(place, cursor) if place else -1
        record = _media.save(session_dir, data, mime) if data else None
        size = None
        if record is not None:
            try:
                size = Path(record["path"]).stat().st_size
            except OSError:
                record = None
        if record is None:
            cursor = at + len(place) if at >= 0 else cursor
            continue
        kept.append(record)
        name = f"image-{len(kept)}{_media.kept_path(record, session_dir).suffix}"
        marker = format_marker(name, record["path"], size or 0, mime)
        if at >= 0:
            text = text[:at] + marker + text[at + len(place):]
            cursor = at + len(marker)
        else:
            before = "\n\n" if cursor else ""
            after = "\n\n" if not cursor and text else ""
            text = text[:cursor] + before + marker + after + text[cursor:]
            cursor += len(before) + len(marker)
    # The model reads the text first, then the pictures (as a turn sent here does).
    first = 1 if text.strip() else 0
    return text, [{**record, "index": first + n} for n, record in enumerate(kept)]


def _put(store, writer, session_id: str, node: Call) -> None:
    """Append ``node``, or refresh it when an earlier import wrote it."""
    if store.message_exists(session_id, node.id):
        store.update_node(session_id, node.id, output=node.output, name=node.name,
                          input=node.input, metadata=node.metadata)
    else:
        writer.append(node)


def write_turns(
    store,
    session_id: str,
    session: ExternalSession,
    *,
    start: int = 0,
    predecessor: str = _ROOT_ID,
    indices: Optional[list[int]] = None,
    offset: int = 0,
) -> Optional[str]:
    """Write ``session.turns[start:]`` (or the turns at ``indices``, in
    order, each after the one before); return the last reply's id.

    A turn's nodes are numbered by its index plus ``offset``: after an
    earlier reading counted turns this one does not, new turns are numbered
    past what was written then.

    Turns already in the session are refreshed in place (same ids), so
    a turn that was still running when first imported gets completed.
    """
    from openprogram.state.store import SessionNodeWriter

    writer = SessionNodeWriter(store, session_id, advance_head=False)
    if not store.message_exists(session_id, _ROOT_ID):
        writer.append(Call(id=_ROOT_ID, created_at=session.started_at or time.time(),
                           role=ROLE_USER, output="", metadata={"display": "root"}))
    stamp = {"agent": session.agent, "source_id": session.source_id}
    prefix = node_prefix(session.agent, session.source_id)
    last: Optional[str] = None
    previous = predecessor
    for index in (indices if indices is not None else range(start, len(session.turns))):
        turn = session.turns[index]
        started = turn.timestamp or time.time()
        uid, rid = user_id(prefix, index + offset), reply_id(prefix, index + offset)
        shown, media = with_pictures(store, session_id, turn)
        _put(store, writer, session_id, Call(
            id=uid, created_at=started, role=ROLE_USER, output=shown,
            caller=_ROOT_ID, predecessor=previous,
            metadata={"source": "import", "imported_from": stamp,
                      "import_fingerprint": turn.fingerprint(), **({"media": media} if media else {})},
        ))
        blocks, calls = _blocks(turn)
        reply_meta: dict[str, Any] = {
            "status": "completed" if turn.blocks else "cancelled",
            "source": "import",
            "imported_from": stamp,
            "started_at": started,
            "completed_at": turn.ended_at or started,
        }
        if turn.model:
            reply_meta["model"] = reply_meta["token_model"] = turn.model
        if blocks:
            reply_meta["extra"] = json.dumps({"tool_calls": calls, "blocks": blocks},
                                             ensure_ascii=False, default=str)
        _put(store, writer, session_id, Call(
            id=rid, created_at=started, role=ROLE_LLM, name=turn.model,
            output=turn.reply_text(), predecessor=uid, metadata=reply_meta,
        ))
        taken: set[str] = set()
        for tool in turn.tools():
            _put(store, writer, session_id, Call(
                id=_tool_node_id(rid, tool.call_id, taken),
                created_at=tool.timestamp or started, role=ROLE_CODE,
                name=tool.name, input=tool.arguments, output=tool.result,
                caller=rid,
                metadata={"tool_call_id": tool.call_id, "is_error": tool.is_error,
                          "imported_from": stamp},
            ))
        previous = last = rid
    return last
