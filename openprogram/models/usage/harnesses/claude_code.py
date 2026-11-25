"""Claude Code's session files: ``<home>/projects/<folder>/<session>.jsonl``
and the subagent files under ``<session>/subagents/``.

One call is an ``assistant`` line with ``message.usage``, keyed
``message.id`` + ``requestId``. Claude Code writes one line per content
block and repeats the usage on each; the store keeps one row per key.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Iterable, Iterator

from .common import cut_prompt, parse_ts, real_homes
from .store import Batch, Call

AGENT = "claude_code"
LABEL = "Claude Code"
PROVIDER = "anthropic"

_COMMAND = re.compile(r"<command-name>\s*/?([^<]*?)\s*</command-name>")
_COMMAND_ARGS = re.compile(r"<command-args>([\s\S]*?)</command-args>")


def homes() -> list[Path]:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return real_homes([Path(configured).expanduser()] if configured else [Path.home() / ".claude"])


def files(home: Path) -> Iterator[tuple[str, bool]]:
    """Every session file under ``home``, with whether it is a main file."""
    projects = home / "projects"
    if not projects.is_dir():
        return
    for folder in _dirs(projects):
        for entry in _scandir(folder):
            if entry.is_file() and entry.name.endswith(".jsonl"):
                yield entry.path, True
            elif entry.is_dir():
                sub = os.path.join(entry.path, "subagents")
                if os.path.isdir(sub):
                    for root, _dirnames, names in os.walk(sub):
                        for name in names:
                            if name.endswith(".jsonl") and name != "journal.jsonl":
                                yield os.path.join(root, name), False


def _dirs(path: Path) -> Iterable[str]:
    return [e.path for e in _scandir(str(path)) if e.is_dir()]


def _scandir(path: str) -> list[os.DirEntry]:
    try:
        with os.scandir(path) as it:
            return list(it)
    except OSError:
        return []


def resume_command(session_id: str, cwd: str | None) -> str:
    cmd = f"claude --resume {shlex.quote(session_id)}"
    return f"cd {shlex.quote(cwd)} && {cmd}" if cwd else cmd


def _prompt_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, list):
        content = "\n".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    if not isinstance(content, str):
        return ""
    text = content.strip()
    name = _COMMAND.search(text)
    if name:
        args = _COMMAND_ARGS.search(text)
        return ("/" + name.group(1).strip() + (" " + args.group(1).strip() if args and args.group(1).strip() else "")).strip()
    if text.startswith("<") or text.startswith("[Request interrupted") or text.startswith("Caveat:"):
        return ""
    return text


def parse(path: str, lines: Iterable[bytes], state: dict, batch: Batch, main: bool) -> None:
    """Add one file's new lines to ``batch``; ``state`` carries what the
    file's earlier lines established."""
    for line in lines:
        usage = b'"usage"' in line and b'"assistant"' in line
        title = b'"custom-title"' in line
        prompt = not state.get("prompt") and b'"user"' in line
        place = not state.get("place") and b'"cwd"' in line
        if not (usage or title or prompt or place):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        sid = d.get("sessionId") or state.get("session")
        if not sid:
            continue
        state["session"] = sid
        if main:
            batch.session(sid, path=path)
        if d.get("cwd"):
            state["place"] = True
            batch.session(sid, cwd=d.get("cwd"), branch=d.get("gitBranch"), client=d.get("entrypoint"))
        kind = d.get("type")
        if kind == "custom-title" and isinstance(d.get("customTitle"), str):
            batch.session(sid, name=d["customTitle"].strip())
        elif kind == "user" and prompt:
            if d.get("isSidechain") or d.get("isMeta") or d.get("toolUseResult") is not None:
                continue
            text = _prompt_text(d.get("message") or {})
            if text:
                state["prompt"] = True
                batch.session(sid, first_prompt=cut_prompt(text))
        elif kind == "assistant" and usage:
            _call(d, sid, batch)


def _call(d: dict, sid: str, batch: Batch) -> None:
    message = d.get("message") or {}
    u = message.get("usage")
    model = message.get("model") or ""
    if not isinstance(u, dict) or not model or model == "<synthetic>":
        return
    ts = parse_ts(d.get("timestamp"))
    if ts is None:
        return
    mid, rid = message.get("id"), d.get("requestId")
    key = f"{mid or ''}:{rid or ''}" if (mid or rid) else f"{sid}:{d.get('uuid') or ''}"
    created = int(u.get("cache_creation_input_tokens") or 0)
    split = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    one_hour = int(split.get("ephemeral_1h_input_tokens") or 0)
    five_min = int(split.get("ephemeral_5m_input_tokens") or 0)
    batch.calls.append(Call(
        key=key, session=sid, ts=ts, model=model,
        speed="fast" if u.get("speed") == "fast" else "",
        client=d.get("entrypoint"),
        input=int(u.get("input_tokens") or 0),
        cache_read=int(u.get("cache_read_input_tokens") or 0),
        cache_write=max(created, one_hour + five_min),
        cache_write_1h=one_hour,
        output=int(u.get("output_tokens") or 0),
    ))
