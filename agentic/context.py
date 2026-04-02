"""
Context — the execution record for one Agentic Function call.

Forms a tree: each function's Context has children (sub-calls) and a parent (caller).
Managed automatically by @agentic_function and llm_call.
"""

from __future__ import annotations

import time
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from contextvars import ContextVar

# Global: currently active Context node
_current_ctx: ContextVar[Optional["Context"]] = ContextVar("_current_ctx", default=None)


@dataclass
class Context:
    """
    Execution record for one Agentic Function call.
    
    All fields are managed automatically. Users don't need to touch this.
    """

    # === Auto-managed by @agentic_function ===
    name: str = ""                           # function name (from __name__)
    prompt: str = ""                         # docstring (from __doc__)
    params: dict = field(default_factory=dict)  # call arguments
    output: Any = None                       # return value
    error: str = ""                          # error message
    status: str = "running"                  # running / success / error
    children: list = field(default_factory=list)  # child Contexts
    parent: Optional["Context"] = field(default=None, repr=False)
    start_time: float = 0.0
    end_time: float = 0.0
    expose: str = "summary"                  # trace / detail / summary / result / silent

    # === Auto-managed by llm_call ===
    input: Optional[dict] = None             # data sent to LLM
    media: Optional[list] = None             # media file paths
    raw_reply: str = ""                      # LLM raw response

    # === Optional user override ===
    summary_fn: Optional[Callable] = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Path (auto-computed from tree structure)
    # ------------------------------------------------------------------

    @property
    def path(self) -> str:
        """Auto-computed path like 'root/navigate_0/observe_1'. No storage needed."""
        if not self.parent:
            return self.name
        # Count same-name siblings before me
        idx = 0
        for c in self.parent.children:
            if c is self:
                break
            if c.name == self.name:
                idx += 1
        return f"{self.parent.path}/{self.name}_{idx}"

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    def summarize(
        self,
        level: Optional[str] = None,
        max_tokens: Optional[int] = None,
        max_siblings: Optional[int] = None,
        include_parent: bool = True,
    ) -> str:
        """
        Generate a summary of context up to this point.
        
        Includes:
        - Parent info (who called me)
        - Previous siblings' results (filtered by their expose level)
        - Current function's prompt and params
        
        Args:
            level:        Override granularity (ignore siblings' expose settings)
            max_tokens:   Approximate token budget (truncates oldest siblings first)
            max_siblings: Only include the most recent N siblings
            include_parent: Whether to include parent info
        """
        parts = []

        # Parent info
        if include_parent and self.parent and self.parent.name:
            parts.append(f"[Caller: {self.parent.name}({_fmt_params(self.parent.params)})]")

        # Previous siblings
        if self.parent:
            siblings = []
            for c in self.parent.children:
                if c is self:
                    break
                if c.status == "running":
                    continue
                expose = level or c.expose
                if expose == "silent":
                    continue
                siblings.append(c._render(expose))

            if max_siblings is not None:
                siblings = siblings[-max_siblings:]

            if max_tokens is not None:
                # Simple truncation: drop oldest until under budget
                total = sum(len(s) for s in siblings)
                while siblings and total > max_tokens * 4:  # rough chars-to-tokens
                    removed = siblings.pop(0)
                    total -= len(removed)

            parts.extend(siblings)

        return "\n".join(parts)

    def _render(self, level: str) -> str:
        """Render this Context at the given level."""
        if self.summary_fn:
            return self.summary_fn(self)

        if level == "trace":
            lines = [f"{self.name}({_fmt_params(self.params)})"]
            if self.prompt:
                lines.append(f"  prompt: {self.prompt[:200]}")
            if self.input:
                lines.append(f"  input: {json.dumps(self.input, ensure_ascii=False, default=str)[:500]}")
            if self.media:
                lines.append(f"  media: {self.media}")
            if self.raw_reply:
                lines.append(f"  raw_reply: {self.raw_reply[:500]}")
            if self.output is not None:
                lines.append(f"  output: {json.dumps(self.output, ensure_ascii=False, default=str)[:500]}")
            if self.error:
                lines.append(f"  error: {self.error}")
            dur = f" ({self.duration_ms:.0f}ms)" if self.end_time else ""
            lines[0] += f" → {self.status}{dur}"
            return "\n".join(lines)

        elif level == "detail":
            dur = f" {self.duration_ms:.0f}ms" if self.end_time else ""
            inp = json.dumps(self.input, ensure_ascii=False, default=str)[:200] if self.input else ""
            out = json.dumps(self.output, ensure_ascii=False, default=str)[:200] if self.output is not None else ""
            return f"{self.name}({_fmt_params(self.params)}) → {self.status}{dur} | input: {inp} | output: {out}"

        elif level == "summary":
            dur = f" {self.duration_ms:.0f}ms" if self.end_time else ""
            out = json.dumps(self.output, ensure_ascii=False, default=str)[:100] if self.output is not None else ""
            err = f" error: {self.error}" if self.error else ""
            return f"{self.name}: {out}{err}{dur}"

        elif level == "result":
            return json.dumps(self.output, ensure_ascii=False, default=str) if self.output is not None else ""

        return ""

    # ------------------------------------------------------------------
    # Tree operations
    # ------------------------------------------------------------------

    @property
    def duration_ms(self) -> float:
        if self.end_time and self.start_time:
            return (self.end_time - self.start_time) * 1000
        return 0.0

    def tree(self, indent: int = 0) -> str:
        """Generate a human-readable tree view."""
        prefix = "  " * indent
        dur = f" {self.duration_ms:.0f}ms" if self.end_time else ""
        icon = "✓" if self.status == "success" else "✗" if self.status == "error" else "…"
        out = f" → {self.output}" if self.output is not None else ""
        err = f" ERROR: {self.error}" if self.error else ""
        line = f"{prefix}{self.name} {icon}{dur}{out}{err}"
        lines = [line]
        for c in self.children:
            lines.append(c.tree(indent + 1))
        return "\n".join(lines)

    def traceback(self) -> str:
        """Generate an Agentic Traceback (like Python's traceback)."""
        lines = ["Agentic Traceback:"]
        self._traceback_lines(lines, indent=1)
        return "\n".join(lines)

    def _traceback_lines(self, lines: list, indent: int):
        prefix = "  " * indent
        dur = f", {self.duration_ms:.0f}ms" if self.end_time else ""
        params_str = _fmt_params(self.params)
        lines.append(f"{prefix}{self.name}({params_str}) → {self.status}{dur}")
        if self.error:
            lines.append(f"{prefix}  error: {self.error}")
        for c in self.children:
            c._traceback_lines(lines, indent + 1)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str):
        """Save the Context tree to a file (.jsonl or .md)."""
        if path.endswith(".md"):
            with open(path, "w") as f:
                f.write(self.tree())
        else:
            with open(path, "w") as f:
                for record in self._to_records():
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def _to_records(self, depth: int = 0) -> list[dict]:
        records = [{
            "depth": depth,
            "name": self.name,
            "prompt": self.prompt,
            "params": self.params,
            "input": self.input,
            "media": self.media,
            "output": self.output,
            "raw_reply": self.raw_reply,
            "error": self.error,
            "status": self.status,
            "expose": self.expose,
            "duration_ms": self.duration_ms,
        }]
        for c in self.children:
            records.extend(c._to_records(depth + 1))
        return records


# ------------------------------------------------------------------
# Module-level functions
# ------------------------------------------------------------------

def get_context() -> Optional[Context]:
    """Get the current Context (inside an @agentic_function)."""
    return _current_ctx.get(None)


def get_root_context() -> Optional[Context]:
    """Get the root Context node."""
    ctx = _current_ctx.get(None)
    if ctx is None:
        return None
    while ctx.parent is not None:
        ctx = ctx.parent
    return ctx


def init_root(name: str = "root") -> Context:
    """Initialize a root Context. Call once at the start of a run."""
    root = Context(name=name, start_time=time.time(), status="running")
    _current_ctx.set(root)
    return root


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _fmt_params(params: dict) -> str:
    if not params:
        return ""
    parts = []
    for k, v in params.items():
        v_str = json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v, str) else f'"{v}"'
        if len(v_str) > 50:
            v_str = v_str[:47] + "..."
        parts.append(f"{k}={v_str}")
    return ", ".join(parts)
