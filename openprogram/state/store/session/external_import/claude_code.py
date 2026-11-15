"""Read Claude Code transcripts (``~/.claude/projects/<slug>/<id>.jsonl``).

Each line is one record. Conversation records (``user`` / ``assistant``)
carry ``uuid`` and ``parentUuid``; a rewind leaves the abandoned records
in the file and starts a new chain from an earlier parent, and a
compaction restarts the chain with ``logicalParentUuid`` pointing back
across the boundary. The conversation the user last saw is therefore the
chain walked back from the newest record, not the file in order.

Assistant records hold one content block each (text, thinking or
tool_use); tool results come back as ``tool_result`` blocks inside the
next user record. Subagent transcripts live in their own files and are
marked ``isSidechain``; their outcome already reaches the main chain as
the Task tool's result, so they are not imported separately.

A session's identity is its file name. ``sessionId`` inside the records
is not: a forked session starts with copies of its parent's records,
parent ``sessionId`` included.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterator, Optional

from .model import CLAUDE_CODE, Candidate, ExternalSession, ToolBlock, Turn
from .sanitize import clean_user_text, is_command
from .timeparse import fallback_title, last_message_time, parse_timestamp

_TITLE_MARKER = re.compile(rb'"(?:custom-title|ai-title|summary)"')
_CHUNK = 8 << 20


def default_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def discover(home: Optional[Path] = None) -> list[Path]:
    root = (home or default_home()) / "projects"
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*/*.jsonl") if p.is_file())


def _records(path: Path) -> Iterator[dict[str, Any]]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def _title_from(record: dict[str, Any], titles: dict[str, str]) -> None:
    kind = record.get("type")
    if kind == "custom-title" and str(record.get("customTitle") or "").strip():
        titles["custom"] = record["customTitle"].strip()
    elif kind == "ai-title" and str(record.get("aiTitle") or "").strip():
        titles["ai"] = record["aiTitle"].strip()
    elif kind == "summary" and str(record.get("summary") or "").strip():
        titles["summary"] = record["summary"].strip()


def _absorb(raw: bytes, scan: dict[str, Any]) -> None:
    """Take title, cwd and the first user message from one record line."""
    try:
        record = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return
    if not isinstance(record, dict):
        return
    _title_from(record, scan["titles"])
    scan["cwd"] = scan["cwd"] or record.get("cwd")
    if not scan["first_user"] and _is_user_turn_record(record):
        scan["first_user"] = _user_text(record.get("message", {}).get("content"))


def _pick_title(titles: dict[str, str], first_user: str) -> str:
    return (titles.get("custom") or titles.get("ai") or titles.get("summary")
            or fallback_title(first_user))


def summarize(path: Path, scan: Optional[dict[str, Any]] = None) -> Optional[Candidate]:
    """Title, cwd and recency without parsing every record.

    Only lines that can hold a title, plus the leading records up to the
    first real user message, are decoded; a long transcript is mostly
    tool output and does not need to be.

    ``scan`` carries what an earlier call found and where it stopped.
    Transcripts only grow, so a later call reads just the bytes appended
    since — listing gigabytes of history stays cheap. The dict is
    updated in place; a file that shrank or was replaced is rescanned.
    """
    if scan is None:
        scan = {}
    try:
        stat = path.stat()
        if (scan.get("inode") != stat.st_ino
                or int(scan.get("offset") or 0) > stat.st_size):
            scan.clear()
            scan.update(inode=stat.st_ino, offset=0, titles={}, cwd=None, first_user="")
        offset = int(scan["offset"])
        with open(path, "rb") as fh:
            fh.seek(offset)
            # Until the first user message and cwd are known, every record.
            while not scan["first_user"] or scan["cwd"] is None:
                raw = fh.readline()
                if not raw.endswith(b"\n"):
                    break  # end of file, or a record still being written
                offset += len(raw)
                _absorb(raw, scan)
            else:
                # After that only title records matter: search big chunks
                # for their markers rather than splitting every line.
                pending = b""
                while chunk := fh.read(_CHUNK):
                    buf = pending + chunk
                    end = buf.rfind(b"\n") + 1  # complete lines only
                    done = 0
                    for hit in _TITLE_MARKER.finditer(buf, 0, end):
                        if hit.start() < done:
                            continue
                        start = buf.rfind(b"\n", 0, hit.start()) + 1
                        done = buf.index(b"\n", hit.end()) + 1
                        _absorb(buf[start:done], scan)
                    offset += end
                    pending = buf[end:]
        scan["offset"] = offset
    except OSError:
        return None
    cwd, first_user = scan["cwd"], scan["first_user"]
    if not first_user:
        return None
    return Candidate(
        agent=CLAUDE_CODE, source_id=path.stem, path=path,
        cwd=cwd, title=_pick_title(scan["titles"], first_user),
        updated_at=last_message_time(path, ("user", "assistant")) or stat.st_mtime,
        size=stat.st_size,
    )


def _is_user_turn_record(record: dict[str, Any]) -> bool:
    return (record.get("type") == "user"
            and not record.get("isMeta")
            and not record.get("isSidechain")
            and not record.get("isCompactSummary"))


def _user_text(content: Any) -> str:
    """The typed text of a user record; tool results are not text."""
    if isinstance(content, str):
        return clean_user_text(content)
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            cleaned = clean_user_text(block.get("text") or "")
            if cleaned:
                parts.append(cleaned)
        elif block.get("type") == "image":
            parts.append("[image]")
    # An image alone (with no text) is still something the user sent.
    return "\n\n".join(parts).strip()


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text") or "")
                elif block.get("type") == "image":
                    parts.append("[image]")
        return "\n".join(parts)
    return "" if content is None else json.dumps(content, ensure_ascii=False)


def _active_chain(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The records on the chain that ends at the newest conversation record."""
    by_uuid = {r["uuid"]: r for r in records if isinstance(r.get("uuid"), str)}
    leaf = next((r for r in reversed(records)
                 if r.get("type") in ("user", "assistant")
                 and not r.get("isSidechain") and r.get("uuid")), None)
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current = leaf
    while current is not None and current["uuid"] not in seen:
        seen.add(current["uuid"])
        chain.append(current)
        parent = current.get("parentUuid") or current.get("logicalParentUuid")
        current = by_uuid.get(parent) if isinstance(parent, str) else None
    chain.reverse()
    return chain


