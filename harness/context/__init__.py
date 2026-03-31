"""
Context — execution state management for Agentic Programming.

Modeled after Python's runtime execution model:

    Python                          Agentic Programming
    ─────                           ────────────────────
    Frame (locals, globals, code)   Frame (function, params, caller, reason)
    Call stack (linked frames)      CallStack (list of Frames)
    logging module                  ExecutionLog (structured entries)
    locals() / scope                context.scope_for(function) → filtered dict

Context replaces the raw dict used previously. It is dict-compatible
(existing code that passes dict still works), but adds:
    - Call stack tracking (who called whom, and why)
    - Scoped context (each Function only sees what it declared in params)
    - Structured execution log (like Python's logging, but for Function calls)
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("harness")


# ------------------------------------------------------------------
# Frame — one entry in the call stack
# ------------------------------------------------------------------

@dataclass
class Frame:
    """
    A single call stack frame. Like Python's frame object.

    In Python: each function call creates a frame with locals, globals,
    and a reference to the calling frame.

    Here: each Function call creates a Frame with the function name,
    the caller, the reason for the call, and the depth in the stack.
    """
    function: str
    caller: str
    reason: str
    depth: int
    timestamp: float = field(default_factory=time.time)


# ------------------------------------------------------------------
# LogEntry — one execution record
# ------------------------------------------------------------------

@dataclass
class LogEntry:
    """
    A structured log entry for one Function execution.

    Like a log line from Python's logging module, but structured
    for Agentic Programming: records what ran, who called it,
    what it returned, and whether it succeeded.
    """
    function: str
    caller: str
    reason: str
    depth: int
    status: str                    # "success" | "error" | "retry"
    duration_ms: float = 0.0
    input_keys: list = field(default_factory=list)
    output_summary: Optional[dict] = None
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def __str__(self):
        indent = "  " * self.depth
        status_icon = {"success": "✓", "error": "✗", "retry": "↻"}.get(self.status, "?")
        return (
            f"{indent}{status_icon} {self.function}() "
            f"[{self.duration_ms:.0f}ms] "
            f"called by {self.caller}: {self.reason}"
        )


# ------------------------------------------------------------------
# Context — the execution state
# ------------------------------------------------------------------

class Context:
    """
    Execution state for Agentic Programming.

    Manages three things:
        1. Data store — key-value pairs (like Python's namespace)
        2. Call stack — who called whom (like Python's sys._getframe())
        3. Execution log — what happened (like Python's logging)

    Dict-compatible: context["key"] and context.get("key") work.

    Usage:
        ctx = Context(task="click login button")

        # Push a frame (entering a function)
        ctx.push("programmer", "observe", reason="need to see the screen")

        # Get scoped context for a function (only its declared params)
        scoped = ctx.scope_for(function)

        # Store a result
        ctx["observe"] = result.model_dump()

        # Pop the frame (leaving the function)
        ctx.pop(status="success", output=result.model_dump())

        # Check the log
        for entry in ctx.log:
            print(entry)
    """

    def __init__(self, task: str = "", **kwargs):
        self._data: dict[str, Any] = {"task": task, **kwargs}
        self._stack: list[Frame] = []
        self._log: list[LogEntry] = []
        self._frame_start: dict[int, float] = {}  # depth → start time

    # --- Dict-compatible interface ---

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any):
        self._data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def update(self, other: dict):
        self._data.update(other)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def to_dict(self) -> dict:
        """Export the raw data as a plain dict."""
        return dict(self._data)

    # --- Call stack ---

    @property
    def stack(self) -> list[Frame]:
        """Current call stack (read-only view)."""
        return list(self._stack)

    @property
    def depth(self) -> int:
        """Current call depth."""
        return len(self._stack)

    @property
    def current_frame(self) -> Optional[Frame]:
        """The top frame on the stack, or None."""
        return self._stack[-1] if self._stack else None

    def push(self, caller: str, function: str, reason: str = ""):
        """
        Enter a function call. Like pushing a frame onto Python's call stack.

        Args:
            caller:    Who is calling (e.g. "programmer", "workflow")
            function:  The function being called
            reason:    Why this call is being made
        """
        depth = len(self._stack)
        frame = Frame(
            function=function,
            caller=caller,
            reason=reason,
            depth=depth,
        )
        self._stack.append(frame)
        self._frame_start[depth] = time.time()

        logger.debug(
            "%s→ %s() called by %s: %s",
            "  " * depth, function, caller, reason,
        )

    def pop(
        self,
        status: str = "success",
        output: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> LogEntry:
        """
        Leave a function call. Like popping a frame from Python's call stack.
        Records the result in the execution log.

        Args:
            status:  "success", "error", or "retry"
            output:  The function's return value (summarized)
            error:   Error message if status is "error"

        Returns:
            The LogEntry created for this execution.
        """
        if not self._stack:
            raise RuntimeError("Cannot pop from empty call stack")

        frame = self._stack.pop()
        start = self._frame_start.pop(frame.depth, time.time())
        duration_ms = (time.time() - start) * 1000

        entry = LogEntry(
            function=frame.function,
            caller=frame.caller,
            reason=frame.reason,
            depth=frame.depth,
            status=status,
            duration_ms=duration_ms,
            output_summary=output,
            error=error,
        )
        self._log.append(entry)

        logger.debug("%s", entry)
        return entry

    # --- Execution log ---

    @property
    def log(self) -> list[LogEntry]:
        """Full execution log (read-only view)."""
        return list(self._log)

    def print_log(self):
        """Print the execution log in a human-readable format."""
        for entry in self._log:
            print(entry)

    # --- Scoping ---

    def scope_for(self, params: Optional[list[str]] = None) -> dict:
        """
        Create a scoped view of the context for a Function.

        Like Python's locals() — only the declared variables are visible.

        Args:
            params:  Which keys to include (None = everything)

        Returns:
            A filtered dict containing only the requested keys,
            plus metadata (task, call_stack summary).
        """
        scoped = {"task": self._data.get("task", "")}

        # Add call stack summary (so the Function knows where it is)
        if self._stack:
            scoped["_call_stack"] = [
                f"{f.caller} → {f.function}(): {f.reason}"
                for f in self._stack
            ]

        if params is None:
            scoped.update(self._data)
        else:
            for key in params:
                if key in self._data:
                    scoped[key] = self._data[key]

        return scoped
