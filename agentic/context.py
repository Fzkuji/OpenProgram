"""
Context — the execution record for one Agentic Function call.

Architecture:
    All function calls form a single tree. Each node is a Context.
    The tree is a COMPLETE RECORD of everything that happened — it never changes.
    
    Two separate concerns:
    - Recording: automatic, unconditional, every function call gets a node.
    - Reading: summarize() queries the tree selectively for LLM context injection.

Tree structure example:
    root
    ├── navigate("login")
    │   ├── observe("find login")       → root/navigate_0/observe_0
    │   │   ├── run_ocr(img)            → root/navigate_0/observe_0/run_ocr_0
    │   │   └── detect_all(img)         → root/navigate_0/observe_0/detect_all_0
    │   ├── observe("find password")    → root/navigate_0/observe_1
    │   └── act("login", [347, 291])    → root/navigate_0/act_0
    └── navigate("settings")
        └── ...

See also:
    - function.py: @agentic_function decorator
    - runtime.py: runtime.exec() — LLM call with auto recording
    - docs/context/ENGINEERING.md: API reference
    - docs/context/PRACTICE.md: usage strategies
"""

from __future__ import annotations

import time
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from contextvars import ContextVar

# Tracks the currently active Context node.
# @agentic_function sets this on entry and resets on exit.
_current_ctx: ContextVar[Optional["Context"]] = ContextVar("_current_ctx", default=None)


