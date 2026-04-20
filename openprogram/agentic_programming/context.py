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
        name, prompt, params, parent, children, expose, render_range,
        start_time

    Set by @agentic_function (on exit, function nodes):
        output OR error, status, end_time

    Set by runtime.exec() (exec nodes):
        name="_exec", node_type="exec", raw_reply, output, status
    """

    # --- Identity & input ---
    name: str = ""              # Function name (from fn.__name__), or "_exec" for exec nodes
    prompt: str = ""            # Docstring (from fn.__doc__) — doubles as LLM prompt
    system: str = ""            # Optional system prompt (from @agentic_function(system=...))
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

    expose: str = "io"
    # What OUTSIDE observers see of this node after it completes.
    # Three levels:
    #   "io"     — only name + return value (subtree hidden)           [DEFAULT]
    #   "full"   — docstring + params + output + LLM reply + subtree
    #   "hidden" — not shown at all
    #
    # While a function is still running, its expose is ignored: callers see
    # the full in-progress state. expose only gates post-completion visibility.
    #
    # Example:
    #   navigate() calls observe/act/verify. navigate.expose="io" (default) →
    #   after navigate returns, siblings see "navigate: {success: true}",
    #   not the 10 sub-steps. navigate.expose="full" → the whole subtree
    #   is visible.
    #
    # The children are ALWAYS recorded in the tree; expose only affects how
    # render_context() picks nodes into the LLM prompt. tree() and save()
    # always show the complete structure.

    source_file: str = ""
    # Absolute path to the source file where this function is defined.
    # Set automatically by @agentic_function. Used by the visualizer
    # to show source code even after server restart (when modules aren't loaded).

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

    # --- Incoming context scope (set via @agentic_function decorator) ---
    render_range: Optional[dict] = field(default=None, repr=False)
    # Scope config for this node's render_context() calls. Forwarded as
    # kwargs: exec_ctx.render_context(**parent_ctx.render_range).
    # Keys: depth, siblings, include, exclude, branch, max_tokens
    # (see render_context() docstring for each parameter's semantics).
    # If None, runtime.exec() calls render_context() with defaults.

    # --- Optional: user-provided render function ---
    summary_fn: Optional[Callable] = field(default=None, repr=False)
    # If set, _render_traceback() calls this instead of the built-in formatting.
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
    # RENDER CONTEXT — query the tree for LLM context
    # ==================================================================

    def render_context(
        self,
        depth: int = -1,
        siblings: int = -1,
        prompted_functions: Optional[set] = None,
        include: Optional[list] = None,
        exclude: Optional[list] = None,
        branch: Optional[list] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Walk the Context tree and produce a text prompt for the LLM.

        This is the ONLY way Context data flows into LLM calls.
        runtime.exec() calls this automatically using each node's render_range.

        Every visible node is rendered according to its own `expose` field:
            "io"     — one line showing name + return value; subtree hidden
            "full"   — docstring + params + output + LLM reply; subtree expandable
            "hidden" — skipped entirely
        While a node is `running`, its expose is ignored and it's rendered full.

        Default scope (all parameters at their defaults):
            - ALL ancestors (root → parent chain)
            - ALL same-level siblings that completed before this node
            - Siblings' subtrees are only shown when sibling.expose == "full"
              AND the sibling name is in `branch` (opt-in)
            - The current node itself is not rendered

        This guarantees maximum prompt-cache hit rate: every call sees the
        previous call's context as a prefix, plus new content appended.

        Args:
            depth:      How many ancestor levels to show.
                        -1 = all (default), 0 = none, 1 = parent only, N = up to N levels.

            siblings:   How many previous siblings to show.
                        -1 = all (default), 0 = none, N = last N siblings.
                        When N is set, keeps the N most recent (closest to current).

            include:    Path whitelist. Only show nodes whose path matches.
                        Supports * wildcard: "root/navigate_0/*" matches all children.

            exclude:    Path blacklist. Hide nodes whose path matches.
                        Supports * wildcard.

            branch:     List of node names whose subtrees should be expanded.
                        Only meaningful for siblings with expose="full"
                        (an "io" sibling refuses expansion regardless of branch).

            max_tokens: Approximate token budget for sibling context. When exceeded,
                        drops the oldest siblings first. The current call block is
                        always preserved. Uses len(text)/4 as token estimate.

        Returns:
            A string ready to be injected into an LLM prompt.
            Empty string if nothing to show.

        Examples:
            ctx.render_context()                              # see everything (default)
            ctx.render_context(depth=1, siblings=3)           # parent + last 3 siblings
            ctx.render_context(depth=0, siblings=0)           # isolated (nothing visible)
            ctx.render_context(include=["root/navigate_0/*"]) # only navigate's children
            ctx.render_context(branch=["observe"])            # expand observe's children
            ctx.render_context(max_tokens=1000)               # with token budget
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
                # Ancestors on the call chain are always shown "full" —
                # the current call needs its own ancestor context to be
                # legible. If we've already shown this function's
                # docstring earlier in the session, collapse to "io".
                ancestor_level = "full"
                if a.name in prompted_functions:
                    ancestor_level = "io"
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
                        child_level = child.expose
                        if child_level != "hidden":
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

                render_level = c.expose
                if render_level == "hidden":
                    continue

                # Same function called in a loop: skip docstring, show only I/O
                if c.name == self.name:
                    render_level = "io"

                rendered = c._render_traceback(sibling_indent, render_level)

                # Only "full" siblings can expand their subtree, and only
                # when the caller explicitly opts in via branch=[...].
                if branch and c.name in branch and render_level == "full":
                    rendered += "\n" + c._render_branch_traceback(
                        c._depth() + 1, include, exclude,
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
    # RENDER MESSAGES — flatten tree into a multi-turn conversation
    # ==================================================================

    def render_messages(self) -> list:
        """
        Flatten the execution tree into a multi-turn message list, using
        stack semantics keyed off each function node's ``expose`` field.

        Scope rules (mirror ``render_context``):
            - Walk the ancestor chain root → self.parent.
            - At each ancestor, emit pairs for completed children that come
              before the next ancestor in the chain (or before ``self`` at
              the innermost level).
            - ``self`` is the currently-running exec node and always
              contributes a final UserMessage at the end (no assistant yet).

        Node → message conversion:
            - exec                    → (UserMessage(input_blocks),
                                         AssistantMessage(raw_reply))
            - function + expose="io"  → (UserMessage(call signature),
                                         AssistantMessage(return value))
              [stack frame popped — internal pairs collapsed]
            - function + expose="full"→ recursive expansion of its completed
                                         children (their own pairs)
              [stack frame kept open]
            - function + expose="hidden" → skipped

        Because new exec/function children are appended to ``parent.children``
        in call order, the resulting message list is strictly append-only
        across successive calls to ``exec()`` — enabling maximum prompt-cache
        hit rate under Anthropic / OpenAI automatic caching.

        Returns:
            list of pi-ai ``Message`` objects, ending in a UserMessage for
            the current turn.
        """
        # Walk ancestor chain: root → self.parent
        ancestors: list["Context"] = []
        node = self.parent
        while node is not None:
            ancestors.append(node)
            node = node.parent
        ancestors.reverse()  # root → parent

        messages: list = []
        for i, ancestor in enumerate(ancestors):
            # Boundary = next ancestor in chain, or self at innermost level.
            boundary = ancestors[i + 1] if i + 1 < len(ancestors) else self
            for child in ancestor.children:
                if child is boundary:
                    break
                if child.status == "running":
                    continue
                messages.extend(_node_as_messages(child))

        messages.append(_exec_as_user_message(self))
        return messages

    # ==================================================================
    # RENDERING — format a single node as text
    # ==================================================================

    def _render_traceback(self, indent: str, expose: str) -> str:
        """Render this node in traceback format.

        expose:
          - "hidden": empty string (caller should skip before reaching here)
          - "io":     compact — name + return value (for function nodes),
                     → content / ← reply (for exec nodes)
          - "full":   verbose — adds docstring, params, status, attempts, LLM reply
        """
        if self.summary_fn:
            return self.summary_fn(self)

        if expose == "hidden":
            return ""

        # --- Exec nodes: compact → content / ← reply format ---
        if self.node_type == "exec":
            content = self.params.get("_content", "")
            reply = self.raw_reply or ""
            if expose == "io":
                return f"{indent}→ {content[:200]}\n{indent}← {reply[:300]}"
            # "full": show more
            lines = [f"{indent}→ {content[:500]}"]
            if reply:
                lines.append(f"{indent}← {reply[:500]}")
            if self.error:
                lines.append(f"{indent}  Error: {self.error}")
            return "\n".join(lines)

        # --- Function nodes ---
        dur = f", {self.duration_ms:.0f}ms" if self.end_time else ""
        lines = [f"{indent}- {self._call_path()}({_fmt_params(self.params)})"]

        if expose == "io":
            if self.output is not None:
                lines.append(f"{indent}    return {_json(self.output, 200)}")
            # Errors and failed attempts always surface, even in "io" mode —
            # callers need to know if a sibling had retries or ultimately errored.
            if self.error:
                lines.append(f"{indent}    Error: {self.error}")
            io_failed = [a for a in self.attempts if a.get("error")]
            for child in self.children:
                if child.node_type == "exec":
                    io_failed.extend(a for a in child.attempts if a.get("error"))
            for a in io_failed:
                lines.append(f"{indent}    [Attempt {a['attempt']} FAILED] {a['error']}")
            return "\n".join(lines)

        # "full"
        if self.prompt:
            lines.append(f'{indent}    """{self.prompt}"""')
        if self.output is not None:
            lines.append(f"{indent}    return {_json(self.output, 300)}")
        if self.error:
            lines.append(f"{indent}    Error: {self.error}")

        failed_attempts = [a for a in self.attempts if a.get("error")]
        for child in self.children:
            if child.node_type == "exec":
                failed_attempts.extend(a for a in child.attempts if a.get("error"))
        for a in failed_attempts:
            lines.append(f"{indent}    [Attempt {a['attempt']} FAILED] {a['error']}")
            if a.get("reply"):
                lines.append(f"{indent}      Reply was: {str(a['reply'])[:200]}")

        lines.append(f"{indent}    Status: {self.status}{dur}")

        if self.raw_reply is not None:
            lines.append(f"{indent}    LLM reply: {self.raw_reply[:500]}")

        return "\n".join(lines)

    def _render_branch_traceback(
        self, depth: int = 1,
        include: Optional[list] = None, exclude: Optional[list] = None,
    ) -> str:
        """Render children recursively. Each child's expose controls its own
        visibility; the subtree below a child is only expanded when the child
        is in "full" mode."""
        lines = []
        for c in self.children:
            if not _node_allowed(c, include, exclude):
                continue
            # Running children: force "full" so the in-progress work is visible.
            child_expose = "full" if c.status == "running" else c.expose
            if child_expose == "hidden":
                continue
            indent = "    " * depth
            lines.append(c._render_traceback(indent, child_expose))
            if c.children and child_expose == "full":
                lines.append(c._render_branch_traceback(depth + 1, include, exclude))
        return "\n".join(lines)

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


# ---------------------------------------------------------------------------
# Helpers — convert exec Context nodes to pi-ai Messages for render_messages()
# ---------------------------------------------------------------------------


def _exec_blocks_to_content(blocks: list) -> list:
    """Convert OpenProgram content blocks (list of dicts) to pi-ai content parts.

    Text blocks → TextContent; image blocks (with path or base64 data) →
    ImageContent. Unsupported block types are skipped. If no usable blocks
    remain, returns a single empty TextContent so the message is well-formed.
    """
    import base64
    from openprogram.providers.types import ImageContent, TextContent

    parts: list = []
    for block in blocks or []:
        btype = block.get("type", "text") if isinstance(block, dict) else "text"
        # Skip any system-role text block — those belong in system_prompt, not user content.
        if isinstance(block, dict) and block.get("role") == "system":
            continue
        if btype == "text":
            parts.append(TextContent(type="text", text=block.get("text", "")))
        elif btype == "image":
            data = block.get("data")
            mime = block.get("mime_type")
            if not data and block.get("path"):
                path = block["path"]
                with open(path, "rb") as fh:
                    data = base64.b64encode(fh.read()).decode()
                if not mime:
                    low = path.lower()
                    if low.endswith(".png"):
                        mime = "image/png"
                    elif low.endswith(".jpg") or low.endswith(".jpeg"):
                        mime = "image/jpeg"
                    elif low.endswith(".gif"):
                        mime = "image/gif"
                    elif low.endswith(".webp"):
                        mime = "image/webp"
                    else:
                        mime = "image/png"
            if data:
                parts.append(ImageContent(type="image", data=data, mime_type=mime or "image/png"))
        # audio/file: skipped until upstream provider adapters accept them

    if not parts:
        parts.append(TextContent(type="text", text=""))
    return parts


def _exec_as_user_message(exec_node: "Context"):
    """Build a UserMessage from an exec node's original input blocks."""
    import time as _time
    from openprogram.providers.types import TextContent, UserMessage

    params = exec_node.params or {}
    blocks = list(params.get("_content_blocks") or [])
    if not blocks:
        # Older exec nodes only recorded the text-merged form.
        blocks = [{"type": "text", "text": params.get("_content", "")}]

    parts = _exec_blocks_to_content(blocks)
    ts = int(exec_node.start_time * 1000) if exec_node.start_time else int(_time.time() * 1000)
    return UserMessage(role="user", content=parts, timestamp=ts)


