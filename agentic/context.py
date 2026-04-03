"""
Context — execution record for Agentic Functions.

The Big Picture:
    Every @agentic_function call creates a Context node. Nodes form a tree
    via parent/children links. The tree is a COMPLETE, IMMUTABLE record of
    everything that happened during execution.

    Two concerns, fully separated:

    1. RECORDING — automatic, unconditional. Every function call gets a node.
       All parameters, outputs, errors, LLM I/O are captured. Nothing is
       ever deleted or modified after recording.

    2. READING — on-demand, selective. When a function needs to call an LLM,
       summarize() queries the tree and returns a text string containing
       only the relevant parts. What to include is configured per-function
       via the @agentic_function decorator's `summarize` parameter.

    This separation means:
    - Recording is never affected by how data is read later
    - Different functions can read the SAME tree differently
    - The full history is always available for debugging/saving

Tree Example:
    root
    ├── navigate("login")                   → root/navigate_0
    │   ├── observe("find login")           → root/navigate_0/observe_0
    │   │   ├── run_ocr(img)                → root/navigate_0/observe_0/run_ocr_0
    │   │   └── detect_all(img)             → root/navigate_0/observe_0/detect_all_0
    │   ├── act("click login")              → root/navigate_0/act_0
    │   └── verify("check result")          → root/navigate_0/verify_0
    └── navigate("settings")                → root/navigate_1
        └── ...

    Paths are auto-computed: {parent_path}/{name}_{index_among_same_name_siblings}

See also:
    function.py  — @agentic_function decorator (creates nodes, manages the tree)
    runtime.py   — runtime.exec() (calls the LLM, reads/writes Context nodes)
"""

from __future__ import annotations

import os
import time
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from contextvars import ContextVar


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
# Currently active Context node. @agentic_function sets on entry, resets on exit.
_current_ctx: ContextVar[Optional["Context"]] = ContextVar(
    "_current_ctx", default=None
)

# The last completed top-level Context tree. Set when a top-level
# @agentic_function finishes (parent=None). Allows get_root_context()
# to return the tree after execution completes.
_last_root: Optional["Context"] = None


# ---------------------------------------------------------------------------
# Context — one node in the execution tree
# ---------------------------------------------------------------------------

