"""CliEvent — normalized events emitted by a CLI-backed run.

CLIs yield heterogeneous stream messages (claude-stream-json's
``system`` / ``assistant`` / ``result`` / ``compact_boundary``, Codex's
Responses-API events, Gemini CLI's text blocks, etc.). The runner parses
each provider's dialect and produces the **same** event shapes here, so
consumers above the runner don't branch on provider.

Shape: sealed discriminated union via ``type`` discriminator. Each event
is a frozen dataclass. Check ``isinstance`` in consumers, not ``.type``
strings — the string is for logging only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Union


# --- content events ------------------------------------------------


@dataclass(frozen=True)
class TextDelta:
    """Incremental chunk of assistant text. Consumers concatenate."""

    type: Literal["text_delta"] = "text_delta"
    text: str = ""
    elapsed_ms: int = 0


@dataclass(frozen=True)
class ThinkingDelta:
    """Incremental chunk of the model's internal reasoning (when exposed)."""

    type: Literal["thinking_delta"] = "thinking_delta"
    text: str = ""
    elapsed_ms: int = 0


@dataclass(frozen=True)
class ToolCall:
    """Tool the CLI decided to call. Carries name + arguments.

    Execution is out-of-band — the CLI runs its own tool and its result
    arrives later as ``ToolResult``. We emit both so consumers can log
    the full call/response loop without re-parsing.
    """

    type: Literal["tool_call"] = "tool_call"
    call_id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)
    elapsed_ms: int = 0


@dataclass(frozen=True)
class ToolResult:
    """Result of a tool call, produced by the CLI after it ran the tool."""

    type: Literal["tool_result"] = "tool_result"
    call_id: str = ""
    output: str = ""
    is_error: bool = False
    elapsed_ms: int = 0


# --- control / metadata events ------------------------------------


@dataclass(frozen=True)
class SessionInfo:
    """Session identity / model info from the first stream message.

    Captured for resume: on the next run the runner passes the same
    ``session_id`` back through ``resume_args`` (per ``CliBackendConfig``).
    ``model_id`` is the CLI's **actual** model id after any internal
    routing (e.g. Claude Code's haiku helper vs primary model).
    """

    type: Literal["session"] = "session"
    session_id: Optional[str] = None
    model_id: Optional[str] = None


@dataclass(frozen=True)
class Usage:
    """Final token usage for one turn.

    All counts already normalized: ``input_tokens`` is *total* input
    (raw + cache_read + cache_create), not "non-cached only" like the
    Anthropic raw API reports. Consumers can trust the shape across all
    CLI backends.
    """

    type: Literal["usage"] = "usage"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_create: int = 0
    context_window: Optional[int] = None
    # Per-turn input size vs cumulative — runner decides which makes
    # sense for the backend and fills in. Claude Code's top-level
    # ``usage`` is per-turn; ``modelUsage`` is cumulative.
    turn_input_tokens: Optional[int] = None


@dataclass(frozen=True)
class CompactBoundary:
    """A ``/compact`` happened inside the CLI's own session.

    ``post_tokens`` is the authoritative post-compact context size —
    override any cumulative counters with this when it arrives.
    """

    type: Literal["compact_boundary"] = "compact_boundary"
    post_tokens: Optional[int] = None


@dataclass(frozen=True)
class Done:
    """Turn finished cleanly. Runner yields this once, then stops iterating."""

    type: Literal["done"] = "done"
    duration_ms: int = 0
    num_turns: int = 0


@dataclass(frozen=True)
class Error:
    """Something went wrong. Runner yields this and then stops.

    ``recoverable=True`` means the caller can retry (watchdog stall,
    transient network). ``recoverable=False`` means structural (bad
    config, CLI missing, auth failed).
    """

    type: Literal["error"] = "error"
    message: str = ""
    recoverable: bool = False
    # Raw exception class name / CLI exit code / stream parse error kind.
    # Free-form; useful for logs and tests.
    kind: Optional[str] = None


# --- the sealed union ---------------------------------------------


CliEvent = Union[
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolResult,
    SessionInfo,
    Usage,
    CompactBoundary,
    Done,
    Error,
]
"""Every event yielded by ``CliRunner.run``. Narrow with ``isinstance``."""


__all__ = [
    "CliEvent",
    "TextDelta",
    "ThinkingDelta",
    "ToolCall",
    "ToolResult",
    "SessionInfo",
    "Usage",
    "CompactBoundary",
    "Done",
    "Error",
]
