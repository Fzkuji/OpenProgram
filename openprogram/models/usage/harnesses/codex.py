"""Codex's session files: ``<home>/sessions`` and ``<home>/archived_sessions``,
``rollout-*.jsonl`` and the zstd archives ``rollout-*.jsonl.zst``.

One call is a ``token_usage_record``, keyed by its ``response_id``. A file
written before Codex had those records counts each ``token_count`` event's
``last_token_usage`` instead, skipping a snapshot whose running total did
not move. In newer files each record comes before its ``token_count``, so
once a file has shown a record its counts are not billed. ``token_count``
also carries the plan windows (``rate_limits``) at the time it was written.
"""
from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Iterable, Iterator

from .common import cut_prompt, parse_ts, real_homes
from .store import Batch, Call

AGENT = "codex"
LABEL = "Codex"
PROVIDER = "openai"

_PREAMBLES = ("<", "# AGENTS.md")


def homes() -> list[Path]:
    configured = os.environ.get("CODEX_HOME")
    return real_homes([Path(configured).expanduser()] if configured else [Path.home() / ".codex"])


def files(home: Path) -> Iterator[tuple[str, bool]]:
    """Every rollout under ``home``; of a plain file and its zstd twin, the plain one."""
    for top in ("sessions", "archived_sessions"):
        root = home / top
        if not root.is_dir():
            continue
        for dirpath, _dirnames, names in os.walk(root, followlinks=True):
            plain = {n for n in names if n.endswith(".jsonl")}
            for name in names:
                if not name.startswith("rollout-"):
                    continue
                if name.endswith(".jsonl") or (name.endswith(".jsonl.zst") and name[:-4] not in plain):
                    yield os.path.join(dirpath, name), True


def titles(home: Path) -> dict[str, str]:
    """Session names from ``session_index.jsonl``; the newest line wins."""
    names: dict[str, str] = {}
    try:
        with open(home / "session_index.jsonl", "rb") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("id") and isinstance(d.get("thread_name"), str) and d["thread_name"].strip():
                    names[d["id"]] = d["thread_name"].strip()
    except OSError:
        pass
    return names


def resume_command(session_id: str, cwd: str | None) -> str:
    cmd = f"codex resume {shlex.quote(session_id)}"
    return f"cd {shlex.quote(cwd)} && {cmd}" if cwd else cmd


_MARKERS = (b'"session_meta"', b'"turn_context"', b'"thread_settings_applied"',
            b'"token_usage_record"', b'"token_count"')


def parse(path: str, lines: Iterable[bytes], state: dict, batch: Batch, main: bool) -> None:
    for line in lines:
        if not any(m in line for m in _MARKERS):
            if state.get("prompt") or b'"user"' not in line:
                continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        p = d.get("payload")
        if not isinstance(p, dict):
            continue
        kind = d.get("type")
        sub = p.get("type")
        if kind == "session_meta":
            if state.get("thread"):
                continue  # a fork repeats its parent's meta after its own
            state["thread"] = p.get("id") or p.get("session_id")
            state["client"] = p.get("originator")
            git = p.get("git") if isinstance(p.get("git"), dict) else {}
            batch.session(state["thread"], path=path, cwd=p.get("cwd"), client=p.get("originator"),
                          branch=git.get("branch"))
        elif kind == "turn_context":
            if p.get("model"):
                state["model"] = p["model"]
        elif kind == "event_msg" and sub == "thread_settings_applied":
            settings = p.get("thread_settings") if isinstance(p.get("thread_settings"), dict) else {}
            if settings.get("model"):
                state["model"] = settings["model"]
            if "service_tier" in settings:
                state["speed"] = "fast" if settings.get("service_tier") in ("priority", "fast") else ""
        elif kind == "token_usage_record":
            state["records"] = True
            usage = p.get("usage")
            key = p.get("response_id") or f"{p.get('thread_id')}:{p.get('turn_id')}:{d.get('ordinal')}"
            _call(batch, state, f"r:{key}", d.get("timestamp"), usage)
        elif kind == "event_msg" and sub == "token_count":
            ts = parse_ts(d.get("timestamp"))
            if isinstance(p.get("rate_limits"), dict) and ts is not None:
                batch.observe_quota(ts, p["rate_limits"])
            info = p.get("info")
            if state.get("records") or not isinstance(info, dict):
                continue
            last = info.get("last_token_usage")
            total = (info.get("total_token_usage") or {}).get("total_tokens")
            if total is not None and total == state.get("total"):
                continue
            state["total"] = total
            if isinstance(last, dict) and last.get("total_tokens"):
                _call(batch, state, f"c:{d.get('timestamp')}:{last.get('total_tokens')}", d.get("timestamp"), last)
        elif kind == "response_item" and sub == "message" and p.get("role") == "user" and not state.get("prompt"):
            text = "\n".join(
                part.get("text", "") for part in p.get("content") or []
                if isinstance(part, dict) and part.get("type") in ("input_text", "text")
            ).strip()
            if text and not text.startswith(_PREAMBLES) and state.get("thread"):
                state["prompt"] = True
                batch.session(state["thread"], first_prompt=cut_prompt(text))


def _call(batch: Batch, state: dict, key: str, timestamp, usage) -> None:
    ts = parse_ts(timestamp)
    if ts is None or not isinstance(usage, dict) or not state.get("model"):
        return
    cached = int(usage.get("cached_input_tokens") or 0)
    written = int(usage.get("cache_write_input_tokens") or 0)
    total_input = int(usage.get("input_tokens") or 0)
    batch.calls.append(Call(
        key=key, session=state.get("thread"), ts=ts, model=state["model"],
        speed=state.get("speed") or "", client=state.get("client"),
        input=max(0, total_input - cached - written),
        cache_read=cached, cache_write=written,
        output=int(usage.get("output_tokens") or 0),
    ))