def parse(path: Path) -> ExternalSession:
    records = list(_records(path))
    titles: dict[str, str] = {}
    for record in records:
        _title_from(record, titles)
    session = ExternalSession(
        agent=CLAUDE_CODE,
        source_id=path.stem,
        path=path,
        cwd=next((r["cwd"] for r in records if r.get("cwd")), None),
    )

    turn: Optional[Turn] = None
    pending: dict[str, ToolBlock] = {}
    for record in _active_chain(records):
        ts = parse_timestamp(record.get("timestamp"))
        message = record.get("message") or {}
        content = message.get("content")
        if record.get("type") == "user":
            if not _is_user_turn_record(record):
                continue
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tool = pending.pop(str(block.get("tool_use_id") or ""), None)
                        if tool is not None:
                            tool.result = _result_text(block.get("content"))
                            tool.is_error = bool(block.get("is_error"))
            text = _user_text(content)
            if text:
                raw = content if isinstance(content, str) else ""
                turn = Turn(user_text=text, timestamp=ts, ended_at=ts,
                            command=is_command(raw))
                pending.clear()
                session.turns.append(turn)
            elif turn is not None and ts:
                turn.ended_at = ts
            continue
        if record.get("type") != "assistant" or turn is None:
            continue
        if record.get("isApiErrorMessage") or record.get("isSidechain"):
            continue
        model = message.get("model") or ""
        if model and not model.startswith("<"):
            turn.model = session.model = model
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and (block.get("text") or "").strip():
                turn.blocks.append({"type": "text", "text": block["text"]})
            elif kind == "thinking" and (block.get("thinking") or "").strip():
                turn.blocks.append({"type": "thinking", "text": block["thinking"]})
            elif kind == "tool_use":
                tool = ToolBlock(
                    call_id=str(block.get("id") or f"tool{len(turn.blocks)}"),
                    name=str(block.get("name") or "tool"),
                    arguments=block.get("input") if block.get("input") is not None else {},
                    timestamp=ts,
                )
                pending[tool.call_id] = tool
                turn.blocks.append(tool)
        if ts:
            turn.ended_at = ts

    # A built-in command (/clear, /model …) with nothing after it is a
    # control action, not a conversation turn.
    session.turns = [t for t in session.turns if t.blocks or not t.command]
    first_user = session.turns[0].user_text if session.turns else ""
    session.title = _pick_title(titles, first_user)
    return session

