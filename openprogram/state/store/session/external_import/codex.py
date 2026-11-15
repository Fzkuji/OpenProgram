"""Read Codex rollouts (``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``).

A rollout is append-only: ``session_meta`` first, then ``response_item``
records (messages, reasoning, tool calls and their outputs) interleaved
with ``event_msg`` / ``turn_context`` bookkeeping. Compaction appends a
``compacted`` record but leaves the earlier items in place, and a fork
writes a new file, so the file in order is the conversation.

User messages also carry Codex's own context (environment, AGENTS.md,
app context) as separate content blocks; those are dropped. Thread
titles are not in the rollout — Codex keeps them in
``session_index.jsonl`` next to the sessions directory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator, Optional

from .model import CODEX, Candidate, ExternalSession, ToolBlock, Turn
from .sanitize import clean_block_text, image_label, is_command
from .timeparse import fallback_title, last_message_time, parse_timestamp

_TOOL_CALLS = {"function_call", "custom_tool_call", "local_shell_call", "web_search_call"}
_TOOL_OUTPUTS = {"function_call_output", "custom_tool_call_output", "local_shell_call_output"}


def default_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def discover(home: Optional[Path] = None) -> list[Path]:
    root = (home or default_home()) / "sessions"
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("**/rollout-*.jsonl") if p.is_file())


def thread_titles(home: Optional[Path] = None) -> dict[str, str]:
    """Thread id → the name Codex shows for it (the latest rename wins)."""
    titles: dict[str, str] = {}
    index = (home or default_home()) / "session_index.jsonl"
    try:
        with open(index, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("id") and str(row.get("thread_name") or "").strip():
                    titles[row["id"]] = row["thread_name"].strip()
    except OSError:
        pass
    return titles


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


def _is_subagent(meta: dict[str, Any]) -> bool:
    source = meta.get("source")
    return isinstance(source, dict) and "subagent" in source


def _user_text(content: Any) -> tuple[str, bool]:
    """(the user's own text, whether it was a slash command)."""
    if isinstance(content, str):
        content = [{"type": "input_text", "text": content}]
    parts: list[str] = []
    command = False
    in_image = False
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        text = block.get("text") or ""
        if kind == "input_image":
            if not in_image:
                parts.append("[image]")
            continue
        if kind != "input_text":
            continue
        label = image_label(text)
        if label:
            parts.append(label)
            in_image = True
            continue
        if text.strip() == "</image>":
            in_image = False
            continue
        cleaned = clean_block_text(text)
        if cleaned:
            parts.append(cleaned)
            command = command or is_command(text)
    return "\n\n".join(parts).strip(), command


def _output_text(output: Any) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return "\n".join(b.get("text") or "" for b in output
                         if isinstance(b, dict) and b.get("type") in ("input_text", "output_text", "text"))
    if isinstance(output, dict):
        inner = output.get("content") if "content" in output else output.get("output")
        if inner is not None:
            return _output_text(inner)
    return "" if output is None else json.dumps(output, ensure_ascii=False)


def _arguments(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"input": raw}
        return parsed if isinstance(parsed, dict) else {"input": raw}
    return raw if isinstance(raw, dict) else {}


def _tool_block(payload: dict[str, Any], ts: Optional[float], fallback_id: str) -> ToolBlock:
    kind = payload.get("type")
    call_id = str(payload.get("call_id") or payload.get("id") or fallback_id)
    if kind == "local_shell_call":
        action = payload.get("action") or {}
        return ToolBlock(call_id, "shell", {"command": action.get("command")}, timestamp=ts)
    if kind == "web_search_call":
        action = payload.get("action") or {}
        return ToolBlock(call_id, "web_search", {k: v for k, v in action.items() if k != "type"},
                         result="", timestamp=ts)
    raw = payload.get("arguments") if kind == "function_call" else payload.get("input")
    return ToolBlock(call_id, str(payload.get("name") or "tool"), _arguments(raw), timestamp=ts)


def _reasoning_text(payload: dict[str, Any]) -> str:
    parts = [s.get("text") or "" for s in payload.get("summary") or [] if isinstance(s, dict)]
    if not any(p.strip() for p in parts):
        parts = [c.get("text") or "" for c in payload.get("content") or [] if isinstance(c, dict)]
    return "\n\n".join(p.strip() for p in parts if p.strip())


def summarize(path: Path, titles: Optional[dict[str, str]] = None) -> Optional[Candidate]:
    """Id, cwd and title from the head of the rollout, recency from the last item."""
    meta: dict[str, Any] = {}
    first_user = ""
    try:
        for record in _records(path):
            payload = record.get("payload") or {}
            if record.get("type") == "session_meta":
                meta = payload
                if _is_subagent(meta):
                    return None
            elif (record.get("type") == "response_item" and payload.get("type") == "message"
                  and payload.get("role") == "user"):
                first_user = _user_text(payload.get("content"))[0]
                if first_user:
                    break
        stat = path.stat()
    except OSError:
        return None
    if not first_user:
        return None
    source_id = str(meta.get("id") or path.stem)
    title = (titles or {}).get(source_id) or fallback_title(first_user)
    return Candidate(
        agent=CODEX, source_id=source_id, path=path, cwd=meta.get("cwd"),
        title=title, updated_at=last_message_time(path, ("response_item",)) or stat.st_mtime,
        size=stat.st_size,
    )


def parse(path: Path, titles: Optional[dict[str, str]] = None) -> ExternalSession:
    session = ExternalSession(agent=CODEX, source_id=path.stem, path=path)
    turn: Optional[Turn] = None
    pending: dict[str, ToolBlock] = {}
    model = ""
    for record in _records(path):
        kind = record.get("type")
        payload = record.get("payload") or {}
        ts = parse_timestamp(record.get("timestamp"))
        if kind == "session_meta":
            session.source_id = str(payload.get("id") or session.source_id)
            session.cwd = payload.get("cwd") or session.cwd
            continue
        if kind == "turn_context":
            model = payload.get("model") or model
            continue
        if kind != "response_item":
            continue
        item = payload.get("type")
        if item == "message" and payload.get("role") == "user":
            text, command = _user_text(payload.get("content"))
            if text:
                turn = Turn(user_text=text, timestamp=ts, ended_at=ts, command=command)
                pending.clear()
                session.turns.append(turn)
            continue
        if turn is None:
            continue
        if ts:
            turn.ended_at = ts
        if model:
            turn.model = session.model = model
        if item == "message" and payload.get("role") == "assistant":
            text = _output_text(payload.get("content"))
            if text.strip():
                turn.blocks.append({"type": "text", "text": text})
        elif item == "reasoning":
            text = _reasoning_text(payload)
            if text:
                turn.blocks.append({"type": "thinking", "text": text})
        elif item in _TOOL_CALLS:
            tool = _tool_block(payload, ts, f"call{len(turn.blocks)}")
            pending[tool.call_id] = tool
            turn.blocks.append(tool)
        elif item in _TOOL_OUTPUTS:
            tool = pending.pop(str(payload.get("call_id") or ""), None)
            if tool is not None:
                tool.result = _output_text(payload.get("output"))
    session.turns = [t for t in session.turns if t.blocks or not t.command]
    first_user = session.turns[0].user_text if session.turns else ""
    session.title = (titles or {}).get(session.source_id) or fallback_title(first_user)
    return session