@dataclass
class Context:
    """
    Execution record for one Agentic Function call.
    
    All fields are managed automatically by @agentic_function and runtime.exec().
    Users write normal Python — they don't need to know this class exists.
    """

    # === Auto-managed by @agentic_function ===
    name: str = ""
    prompt: str = ""
    params: dict = field(default_factory=dict)
    output: Any = None
    error: str = ""
    status: str = "running"
    children: list = field(default_factory=list)
    parent: Optional["Context"] = field(default=None, repr=False)
    start_time: float = 0.0
    end_time: float = 0.0

    # Render setting: how this node is displayed when others read it via summarize().
    # trace / detail / summary (default) / result / silent
    # This is a DEFAULT — callers can override it with summarize(level=...).
    render: str = "summary"

    # === Auto-managed by runtime.exec() ===
    input: Optional[dict] = None
    media: Optional[list] = None
    raw_reply: str = ""

    # === Internal: summarize kwargs from decorator ===
    _summarize_kwargs: Optional[dict] = field(default=None, repr=False)

    # === Optional user override ===
    summary_fn: Optional[Callable] = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Path
    # ------------------------------------------------------------------

    @property
    def path(self) -> str:
        """Auto-computed path like 'root/navigate_0/observe_1'."""
        if not self.parent:
            return self.name
        idx = 0
        for c in self.parent.children:
            if c is self:
                break
            if c.name == self.name:
                idx += 1
        return f"{self.parent.path}/{self.name}_{idx}"

    # ------------------------------------------------------------------
    # summarize() — read from the Context tree for LLM input
    # ------------------------------------------------------------------

    def summarize(
        self,
        level: Optional[str] = None,
        max_tokens: Optional[int] = None,
        max_siblings: Optional[int] = None,
        depth: int = -1,
        siblings: int = -1,
        include: Optional[list] = None,
        exclude: Optional[list] = None,
        branch: Optional[list] = None,
    ) -> str:
        """
        Query the Context tree and generate a text summary for LLM input.
        
        Args:
            level:        Override all nodes' render levels
            max_tokens:   Token budget (drops oldest first)
            max_siblings: Legacy alias for siblings=N
            depth:        Ancestor visibility. -1=all, 0=none, 1=parent only
            siblings:     Sibling visibility. -1=all, 0=none, N=last N
            include:      Path whitelist (supports * wildcard)
            exclude:      Path blacklist
            branch:       Show subtree under these node names
        """
        effective_siblings = max_siblings if max_siblings is not None else (
            None if siblings == -1 else siblings
        )

        parts = []

        # Ancestor chain
        if depth != 0 and self.parent and self.parent.name:
            ancestors = []
            node = self.parent
            while node and node.name:
                ancestors.append(node)
                node = node.parent
                if depth > 0 and len(ancestors) >= depth:
                    break
            for a in reversed(ancestors):
                if not self._path_allowed(a, include, exclude):
                    continue
                parts.append(f"[Ancestor: {a.name}({_fmt_params(a.params)})]")

        # Previous siblings
        if self.parent and (effective_siblings is None or effective_siblings > 0):
            sibling_parts = []
            for c in self.parent.children:
                if c is self:
                    break
                if c.status == "running":
                    continue
                if not self._path_allowed(c, include, exclude):
                    continue
                render_level = level or c.render
                if render_level == "silent":
                    continue
                rendered = c._render(render_level)
                if branch and c.name in branch:
                    rendered += "\n" + c._render_branch(level)
                sibling_parts.append(rendered)

            if effective_siblings is not None:
                sibling_parts = sibling_parts[-effective_siblings:]

            if max_tokens is not None:
                total = sum(len(s) for s in sibling_parts)
                while sibling_parts and total > max_tokens * 4:
                    removed = sibling_parts.pop(0)
                    total -= len(removed)

            parts.extend(sibling_parts)

        if branch and include:
            root = self
            while root.parent:
                root = root.parent
            for path_pattern in include:
                for node in self._find_by_path(root, path_pattern):
                    if node is not self and node not in (self.parent.children if self.parent else []):
                        parts.append(node._render(level or node.render))

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _path_allowed(self, node: "Context", include: Optional[list], exclude: Optional[list]) -> bool:
        if include is not None:
            return any(_path_matches(node.path, pattern) for pattern in include)
        if exclude is not None:
            return not any(_path_matches(node.path, pattern) for pattern in exclude)
        return True

    def _render_branch(self, level: Optional[str], indent: int = 1) -> str:
        lines = []
        for c in self.children:
            render_level = level or c.render
            if render_level != "silent":
                prefix = "  " * indent
                lines.append(f"{prefix}{c._render(render_level)}")
                if c.children:
                    lines.append(c._render_branch(level, indent + 1))
        return "\n".join(lines)

    @staticmethod
    def _find_by_path(root: "Context", pattern: str) -> list:
        results = []
        stack = [root]
        while stack:
            node = stack.pop()
            if _path_matches(node.path, pattern):
                results.append(node)
            stack.extend(node.children)
        return results

    def _render(self, level: str) -> str:
        """Render this node at the given level."""
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
            "path": self.path,
            "name": self.name,
            "prompt": self.prompt,
            "params": self.params,
            "input": self.input,
            "media": self.media,
            "output": self.output,
            "raw_reply": self.raw_reply,
            "error": self.error,
            "status": self.status,
            "render": self.render,
            "duration_ms": self.duration_ms,
        }]
        for c in self.children:
            records.extend(c._to_records(depth + 1))
        return records


# ======================================================================
# Module-level functions
# ======================================================================

def get_context() -> Optional[Context]:
    """Get the current Context node."""
    return _current_ctx.get(None)


def get_root_context() -> Optional[Context]:
    """Get the root of the Context tree."""
    ctx = _current_ctx.get(None)
    if ctx is None:
        return None
    while ctx.parent is not None:
        ctx = ctx.parent
    return ctx


def init_root(name: str = "root") -> Context:
    """Manually initialize a root Context."""
    root = Context(name=name, start_time=time.time(), status="running")
    _current_ctx.set(root)
    return root


# ======================================================================
# Internal helpers
# ======================================================================

def _path_matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/*"):
        prefix = pattern[:-2]
        return path.startswith(prefix + "/") or path == prefix
    if "*" in pattern:
        import fnmatch
        return fnmatch.fnmatch(path, pattern)
    return path == pattern


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