def _exec_as_assistant_message(exec_node: "Context"):
    """Build an AssistantMessage from an exec node's raw_reply."""
    import time as _time
    from openprogram.providers.types import AssistantMessage, TextContent, Usage

    text = exec_node.raw_reply or ""
    ts = int(exec_node.end_time * 1000) if exec_node.end_time else int(_time.time() * 1000)
    # api/provider/model aren't persisted on the Context node — use empty
    # placeholders since these are only reconstructed-for-context messages.
    return AssistantMessage(
        role="assistant",
        content=[TextContent(type="text", text=text)],
        api="",
        provider="",
        model="",
        usage=Usage(),
        stop_reason="stop",
        timestamp=ts,
    )


def _function_as_user_message(node: "Context"):
    """UserMessage showing a completed function's call signature + docstring."""
    import time as _time
    from openprogram.providers.types import TextContent, UserMessage

    params_str = _fmt_params(node.params)
    lines = [f"call {node.name}({params_str})"]
    if node.prompt:
        lines.append(f'"""{node.prompt}"""')
    text = "\n".join(lines)
    ts = int(node.start_time * 1000) if node.start_time else int(_time.time() * 1000)
    return UserMessage(
        role="user",
        content=[TextContent(type="text", text=text)],
        timestamp=ts,
    )


