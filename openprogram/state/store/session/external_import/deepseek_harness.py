"""Read DeepSeek Harness conversations (``~/.dsh``).

The DeepSeek Harness app lists its conversations by workspace in
``storages/workspace.json`` (the archived ones apart); each conversation's
log is ``sessions/<workspace slug>/<id>/session.v4.jsonl.zstd``, a
zstd-compressed event log (``@deepseek-ai/dsh-session``): a header line,
then events ``{type, seq, time, data}``. A turn opens with
``turn/start``; the person's message is the ``user/message`` whose
``source.kind`` is ``user`` (others are context the harness injected);
``assistant/message`` holds a step's reasoning, text and tool-call blocks,
and ``tool/result`` the result of a call. ``session/title`` names the
conversation and ``model/selection`` its model. A turn's ``turn_id`` is the
id of its last assistant message, which the ACP session's updates carry
too: a turn OpenProgram ran in the session is known by it.
"""
from __future__ import annotations

import io
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from .model import DEEPSEEK_HARNESS, ExternalSession, ToolBlock, Turn

_log = logging.getLogger(__name__)

LOG_NAME = "session.v4.jsonl.zstd"


def default_home() -> Path:
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh")


@dataclass
class Conversation:
    id: str
    path: Path
    cwd: Optional[str]
    updated_at: float


def conversations(home: Optional[Path] = None) -> list[Conversation]:
    """The app's conversations that are not archived, newest first."""
    home = Path(home or default_home())
    try:
        workspace = json.loads((home / "storages" / "workspace.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    archived = set((workspace.get("global") or {}).get("archivedSessionIds") or [])
    wanted: dict[str, Optional[str]] = {}
    for space in ((workspace.get("tables") or {}).get("workspaces") or {}).values():
        for session_id in (space or {}).get("sessionIds") or []:
            if session_id not in archived:
                wanted[session_id] = (space or {}).get("path")
    found = []
    for path in (home / "sessions").glob(f"*/*/{LOG_NAME}"):
        session_id = path.parent.name
        if session_id in wanted:
            found.append(Conversation(session_id, path, wanted[session_id], path.stat().st_mtime))
    found.sort(key=lambda c: c.updated_at, reverse=True)
    return found


def _records(path: Path) -> Iterator[dict[str, Any]]:
    import zstandard
    with open(path, "rb") as fh:
        reader = zstandard.ZstdDecompressor().stream_reader(fh, read_across_frames=True)
        for line in io.TextIOWrapper(reader, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def _text(content: Any, kind: str = "text") -> str:
    parts = [b.get("text") or "" for b in content or [] if isinstance(b, dict) and b.get("type") == kind]
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _arguments(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def parse(conversation: Conversation) -> ExternalSession:
    session = ExternalSession(agent=DEEPSEEK_HARNESS, source_id=conversation.id,
                              path=conversation.path, cwd=conversation.cwd)
    tools: dict[str, ToolBlock] = {}
    model = provider = ""
    turn: Optional[Turn] = None
    for record in _records(conversation.path):
        kind = record.get("type")
        data = record.get("data") or {}
        ts = (record.get("time") or 0) / 1000 or None
        if kind == "session":
            session.cwd = session.cwd or record.get("cwd")
        elif kind == "model/selection" and isinstance(data, dict):
            model = str(data.get("model") or model)
            provider = str(data.get("provider") or provider)
        elif kind == "session/title" and isinstance(data, dict) and data.get("title"):
            session.title = str(data["title"])
        elif kind == "user/message" and isinstance(data, dict):
            if (data.get("source") or {}).get("kind") != "user":
                continue  # context the harness added, not the person
            text = _text(data.get("content"))
            if not text:
                continue
            turn = Turn(user_text=text, timestamp=ts, ended_at=ts, model=model)
            session.turns.append(turn)
        elif turn is None:
            continue
        elif kind == "assistant/message" and isinstance(data, dict):
            message = data.get("message") or {}
            source = message.get("source") or {}
            turn.model = str(source.get("model") or turn.model or model)
            if message.get("id"):
                turn.turn_id = str(message["id"])
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "reasoning" and block.get("text"):
                    turn.blocks.append({"type": "thinking", "text": block["text"]})
                elif block.get("type") == "text" and block.get("text"):
                    turn.blocks.append({"type": "text", "text": block["text"]})
                elif block.get("type") == "tool-call":
                    tool = ToolBlock(call_id=str(block.get("id") or f"call_{len(tools)}"),
                                     name=str(block.get("name") or "tool"),
                                     arguments=_arguments(block.get("arguments")), timestamp=ts)
                    tools[tool.call_id] = tool
                    turn.blocks.append(tool)
            turn.ended_at = ts or turn.ended_at
        elif kind == "tool/result" and isinstance(data, dict):
            for block in (data.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool-result":
                    tool = tools.get(str(block.get("toolCallId")))
                    if tool is not None:
                        tool.result = _text(block.get("content"))
                        tool.is_error = bool(block.get("isError"))
            turn.ended_at = ts or turn.ended_at
    session.model, session.model_provider = model, provider
    if not session.title and session.turns:
        first = session.turns[0].user_text.strip().splitlines()[0]
        session.title = first[:80]
    return session
