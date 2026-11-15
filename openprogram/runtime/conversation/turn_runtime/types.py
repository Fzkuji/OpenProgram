"""Turn-dispatch type definitions: the ``TurnResult`` dataclass, the
``EventCallback`` alias and its no-op default (``_noop``).

``TurnRequest``, ``PermissionMode`` and the parent sentinel are call-core
types and live in ``openprogram.runtime.agent.turn_request``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

EventCallback = Callable[[dict], None]


def _noop(_: dict) -> None:
    """The canonical no-op ``EventCallback``. Used as the default event
    sink across the dispatcher package (``on_event = on_event or _noop``)
    so every entry point can be called without a callback. Lives here
    next to ``EventCallback`` so titles/forced_tool/__init__ share one
    definition instead of three copies."""
    pass


def _subprocess_terminal_status(out: dict, metadata: dict | None = None) -> str:
    """Classify one subprocess result without overriding cancel intent."""
    if out.get("timed_out") or out.get("error"):
        return "error"
    if out.get("killed"):
        cancellation_requested = bool(
            (metadata or {}).get("cancellation_requested_at")
            or (metadata or {}).get("status") in {"cancelling", "cancelled"}
        )
        return "cancelled" if cancellation_requested else "interrupted"
    return "completed"


@dataclass
class TurnResult:
    final_text: str
    user_msg_id: str
    assistant_msg_id: str
    tool_calls: list[dict] = field(default_factory=list)
    # Files this turn attached (its ``send_file`` calls, then the
    # deliverables it produced): the only files a chat channel uploads
    # for it, whatever markers the reply text holds.
    attached_files: list[str] = field(default_factory=list)
    # What the last model round said; final_text joins every round.
    last_round_text: str = ""
    usage: dict = field(default_factory=dict)
    duration_ms: int = 0
    failed: bool = False
    error: Optional[str] = None
    # The user stopped this turn; ``final_text`` is what it had written by then.
    stopped: bool = False
    # Structured error taxonomy for a failed turn, so the webui can show an
    # actionable error (retryable rate-limit vs fatal auth/context) instead of a
    # string. See docs/design/providers/reliability/error-taxonomy-propagation.md.
    error_reason: Optional[str] = None
    error_retryable: Optional[bool] = None
    error_retry_after_s: Optional[float] = None
    # The failure's stable category beside its reason (``errors.error_kind``:
    # ``stream_idle``, ``stream_total``, ``memory_unavailable``, ``auth``).
    error_kind: Optional[str] = None
    # Which limit or refusal it was (``errors.failure_cause``): a spent quota
    # with its reset time, a rejected key, a region or plan not served, an
    # unreachable host. ``None`` for any other failure.
    error_cause: Optional[dict] = None
    # Per-turn ordered LLM blocks (thinking/text/tool, in emission
    # order). Mirrors what's persisted to ``extra.blocks`` so the
    # webui result-envelope path and the after-refresh DB-rebuilt
    # path render identically.
    blocks: list[dict] = field(default_factory=list)
    structured_output: Any = None
    structured_output_mode: Optional[str] = None
    structured_output_attempt: Optional[int] = None
    structured_error_code: Optional[str] = None
    structured_output_attempts: Optional[int] = None
    structured_output_issues: list[dict] = field(default_factory=list)