def _function_as_assistant_message(node: "Context"):
    """AssistantMessage showing a completed function's return value or error."""
    import time as _time
    from openprogram.providers.types import AssistantMessage, TextContent, Usage

    if node.status == "error" and node.error:
        text = f"raised {node.error}"
    elif node.output is not None:
        text = f"returned {_json(node.output, 500)}"
    else:
        text = "returned None"
    ts = int(node.end_time * 1000) if node.end_time else int(_time.time() * 1000)
    return AssistantMessage(
        role="assistant",
        content=[TextContent(type="text", text=text)],
        api="",
        provider="",
        model="",
        usage=Usage(),
        stop_reason="stop",
        timestamp=ts,
    )


def _node_as_messages(node: "Context") -> list:
    """Recursively convert a completed Context node to message-sequence form.

    Stack semantics driven by ``node.expose``:
      - exec, "io"           → one pair (input → final reply)      [tool loop collapsed]
      - exec, "full"         → full tool-loop trace (input, A+toolCalls, TR*, A_final)
      - function, "io"       → one pair (call → return)            [collapsed]
      - function, "full"     → flattened messages of completed descendants
      - any node, "hidden"   → empty list                          [dropped]
    """
    if node.node_type == "exec":
        return _exec_node_as_messages(node)

    if node.node_type in ("tool_call", "assistant_round"):
        # These live inside an exec subtree and are emitted by
        # _exec_node_as_messages; at scope-walk level they're inert.
        return []

    # Function node
    expose = getattr(node, "expose", "io") or "io"
    if expose == "hidden":
        return []
    if expose == "full":
        msgs: list = []
        for child in node.children:
            if child.status == "running":
                continue
            msgs.extend(_node_as_messages(child))
        return msgs

    # expose == "io" (default): single collapsed pair
    if node.status == "running":
        return []
    return [_function_as_user_message(node), _function_as_assistant_message(node)]


