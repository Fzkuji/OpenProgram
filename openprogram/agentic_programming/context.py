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

# Event broadcasting (on_event / _emit_event) lives in .events.
# Persistence (save / from_jsonl / to_dict / to_records) lives in .persistence.
# ask_user / FollowUp / run_with_follow_up live in
# openprogram.programs.functions.buildin.ask_user — they're built-in user
# interaction tools, not paradigm primitives.
from openprogram.agentic_programming.events import on_event, off_event, _emit_event


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
# Currently active Context node. @agentic_function sets on entry, resets on exit.
_current_ctx: ContextVar[Optional["Context"]] = ContextVar(
    "_current_ctx", default=None
)


# ---------------------------------------------------------------------------
# Context — one node in the execution tree
# ---------------------------------------------------------------------------

@dataclass
class Context:
    """
    One node in the execution tree.

    Two node types:
      - "function" — created by @agentic_function, represents a function call
      - "exec"     — created by runtime.exec(), represents a single LLM call

    Users never create or modify Context objects directly.
    @agentic_function creates function nodes, runtime.exec() creates exec nodes.

    Fields are grouped by who sets them:

    Set by @agentic_function (on entry, function nodes):
        name, prompt, params, parent, children, render, compress,
        start_time, _summarize_kwargs

    Set by @agentic_function (on exit, function nodes):
        output OR error, status, end_time

    Set by runtime.exec() (exec nodes):
        name="_exec", node_type="exec", raw_reply, output, status
    """

    # --- Identity & input ---
    name: str = ""              # Function name (from fn.__name__), or "_exec" for exec nodes
    prompt: str = ""            # Docstring (from fn.__doc__) — doubles as LLM prompt
    system: str = ""            # Optional system prompt (from @agentic_function(system=...))
    no_tools: bool = False      # Dispatcher mode: forbid tool use in runtime.exec

    params: dict = field(default_factory=dict)  # Call arguments
    node_type: str = "function" # "function" or "exec"

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

    source_file: str = ""
    # Absolute path to the source file where this function is defined.
    # Set automatically by @agentic_function. Used by the visualizer
    # to show source code even after server restart (when modules aren't loaded).
    #
    # Use for high-level orchestrating functions. Example:
    #   navigate(compress=True) has children observe, act, verify.
    #   After navigate finishes, others see "navigate: {success: true}"
    #   without the 10 sub-steps inside.
    #
    # The children are still fully recorded in the tree — compress only
    # affects how summarize() renders this node. tree() and save() always
    # show the complete structure.

    # --- LLM call record ---
    raw_reply: str = None            # LLM response text. For function nodes: latest
                                     # child exec's reply (backward compat). For exec
                                     # nodes: the reply from this LLM call.
    attempts: list = field(default_factory=list)
    # Each exec() attempt is recorded here, whether it succeeds or fails:
    # {"attempt": 1, "reply": "LLM response" or None, "error": "error msg" or None}

    # --- Follow-up handler (per-context) ---
    ask_user_handler: Optional[Callable] = field(default=None, repr=False)
    # If set, ask_user() calls this handler when triggered from this context
    # or any descendant that doesn't have its own handler.
    # Signature: fn(question: str) -> str

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

    def find_by_path(self, target_path: str) -> Optional["Context"]:
        """Find a descendant Context by its path. Returns None if not found."""
        if self.path == target_path:
            return self
        for child in self.children:
            result = child.find_by_path(target_path)
            if result is not None:
                return result
        return None

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

    def render_tree(self) -> str:
        """Render a clean call tree from root, marking the current node.

        Output example:
            create
            └── generate_code  <-- Current

        Or for edit() with 3 rounds:
            edit
            ├── generate_code  ✓
            ├── generate_code  ✓
            └── generate_code  <-- Current
        """
        # Find root
        root = self
        while root.parent:
            root = root.parent

        lines = []
        self._render_tree_node(root, lines, "", True)
        return "\n".join(lines)

    def _render_tree_node(self, node: "Context", lines: list, prefix: str, is_root: bool):
        """Recursively render one node and its children."""
        # Status marker
        if node is self:
            marker = "  <-- Current"
        elif node.status == "success":
            marker = "  ✓"
        elif node.status == "error":
            marker = "  ✗"
        elif node.status == "running":
            marker = "  ..."
        else:
            marker = ""

        if is_root:
            lines.append(f"{node.name}{marker}")
            child_prefix = ""
        else:
            lines.append(f"{prefix}{node.name}{marker}")
            child_prefix = prefix.replace("├── ", "│   ").replace("└── ", "    ")

        children = node.children
        for i, child in enumerate(children):
            is_last = (i == len(children) - 1)
            connector = "└── " if is_last else "├── "
            self._render_tree_node(child, lines, child_prefix + connector, False)

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
        prompted_functions: Optional[set] = None,
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

            max_tokens: Approximate token budget for sibling context. When exceeded,
                        drops the oldest siblings first. The current call block is
                        always preserved. Uses len(text)/4 as token estimate.

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
        lines = []
        # Track which functions have had their docstrings shown
        if prompted_functions is None:
            prompted_functions = set()

        # --- Ancestors: root → ... → parent ---
        # Collect ancestors from root to parent, each indented by depth
        # Calculate base depth so the outermost ancestor starts at indent 0
        base_depth = self._depth()
        if depth != 0 and self.parent and self.parent.name:
            ancestors = []
            node = self.parent
            while node and node.name:
                ancestors.append(node)
                node = node.parent
                if depth > 0 and len(ancestors) >= depth:
                    break
            if ancestors:
                base_depth = ancestors[-1]._depth()

            # Build set of nodes on the direct ancestor path (for sibling rendering)
            ancestor_set = set(id(a) for a in ancestors)

            for a in reversed(ancestors):
                if not _node_allowed(a, include, exclude):
                    continue
                ancestor_level = level
                if a.name in prompted_functions:
                    ancestor_level = "result"
                else:
                    # Exec nodes don't mark their direct parent as prompted,
                    # because each exec is an independent LLM call that needs
                    # to see the parent function's docstring.
                    if not (self.node_type == "exec" and a is self.parent):
                        prompted_functions.add(a.name)
                indent = "    " * (a._depth() - base_depth)
                lines.append(a._render_traceback(indent, ancestor_level))

                # For exec nodes: render ancestor's completed children that
                # come before the next node in the ancestor chain. This gives
                # exec nodes visibility into the broader sibling context
                # (e.g., step_a's results when inside step_b's exec node).
                if self.node_type == "exec":
                    child_indent = "    " * (a._depth() - base_depth + 1)
                    for child in a.children:
                        if id(child) in ancestor_set or child is self:
                            break  # stop before the next ancestor in the chain
                        if child.status == "running":
                            continue
                        if not _node_allowed(child, include, exclude):
                            continue
                        child_level = level or child.render
                        if child_level != "silent":
                            lines.append(child._render_traceback(child_indent, child_level))

        # --- Siblings: previous same-level nodes ---
        if self.parent:
            sibling_indent = "    " * (self._depth() - base_depth)

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

                # Same function called in a loop: skip docstring, show only result
                if c.name == self.name and render_level != "result":
                    render_level = "result"

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

        # --- Current call ---
        self_indent = "    " * (self._depth() - base_depth)
        lines.append(f"{self_indent}- {self._call_path()}({_fmt_params(self.params)})  <-- Current Call")
        if self.prompt and self.name not in prompted_functions:
            lines.append(f'{self_indent}    """{self.prompt}"""')

        return "\n".join(lines)

    # ==================================================================
    # RENDERING — format a single node as text
    # ==================================================================

    def _render_traceback(self, indent: str, level: str) -> str:
        """
        Render this node in traceback format.

        Level controls how much detail:
          - "summary" (default): name, docstring, params, output, status, duration
          - "detail": summary + LLM raw_reply
          - "result": name + return value only
          - "silent": empty string
        """
        if self.summary_fn:
            return self.summary_fn(self)

        if level == "silent":
            return ""

        # --- Exec nodes: compact → content / ← reply format ---
        if self.node_type == "exec":
            content = self.params.get("_content", "")
            reply = self.raw_reply or ""
            if level == "result":
                return f"{indent}→ {content[:200]}\n{indent}← {reply[:300]}"
            # summary / detail: show more
            lines = [f"{indent}→ {content[:500]}"]
            if reply:
                lines.append(f"{indent}← {reply[:500]}")
            if self.error:
                lines.append(f"{indent}  Error: {self.error}")
            return "\n".join(lines)

        # --- Function nodes: standard rendering ---
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

        # Show failed attempts (own + exec children's)
        failed_attempts = [a for a in self.attempts if a.get("error")]
        for child in self.children:
            if child.node_type == "exec":
                failed_attempts.extend(a for a in child.attempts if a.get("error"))
        if failed_attempts:
            for a in failed_attempts:
                lines.append(f"{indent}    [Attempt {a['attempt']} FAILED] {a['error']}")
                if a.get("reply"):
                    lines.append(f"{indent}      Reply was: {str(a['reply'])[:200]}")

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
                indent = "    " * depth
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

    def tree(self, indent: int = 0, color: bool = True, _is_last: bool = True, _prefix: str = "") -> str:
        """
        Full tree view for debugging. Shows ALL nodes regardless of
        render/compress settings.

        Args:
            indent:  Legacy indent level (used if no tree connectors).
            color:   Use ANSI colors for terminal output (default True).
            _is_last: Internal — whether this is the last child.
            _prefix:  Internal — accumulated prefix string for tree lines.

        Example output (with color=False):
            login_flow ✓ 8.8s
            ├── observe ✓ 3.1s → "found login form at (200, 300)"
            ├── click ✓ 2.5s → "clicked login button"
            └── verify ✓ 3.2s → "dashboard confirmed"
        """
        # Format duration
        if self.end_time:
            ms = self.duration_ms
            dur = f" {ms/1000:.1f}s" if ms >= 1000 else f" {ms:.0f}ms"
        else:
            dur = ""

        # Status icon
        if self.status == "success":
            icon = "✓"
        elif self.status == "error":
            icon = "✗"
        else:
            icon = "⏳"

        # Output / error snippet
        if self.output is not None:
            out_str = str(self.output)
            if len(out_str) > 80:
                out_str = out_str[:77] + "..."
            out = f' → "{out_str}"'
        else:
            out = ""
        err = f" ERROR: {self.error}" if self.error else ""

        # Apply ANSI colors
        if color:
            c_reset = "\033[0m"
            c_name = "\033[1m"  # bold
            c_dim = "\033[2m"   # dim
            if self.status == "success":
                c_icon = "\033[32m"  # green
            elif self.status == "error":
                c_icon = "\033[31m"  # red
            else:
                c_icon = "\033[33m"  # yellow
            c_dur = "\033[36m"   # cyan
            c_out = "\033[2m"    # dim
            c_err = "\033[31m"   # red

            name_s = f"{c_name}{self.name}{c_reset}"
            icon_s = f"{c_icon}{icon}{c_reset}"
            dur_s = f"{c_dur}{dur}{c_reset}" if dur else ""
            out_s = f"{c_out}{out}{c_reset}" if out else ""
            err_s = f"{c_err}{err}{c_reset}" if err else ""
        else:
            name_s = self.name
            icon_s = icon
            dur_s = dur
            out_s = out
            err_s = err

        line = f"{_prefix}{name_s} {icon_s}{dur_s}{out_s}{err_s}"
        lines = [line]

        # Render children with tree connectors
        for i, c in enumerate(self.children):
            is_last_child = (i == len(self.children) - 1)
            if _prefix or self.parent is not None:
                # We're inside the tree, use connectors
                connector = "└── " if is_last_child else "├── "
                child_prefix = _prefix.replace("├── ", "│   ").replace("└── ", "    ")
                next_prefix = child_prefix + connector
            else:
                # Root node's children
                connector = "└── " if is_last_child else "├── "
                next_prefix = connector

            lines.append(c.tree(
                indent=indent + 1,
                color=color,
                _is_last=is_last_child,
                _prefix=next_prefix,
            ))

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
    # PERSISTENCE — thin delegates to openprogram.agentic_programming.persistence
    # ==================================================================

    def save(self, path: str | os.PathLike[str]) -> None:
        """Save the tree to disk. See persistence.save for format details."""
        from openprogram.agentic_programming.persistence import save as _save
        _save(self, path)

    def _to_dict(self) -> dict:
        from openprogram.agentic_programming.persistence import to_dict
        return to_dict(self)

    @classmethod
    def from_dict(cls, data: dict, parent: Optional["Context"] = None) -> "Context":
        from openprogram.agentic_programming.persistence import from_dict as _from_dict
        return _from_dict(data, parent)

    @classmethod
    def from_jsonl(cls, path: str | os.PathLike[str]) -> "Context":
        from openprogram.agentic_programming.persistence import from_jsonl as _from_jsonl
        return _from_jsonl(path)

    def _to_records(self, tree_depth: int = 0) -> list[dict]:
        from openprogram.agentic_programming.persistence import to_records
        return to_records(self, tree_depth)

    def _to_event_records(self) -> list[dict]:
        from openprogram.agentic_programming.persistence import to_event_records
        return to_event_records(self)


