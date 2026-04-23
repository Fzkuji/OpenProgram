"""Stream parsers for each CLI output format / dialect.

One parser per ``CliBackendConfig.output`` value, and one dialect-
specific parser per ``jsonl_dialect``. Parsers are **pure line → events**
functions — they own zero state outside what the caller gives them.

When you add a new CLI, write / extend a parser here and wire it through
``parser_for(config)``. Keep the runner ignorant of dialects.
"""

from __future__ import annotations

import json
import time
from typing import Callable, Optional

from .config import CliBackendConfig
from .events import (
    CliEvent,
    CompactBoundary,
    SessionInfo,
    TextDelta,
    ToolCall,
    ToolResult,
    Usage,
)


LineParser = Callable[[str, float], list[CliEvent]]
"""Signature: (raw_line, call_start_monotonic) -> events.

``call_start_monotonic`` is ``time.monotonic()`` when the call began, so
the parser can stamp ``elapsed_ms`` on each event. Parsers **never**
read the clock themselves — the runner pins a single reference so all
events in a run share a consistent timeline.
"""


def _elapsed(call_start: float) -> int:
    return int((time.monotonic() - call_start) * 1000)


def parse_text_line(line: str, call_start: float) -> list[CliEvent]:
    """``output="text"`` — each non-empty line is a text delta.

    No structure; the CLI prints plain text. Used by CLIs that don't
    emit JSON. The runner concatenates deltas for consumers that need
    the whole response.
    """
    stripped = line.rstrip("\n")
    if not stripped:
        return []
    return [TextDelta(text=stripped + "\n", elapsed_ms=_elapsed(call_start))]


def parse_claude_stream_json(line: str, call_start: float) -> list[CliEvent]:
    """``output="jsonl"`` + ``jsonl_dialect="claude-stream-json"``.

    Each line is a JSON object; messages we care about:

    - ``system`` — emitted first; carries ``model`` + ``session_id``
    - ``assistant`` — ``message.content[*]`` blocks:
        - ``text`` → TextDelta
        - ``tool_use`` → ToolCall
    - ``user`` — when ``content[*].type == "tool_result"`` → ToolResult
    - ``result`` — final event; ``result`` text + ``usage`` + ``modelUsage``
    - ``compact_boundary`` — ``/compact`` ran; ``compact_metadata.post_tokens``
      is the authoritative post-compact context size

    Parser returns an empty list for unknown/irrelevant messages (e.g.
    the CLI's own status messages), so the runner can keep reading
    without branching.
    """
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return []
    if not isinstance(msg, dict):
        return []

    msg_type = msg.get("type") or ""
    elapsed = _elapsed(call_start)
    out: list[CliEvent] = []

    if msg_type == "system":
        out.append(SessionInfo(
            session_id=msg.get("session_id"),
            model_id=msg.get("model"),
        ))
        return out

    if msg_type == "assistant":
        inner = msg.get("message") or {}
        if isinstance(inner, dict):
            for block in inner.get("content") or []:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text") or ""
                    if text:
                        out.append(TextDelta(text=text, elapsed_ms=elapsed))
                elif btype == "tool_use":
                    out.append(ToolCall(
                        call_id=block.get("id") or "",
                        name=block.get("name") or "",
                        input=dict(block.get("input") or {}),
                        elapsed_ms=elapsed,
                    ))
        return out

    if msg_type == "user":
        # Tool results appear as user messages with content blocks of
        # type "tool_result". Anything else on the user channel is
        # echo-back we should ignore.
        inner = msg.get("message") or {}
        if isinstance(inner, dict):
            for block in inner.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    raw_content = block.get("content")
                    if isinstance(raw_content, list):
                        # Content can be a list of text blocks; flatten.
                        pieces: list[str] = []
                        for c in raw_content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                pieces.append(c.get("text") or "")
                            elif isinstance(c, str):
                                pieces.append(c)
                        output = "".join(pieces)
                    elif isinstance(raw_content, str):
                        output = raw_content
                    else:
                        output = ""
                    out.append(ToolResult(
                        call_id=block.get("tool_use_id") or "",
                        output=output,
                        is_error=bool(block.get("is_error", False)),
                        elapsed_ms=elapsed,
                    ))
        return out

    if msg_type == "compact_boundary":
        meta = msg.get("compact_metadata") or {}
        out.append(CompactBoundary(post_tokens=meta.get("post_tokens")))
        return out

    if msg_type == "result":
        # Usage normalization matches the rules enshrined in
        # legacy_providers/claude_code.py: input_tokens reported by
        # Anthropic is non-cached only, so we add cache reads and
        # cache creates back to match OpenAI-style "total input".
        usage = msg.get("usage") or {}
        raw_in = usage.get("input_tokens", 0) or 0
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        cache_create = usage.get("cache_creation_input_tokens", 0) or 0

        # Pick the real model's entry from ``modelUsage``, not the
        # Haiku helper's. No preferred-model hint here (parser is
        # stateless), so prefer any non-haiku entry, else first.
        context_window = None
        mu_all = msg.get("modelUsage") or {}
        if isinstance(mu_all, dict) and mu_all:
            non_haiku = [k for k in mu_all if "haiku" not in k.lower()]
            picked = non_haiku[0] if non_haiku else next(iter(mu_all))
            mu = mu_all.get(picked) or {}
            context_window = mu.get("contextWindow")

        out.append(Usage(
            input_tokens=raw_in + cache_read + cache_create,
            output_tokens=usage.get("output_tokens", 0) or 0,
            cache_read=cache_read,
            cache_create=cache_create,
            context_window=context_window,
            turn_input_tokens=raw_in + cache_read + cache_create,
        ))
        # Don't emit Done here — the runner emits it after the process
        # exits cleanly, so ``duration_ms`` / ``num_turns`` from the
        # result payload can be stitched with real exit timing.
        return out

    return out


def parse_whole_json(blob: str, call_start: float) -> list[CliEvent]:
    """``output="json"`` — the CLI prints one JSON object on exit.

    Phase 1b placeholder: we treat it as a claude-stream-json object
    with a trailing newline. Real CLIs (Gemini CLI's JSON mode) get a
    dedicated parser when they land in Phase 2.
    """
    return parse_claude_stream_json(blob, call_start)


def parser_for(config: CliBackendConfig) -> LineParser:
    """Pick the parser implied by ``config``.

    Caller decides whether to feed it one line at a time (jsonl) or one
    whole blob (json / text). Runner handles the outer buffering.
    """
    out_fmt = config.output
    if out_fmt == "jsonl":
        dialect = config.jsonl_dialect
        if dialect == "claude-stream-json":
            return parse_claude_stream_json
        # Default JSONL: parse each line, yield any events we can map.
        return parse_claude_stream_json
    if out_fmt == "json":
        return parse_whole_json
    if out_fmt == "text":
        return parse_text_line
    # Unknown format — treat as text so at least deltas flow.
    return parse_text_line


__all__ = [
    "LineParser",
    "parse_text_line",
    "parse_claude_stream_json",
    "parse_whole_json",
    "parser_for",
]