def _exec_node_as_messages(exec_node: "Context") -> list:
    """Render an exec node into messages, respecting its ``expose`` field.

    - ``expose="hidden"`` → []
    - ``expose="io"`` (default) → ``(UserMessage, AssistantMessage(final_text))``.
      The tool-loop trace is hidden; the next exec sees only the final reply.
    - ``expose="full"`` → full tool-loop transcript rebuilt from each
      ``assistant_round`` child:
      ``UserMessage → AssistantMessage(thinking+text+ToolCalls) → ToolResultMessage(s) → ...``
      Round ordering is preserved. Rounds with no tool calls become plain
      assistant turns (e.g. a final answer or a mid-loop narration).
    """
    if exec_node.status != "success" or exec_node.raw_reply is None:
        return []

    expose = getattr(exec_node, "expose", "io") or "io"
    if expose == "hidden":
        return []

    rounds = [c for c in exec_node.children
              if c.node_type == "assistant_round"
              and getattr(c, "expose", "io") != "hidden"]

    # Collapsed form — default, or no rounds recorded (no tool loop happened)
    if expose != "full" or not rounds:
        return [_exec_as_user_message(exec_node), _exec_as_assistant_message(exec_node)]

    # Full form — expand the precise tool loop round-by-round
    import time as _time
    from openprogram.providers.types import (
        AssistantMessage,
        TextContent,
        ThinkingContent,
        ToolCall,
        ToolResultMessage,
        Usage,
    )

    messages: list = [_exec_as_user_message(exec_node)]

    for round_node in rounds:
        params = round_node.params or {}
        thinking = params.get("_thinking") or ""
        text = params.get("_text") or ""
        stop_reason = params.get("_stop_reason") or "stop"

        tool_call_children = [c for c in round_node.children
                              if c.node_type == "tool_call" and c.status != "running"
                              and getattr(c, "expose", "io") != "hidden"]

        content_blocks: list = []
        if thinking:
            content_blocks.append(ThinkingContent(type="thinking", thinking=thinking))
        if text:
            content_blocks.append(TextContent(type="text", text=text))
        for tc_node in tool_call_children:
            tool_call_id = (tc_node.params or {}).get("_tool_call_id") or f"call_{id(tc_node)}"
            args = {k: v for k, v in (tc_node.params or {}).items() if not k.startswith("_")}
            content_blocks.append(ToolCall(
                type="toolCall",
                id=tool_call_id,
                name=tc_node.name,
                arguments=args,
            ))

        if not content_blocks:
            continue

        round_ts = int(round_node.start_time * 1000) if round_node.start_time else int(_time.time() * 1000)
        messages.append(AssistantMessage(
            role="assistant",
            content=content_blocks,
            api="",
            provider="",
            model="",
            usage=Usage(),
            stop_reason=stop_reason if stop_reason in ("stop", "length", "toolUse") else "stop",
            timestamp=round_ts,
        ))

        for tc_node in tool_call_children:
            tool_call_id = (tc_node.params or {}).get("_tool_call_id") or f"call_{id(tc_node)}"
            result_text = tc_node.output if isinstance(tc_node.output, str) else (
                _json(tc_node.output, 0) if tc_node.output is not None else ""
            )
            result_ts = int(tc_node.end_time * 1000) if tc_node.end_time else round_ts
            messages.append(ToolResultMessage(
                role="toolResult",
                tool_call_id=tool_call_id,
                tool_name=tc_node.name,
                content=[TextContent(type="text", text=str(result_text))],
                is_error=(tc_node.status == "error"),
                timestamp=result_ts,
            ))

    return messages