# ======================================================================
# Internal helpers
# ======================================================================

def _node_allowed(node: Context, include: Optional[list], exclude: Optional[list]) -> bool:
    """Check if a node passes include/exclude path filters.
    
    include and exclude are applied together:
    1. If include is set, node must match at least one include pattern
    2. If exclude is set, node must not match any exclude pattern
    Both conditions must be satisfied.
    """
    allowed = True
    if include is not None:
        allowed = any(_path_matches(node.path, p) for p in include)
    if allowed and exclude is not None:
        allowed = not any(_path_matches(node.path, p) for p in exclude)
    return allowed


def _path_matches(path: str, pattern: str) -> bool:
    """Match a node path against a pattern. Supports * wildcard and /* suffix.
    
    foo/* matches children of foo (e.g. foo/bar_0), NOT foo itself.
    """
    if pattern.endswith("/*"):
        prefix = pattern[:-2]
        return path.startswith(prefix + "/")
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


def _fmt_params_expanded(params: dict, call_path: str, indent: str, param_indent: str) -> str:
    """Format current call with fully expanded parameters, multi-line."""
    if not params:
        return f"{indent}- {call_path}()"

    # Check if any param is long enough to warrant multi-line
    short_parts = []
    has_long = False
    for k, v in params.items():
        v_str = repr(v) if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
        if len(v_str) > 80:
            has_long = True
            break
        short_parts.append(f"{k}={v_str}")

    if not has_long:
        return f"{indent}- {call_path}({', '.join(short_parts)})"

    # Multi-line format
    lines = [f"{indent}- {call_path}("]
    param_items = list(params.items())
    for i, (k, v) in enumerate(param_items):
        v_str = repr(v) if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
        comma = "," if i < len(param_items) - 1 else ""
        if "\n" in v_str or len(v_str) > 80:
            # Multi-line value: indent each line
            value_indent = param_indent + "    "
            v_lines = v_str.splitlines()
            lines.append(f"{param_indent}{k}={v_lines[0]}")
            for vl in v_lines[1:]:
                lines.append(f"{value_indent}{vl}")
            if comma:
                lines[-1] += comma
        else:
            lines.append(f"{param_indent}{k}={v_str}{comma}")
    lines.append(f"{indent})")
    return "\n".join(lines)


def _json(obj: Any, max_len: int = 0) -> str:
    """Serialize to JSON string, optionally truncated."""
    s = json.dumps(obj, ensure_ascii=False, default=str)
    if max_len and len(s) > max_len:
        return s[:max_len - 3] + "..."
    return s