@dataclass
class Context:
    """
    One execution record = one function call.

    Users never create or modify Context objects directly.
    @agentic_function creates them automatically, and runtime.exec()
    fills in the LLM-related fields.

    Fields are grouped by who sets them:

    Set by @agentic_function (on entry):
        name, prompt, params, parent, children, render, compress,
        start_time, _summarize_kwargs

    Set by @agentic_function (on exit):
        output OR error, status, end_time

    Set by runtime.exec() (during execution):
        input, media, raw_reply
    """

    # --- Identity & input ---
    name: str = ""              # Function name (from fn.__name__)
    prompt: str = ""            # Docstring (from fn.__doc__) — doubles as LLM prompt
    params: dict = field(default_factory=dict)  # Call arguments

    # --- Execution result ---
    output: Any = None          # Return value (set on success)
    error: str = ""             # Error message (set on exception)
    status: str = "running"     # "running" → "success" or "error"

    # --- Tree structure ---
    children: list = field(default_factory=list)  # Child nodes (sub-calls)
    parent: Optional["Context"] = field(default=None, repr=False)

    # --- Timing ---
    start_time: float = 0.0
    end_time: float = 0.0

    # --- Display settings (set via @agentic_function decorator) ---

    render: str = "summary"
    # Default rendering level when others view this node via summarize().
    #
    # Five levels, from most to least verbose:
    #   "trace"   — prompt + full I/O + raw LLM reply + error
    #   "detail"  — name(params) → status duration | input | output
    #   "summary" — name: output_snippet duration  (DEFAULT)
    #   "result"  — just the return value as JSON
    #   "silent"  — not shown at all
    #
    # This is a DEFAULT hint. Callers can override it:
    #   ctx.summarize(level="detail")  ← forces all nodes to render as "detail"

    compress: bool = False
    # When True: after this function completes, summarize() renders only
    # this node's own result — its children are NOT expanded.
    #
    # Use for high-level orchestrating functions. Example:
    #   navigate(compress=True) has children observe, act, verify.
    #   After navigate finishes, others see "navigate: {success: true}"
    #   without the 10 sub-steps inside.
    #
    # The children are still fully recorded in the tree — compress only
    # affects how summarize() renders this node. tree() and save() always
    # show the complete structure.

    # --- LLM call record (set by runtime.exec()) ---
    raw_reply: str = None            # Raw LLM response text (None = not called yet)

    # --- Internal: decorator config ---
    _summarize_kwargs: Optional[dict] = field(default=None, repr=False)
    # The `summarize` dict from @agentic_function(summarize={...}).
    # runtime.exec() uses this: ctx.summarize(**ctx._summarize_kwargs)
    # If None, runtime.exec() calls ctx.summarize() with defaults (see all).

    # --- Optional: user-provided render function ---
    summary_fn: Optional[Callable] = field(default=None, repr=False)
    # If set, _render() calls this instead of the built-in formatting.
    # Signature: fn(ctx: Context) -> str

    # ==================================================================
    # PATH — auto-computed tree address
    # ==================================================================

    @property
    def path(self) -> str:
        """
        Auto-computed address in the tree.

        Format: parent_path/name_index
        Example: "root/navigate_0/observe_1/run_ocr_0"

        The index counts same-name siblings under the same parent.
        observe_0 = first observe, observe_1 = second observe, etc.
        """
        if not self.parent:
            return self.name
        idx = 0
        for c in self.parent.children:
            if c is self:
                break
            if c.name == self.name:
                idx += 1
        return f"{self.parent.path}/{self.name}_{idx}"

    def _depth(self) -> int:
        """How deep this node is in the tree. Root = 1."""
        d = 1
        node = self.parent
        while node:
            d += 1
            node = node.parent
        return d

    def _indent(self) -> str:
        """Indentation string for this node (4 spaces per level)."""
        return "    " * self._depth()

    def _call_path(self) -> str:
        """Full call path like login_flow.navigate_to.observe_screen."""
        parts = []
        node = self
        while node:
            parts.append(node.name)
            node = node.parent
        return ".".join(reversed(parts))

    @property
    def duration_ms(self) -> float:
        """Execution time in milliseconds. 0 if still running."""
        if self.end_time and self.start_time:
            return (self.end_time - self.start_time) * 1000
        return 0.0

    # ==================================================================
    # SUMMARIZE — query the tree for LLM context
    # ==================================================================

    def summarize(
        self,
        depth: int = -1,
        siblings: int = -1,
        level: Optional[str] = None,
        include: Optional[list] = None,
        exclude: Optional[list] = None,
        branch: Optional[list] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Read from the Context tree and produce a text string for LLM input.

        This is the ONLY way Context data flows into LLM calls.
        runtime.exec() calls this automatically using the decorator's config.

        Default behavior (all defaults):
            - Shows ALL ancestors (root → parent chain)
            - Shows ALL same-level siblings that completed before this node
            - Does NOT show siblings' children (each sibling is one line)
            - Does NOT show the current node itself

        This default guarantees maximum prompt cache hit rate: every call
        sees the previous call's context as a prefix, plus new content
        appended at the end.

        Args:
            depth:      How many ancestor levels to show.
                        -1 = all (default), 0 = none, 1 = parent only, N = up to N levels.

            siblings:   How many previous siblings to show.
                        -1 = all (default), 0 = none, N = last N siblings.
                        When N is set, keeps the N most recent (closest to current).

            level:      Override render level for ALL nodes in the output.
                        If None, each node uses its own `render` setting.
                        Values: "trace" / "detail" / "summary" / "result" / "silent"

            include:    Path whitelist. Only show nodes whose path matches.
                        Supports * wildcard: "root/navigate_0/*" matches all children.

            exclude:    Path blacklist. Hide nodes whose path matches.
                        Supports * wildcard.

            branch:     List of node names whose children should be expanded.
                        By default, siblings are shown as one line (no children).
                        branch=["observe"] would expand observe nodes to show
                        their run_ocr/detect_all children.
                        Respects compress: compressed nodes are NOT expanded.

            max_tokens: Approximate token budget. When exceeded, drops the
                        oldest siblings first. Uses len(text)/4 as token estimate.

        Returns:
            A string ready to be injected into an LLM prompt.
            Empty string if nothing to show.

        Examples:
            ctx.summarize()                              # see everything (default)
            ctx.summarize(depth=1, siblings=3)           # parent + last 3 siblings
            ctx.summarize(depth=0, siblings=0)           # nothing (isolated mode)
            ctx.summarize(level="detail")                # force all nodes to detail
            ctx.summarize(include=["root/navigate_0/*"]) # only navigate's children
            ctx.summarize(branch=["observe"])             # expand observe's children
            ctx.summarize(max_tokens=1000)               # with token budget
        """
        lines = ["Execution Context (most recent call last):"]

        # --- Ancestors: root → ... → parent ---
        # Collect ancestors from root to parent, each indented by depth
        if depth != 0 and self.parent and self.parent.name:
            ancestors = []
            node = self.parent
            while node and node.name:
                ancestors.append(node)
                node = node.parent
                if depth > 0 and len(ancestors) >= depth:
                    break
            for a in reversed(ancestors):
                if not _node_allowed(a, include, exclude):
                    continue
                lines.append(a._render_traceback("    " * a._depth(), level))

        # --- Siblings: previous same-level nodes ---
        if self.parent:
            sibling_indent = "    " * self._depth()

            sibling_parts = []
            for c in self.parent.children:
                if c is self:
                    break
                if c.status == "running":
                    continue
                if not _node_allowed(c, include, exclude):
                    continue

                render_level = level or c.render
                if render_level == "silent":
                    continue

                rendered = c._render_traceback(sibling_indent, render_level)

                if branch and c.name in branch:
                    if not (c.compress and c.status != "running"):
                        rendered += "\n" + c._render_branch_traceback(
                            render_level, c._depth() + 1, include, exclude,
                        )

                sibling_parts.append(rendered)

            if siblings >= 0:
                sibling_parts = sibling_parts[-siblings:] if siblings > 0 else []

            if max_tokens is not None:
                total = sum(len(s) for s in sibling_parts)
                while sibling_parts and total > max_tokens * 4:
                    removed = sibling_parts.pop(0)
                    total -= len(removed)

            lines.extend(sibling_parts)

        # --- Current call at its natural indent ---
        self_indent = "    " * self._depth()
        lines.append(f"{self_indent}- {self._call_path()}({_fmt_params(self.params)})  <-- Current Call")
        if self.prompt:
            lines.append(f'{self_indent}    """{self.prompt}"""')

        return "\n".join(lines)

    # ==================================================================
    # RENDERING — format a single node as text
    # ==================================================================

    def _render_traceback(self, indent: str, level: str) -> str:
        """
        Render this node in traceback format.

        Level controls how much detail:
          - "summary" (default): name, purpose, input, output, status
          - "detail": summary + LLM raw_reply
          - "result": just name and output
          - "silent": empty string
        """
        if self.summary_fn:
            return self.summary_fn(self)

        if level == "silent":
            return ""

        dur = f", {self.duration_ms:.0f}ms" if self.end_time else ""
        lines = [f"{indent}- {self._call_path()}({_fmt_params(self.params)})"]

        if level == "result":
            if self.output is not None:
                lines.append(f"{indent}    return {_json(self.output, 200)}")
            return "\n".join(lines)

        # docstring as annotation (not "Purpose:")
        if self.prompt:
            lines.append(f'{indent}    """{self.prompt}"""')

        if self.output is not None:
            lines.append(f"{indent}    return {_json(self.output, 300)}")
        if self.error:
            lines.append(f"{indent}    Error: {self.error}")
        lines.append(f"{indent}    Status: {self.status}{dur}")

        # detail adds LLM interaction
        if level == "detail" and self.raw_reply is not None:
            lines.append(f"{indent}    LLM reply: {self.raw_reply[:500]}")

        return "\n".join(lines)

    def _render_branch_traceback(
        self, level: Optional[str], depth: int = 1,
        include: Optional[list] = None, exclude: Optional[list] = None,
    ) -> str:
        """Render children recursively in traceback format."""
        lines = []
        for c in self.children:
            if not _node_allowed(c, include, exclude):
                continue
            render_level = level or c.render
            if render_level != "silent":
                indent = "  " * depth
                lines.append(c._render_traceback(indent, render_level))
                if c.children and not (c.compress and c.status != "running"):
                    lines.append(c._render_branch_traceback(render_level, depth + 1, include, exclude))
        return "\n".join(lines)

    # --- Legacy _render for tree()/traceback() compatibility ---
    def _render(self, level: str) -> str:
        """Legacy render for backward compat. Delegates to _render_traceback."""
        return self._render_traceback("", level)

    def _render_branch(
        self, level: Optional[str], indent: int = 1,
        include: Optional[list] = None, exclude: Optional[list] = None,
    ) -> str:
        """Legacy branch render for backward compat."""
        return self._render_branch_traceback(level, indent, include, exclude)

    # ==================================================================
    # TREE INSPECTION — human-readable views
    # ==================================================================

    def tree(self, indent: int = 0) -> str:
        """
        Full tree view for debugging. Shows ALL nodes regardless of
        render/compress settings.

        Example output:
            root …
              navigate ✓ 3200ms → {'success': True}
                observe ✓ 1200ms → {'found': True}
                act ✓ 820ms → {'clicked': True}
        """
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
        """
        Error traceback in a format similar to Python's.

        Example output:
            Agentic Traceback:
              navigate(target="login") → error, 4523ms
                observe(task="find login") → success, 1200ms
                act(target="login") → error, 820ms
                  error: element not interactable
        """
        lines = ["Agentic Traceback:"]
        self._traceback_lines(lines, indent=1)
        return "\n".join(lines)

    def _traceback_lines(self, lines: list, indent: int):
        prefix = "  " * indent
        dur = f", {self.duration_ms:.0f}ms" if self.end_time else ""
        lines.append(f"{prefix}{self.name}({_fmt_params(self.params)}) → {self.status}{dur}")
        if self.error:
            lines.append(f"{prefix}  error: {self.error}")
        for c in self.children:
            c._traceback_lines(lines, indent + 1)

    # ==================================================================
    # PERSISTENCE — save the tree to disk
    # ==================================================================

    def save(self, path: str):
        """
        Save the full tree to a file.

        .md   → human-readable tree view (same as tree())
        .jsonl → one JSON object per node, machine-readable

        Raises ValueError for unsupported extensions.
        """
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if path.endswith(".md"):
            with open(path, "w") as f:
                f.write(self.tree())
        elif path.endswith(".jsonl"):
            with open(path, "w") as f:
                for record in self._to_records():
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        else:
            raise ValueError(
                f"Unsupported file extension: {path}. Use .md or .jsonl."
            )

    def _to_records(self, tree_depth: int = 0) -> list[dict]:
        """Flatten the tree into a list of dicts for JSONL export."""
        records = [{
            "depth": tree_depth,
            "path": self.path,
            "name": self.name,
            "prompt": self.prompt,
            "params": self.params,
            "output": self.output,
            "raw_reply": self.raw_reply,
            "error": self.error,
            "status": self.status,
            "render": self.render,
            "compress": self.compress,
            "duration_ms": self.duration_ms,
        }]
        for c in self.children:
            records.extend(c._to_records(tree_depth + 1))
        return records


# ======================================================================
# Module-level convenience functions
# ======================================================================

def get_context() -> Optional[Context]:
    """Get the currently active Context node. None if outside any @agentic_function."""
    return _current_ctx.get(None)


def get_root_context() -> Optional[Context]:
    """Get the root of the current Context tree.
    
    If called inside an @agentic_function, walks up to the root.
    If called after execution, returns the last completed tree.
    """
    ctx = _current_ctx.get(None)
    if ctx is not None:
        while ctx.parent is not None:
            ctx = ctx.parent
        return ctx
    return _last_root


def init_root(name: str = "root") -> Context:
    """
    Manually create a root Context node.

    Usually not needed — @agentic_function creates the root automatically
    when the first decorated function is called.
    """
    root = Context(name=name, start_time=time.time(), status="running")
    _current_ctx.set(root)
    return root


# ======================================================================
# Internal helpers
# ======================================================================

def _node_allowed(node: Context, include: Optional[list], exclude: Optional[list]) -> bool:
    """Check if a node passes include/exclude path filters."""
    if include is not None:
        return any(_path_matches(node.path, p) for p in include)
    if exclude is not None:
        return not any(_path_matches(node.path, p) for p in exclude)
    return True


def _path_matches(path: str, pattern: str) -> bool:
    """Match a node path against a pattern. Supports * wildcard and /* suffix."""
    if pattern.endswith("/*"):
        prefix = pattern[:-2]
        return path.startswith(prefix + "/") or path == prefix
    if "*" in pattern:
        import fnmatch
        return fnmatch.fnmatch(path, pattern)
    return path == pattern


def _fmt_params(params: dict) -> str:
    """Format function parameters for display. Truncates long values."""
    if not params:
        return ""
    parts = []
    for k, v in params.items():
        v_str = repr(v) if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
        if len(v_str) > 50:
            v_str = v_str[:47] + "..."
        parts.append(f"{k}={v_str}")
    return ", ".join(parts)


def _json(obj: Any, max_len: int = 0) -> str:
    """Serialize to JSON string, optionally truncated."""
    s = json.dumps(obj, ensure_ascii=False, default=str)
    if max_len and len(s) > max_len:
        return s[:max_len - 3] + "..."
    return s
