"""
Context — the execution record for one Agentic Function call.

Architecture:
    All function calls form a single tree. Each node is a Context.
    The tree is a COMPLETE RECORD of everything that happened.
    When feeding context to an LLM, summarize() QUERIES the tree selectively.
    
    Record and query are fully separated:
    - What gets recorded is NOT affected by queries
    - What gets queried does NOT affect the record

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

Key design decisions (lessons learned):
    1. We use contextvars.ContextVar for implicit call-stack tracking.
       Users never pass ctx manually — the decorator handles everything.
       We tried explicit ctx passing first, but it was error-prone.
    
    2. summarize() is a flexible tree query, NOT a fixed format.
       It supports depth, siblings, include/exclude paths, branch selection.
       Earlier versions had a rigid sibling_summaries() that only looked
       at immediate siblings — too limiting for complex call trees.
    
    3. Each node has an auto-computed path (e.g. "root/navigate_0/observe_1").
       Paths use {name}_{index} where index counts same-name siblings.
       This enables precise addressing in complex trees with repeated calls.
    
    4. expose is a RENDERING HINT, not a security policy.
       summarize(level=...) can override any node's expose setting.
       If you need real isolation, use @agentic_function(context="none").
    
    5. input/media/raw_reply are currently single fields (one LLM call per node).
       TODO: Change to llm_calls: list[LLMCall] to support multiple
       runtime.exec() calls within one function. This is a known limitation.

See also:
    - function.py: @agentic_function decorator
    - runtime.py: runtime.exec() — LLM call with auto recording
    - docs/context/README.md: visual diagrams of all query scenarios
"""

from __future__ import annotations

import time
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from contextvars import ContextVar

# The single global variable that tracks which Context node is "current".
# @agentic_function sets this on entry and resets on exit.
# This is what makes implicit call-stack tracking work without passing ctx.
_current_ctx: ContextVar[Optional["Context"]] = ContextVar("_current_ctx", default=None)


# ======================================================================
# ContextPolicy — controls what context gets injected into LLM calls
# ======================================================================
#
# The core problem: every function in the tree needs DIFFERENT context.
#   - Orchestrator: sees all children's results (big picture)
#   - Planner: sees recent observations + parent goal
#   - Worker (observe/act): sees last few siblings, not ancient history
#   - Leaf (OCR): sees nothing — pure computation
#
# Two additional concerns beyond "how much":
#   1. FRESHNESS: When called 20 times, old siblings become stale.
#      Recency decay controls this — more siblings → fewer visible.
#   2. CACHE STABILITY: For prompt caching ($0.50 vs $5.00/MTok),
#      the rendered text of old siblings must NEVER change.
#      Once rendered, it's frozen — even if decay would change the level.
#
# Design evolution:
#   v1: No policy — summarize() always showed everything. Expensive, noisy.
#   v2: Manual summarize() params per call-site. Flexible but tedious.
#   v3: ContextPolicy object — declare once on the decorator, auto-applied.
#       Presets for common patterns (ORCHESTRATOR, PLANNER, WORKER, LEAF).

@dataclass
class ContextPolicy:
    """
    Controls what context gets injected into LLM calls for a function.
    
    Attach to @agentic_function:
        @agentic_function(context_policy=WORKER)
        def observe(task): ...
    
    Or use directly:
        policy = ContextPolicy(depth=1, siblings=3)
        context_str = policy.apply(ctx)
    
    The policy answers THREE questions:
        1. How many ancestors to show? (depth)
        2. How many siblings to show? (siblings / decay)
        3. At what detail level? (level / progressive_detail)
    """
    
    # --- Ancestor visibility ---
    depth: int = -1
    # How many ancestor levels to include.
    # -1 = all ancestors from root to parent
    #  0 = none (fully isolated from parent chain)
    #  1 = parent only
    #  2 = parent + grandparent
    # Ancestors give the function "why am I being called?" context.
    
    # --- Sibling visibility ---
    siblings: int = -1
    # How many previous siblings to include (most recent first).
    # -1 = all siblings
    #  0 = none (each call is isolated)
    #  N = last N siblings
    # Overridden by decay when decay=True.
    
    # --- Detail level ---
    level: str = "summary"
    # Default expose level for rendering siblings.
    # trace / detail / summary / result / silent
    # Can be overridden per-node by progressive_detail.
    
    # --- Recency decay ---
    # When a function is called many times (e.g. observe in a 20-step loop),
    # old siblings become irrelevant. Decay automatically reduces visibility.
    #
    # How it works: thresholds are checked in order. The FIRST matching
    # threshold determines the window and level.
    #
    # Example with default thresholds:
    #   Call #1:  n_siblings=0  → <5,  show all at "detail"
    #   Call #5:  n_siblings=4  → <5,  show all at "detail"
    #   Call #6:  n_siblings=5  → <15, show last 3 at "summary"
    #   Call #20: n_siblings=19 → >=15, show last 1 at "result"
    decay: bool = False
    decay_thresholds: list = field(default_factory=lambda: [
        # (max_n_siblings, window, level)
        # Read as: "when there are fewer than N siblings, show WINDOW at LEVEL"
        (5,  -1, "detail"),    # Few siblings: show all, full detail
        (15,  3, "summary"),   # Medium: show last 3, one-line summaries
        # (implicit) >=15: show last 1, result only
    ])
    # The final fallback (when n_siblings exceeds all thresholds):
    decay_fallback_window: int = 1
    decay_fallback_level: str = "result"
    
    # --- Progressive detail ---
    # Even within the visible window, closer siblings get more detail.
    # This maximizes cache hit rate: old siblings keep their rendering,
    # only the newest ones get full detail.
    #
    # Format: list of (recency, level)
    #   recency = how many siblings ago (1 = most recent, 2 = second most recent)
    #   level = expose level for that sibling
    #
    # Example: [(1, "detail"), (3, "summary")]
    #   Most recent sibling → detail
    #   2nd and 3rd most recent → summary
    #   Older → use self.level as default
    progressive_detail: Optional[list] = None
    
    # --- Cache optimization ---
    cache_stable: bool = True
    # When True, a sibling's rendering is frozen the first time it appears.
    # Subsequent calls always see the same text, even if decay would change
    # the level. This preserves prompt cache prefixes.
    #
    # Without this: observe[5] rendered as "detail" in call 6, then changes
    # to "summary" in call 10 when decay kicks in → cache prefix broken!
    #
    # With this: observe[5] stays as "detail" forever. In call 10, it's
    # either still shown as "detail" (cache hit) or dropped entirely (decay).
    # The rendering itself never mutates.
    
    # --- Filtering ---
    include: Optional[list] = None   # Path whitelist (supports * wildcard)
    exclude: Optional[list] = None   # Path blacklist (supports * wildcard)
    branch: Optional[list] = None    # Show entire subtree of named nodes
    
    # --- Token budget ---
    max_tokens: Optional[int] = None  # Hard budget. Drops oldest siblings first.
    
    def apply(self, ctx: "Context") -> str:
        """
        Apply this policy to a Context node and return the context string.
        
        This is the main entry point. runtime.exec() calls this automatically
        when a context_policy is set on the function's decorator.
        
        The logic:
            1. Count siblings to determine decay window
            2. Resolve effective window + level
            3. Render siblings with progressive detail
            4. Respect cache stability (frozen renders)
            5. Apply token budget
            6. Prepend ancestor chain
        """
        if ctx.parent is None:
            return ""
        
        # --- Step 1: Determine effective window and level ---
        all_siblings = [c for c in ctx.parent.children
                        if c is not ctx and c.status != "running"]
        n = len(all_siblings)
        
        if self.decay:
            eff_window, eff_level = self._resolve_decay(n)
        else:
            eff_window = self.siblings
            eff_level = self.level
        
        # --- Step 2: Select visible siblings ---
        if eff_window == 0:
            visible = []
        elif eff_window == -1:
            visible = list(all_siblings)  # All
        else:
            visible = all_siblings[-eff_window:]  # Last N
        
        # --- Step 3: Apply include/exclude filters ---
        if self.include is not None:
            visible = [s for s in visible
                       if any(_path_matches(s.path, p) for p in self.include)]
        if self.exclude is not None:
            visible = [s for s in visible
                       if not any(_path_matches(s.path, p) for p in self.exclude)]
        
        # --- Step 4: Render each sibling ---
        sibling_parts = []
        for i, s in enumerate(visible):
            # Determine render level for this specific sibling
            render_level = self._resolve_sibling_level(s, i, len(visible), eff_level)
            
            if render_level == "silent":
                continue
            
            # Cache-stable rendering: use frozen render if available
            if self.cache_stable and s._cached_render is not None:
                rendered = s._cached_render
            else:
                rendered = s._render(render_level)
                if self.cache_stable:
                    s._cached_render = rendered
            
            # Branch mode: append children
            if self.branch and s.name in self.branch:
                rendered += "\n" + s._render_branch(render_level)
            
            sibling_parts.append(rendered)
        
        # --- Step 5: Apply token budget ---
        if self.max_tokens is not None:
            total = sum(len(s) for s in sibling_parts)
            while sibling_parts and total > self.max_tokens * 4:
                removed = sibling_parts.pop(0)
                total -= len(removed)
        
        # --- Step 6: Build ancestor chain ---
        parts = []
        if self.depth != 0 and ctx.parent and ctx.parent.name:
            ancestors = []
            node = ctx.parent
            while node and node.name:
                ancestors.append(node)
                node = node.parent
                if self.depth > 0 and len(ancestors) >= self.depth:
                    break
            for a in reversed(ancestors):
                if self.include and not any(_path_matches(a.path, p) for p in self.include):
                    continue
                if self.exclude and any(_path_matches(a.path, p) for p in self.exclude):
                    continue
                parts.append(f"[Ancestor: {a.name}({_fmt_params(a.params)})]")
        
        parts.extend(sibling_parts)
        return "\n".join(parts)
    
    def _resolve_decay(self, n_siblings: int) -> tuple:
        """
        Given the number of siblings, return (window, level) based on decay thresholds.
        
        Thresholds are checked in order. First match wins.
        If no threshold matches, use the fallback.
        """
        for max_n, window, level in self.decay_thresholds:
            if n_siblings < max_n:
                return (window, level)
        return (self.decay_fallback_window, self.decay_fallback_level)
    
    def _resolve_sibling_level(
        self, sibling: "Context", index_in_visible: int,
        total_visible: int, default_level: str,
    ) -> str:
        """
        Determine the render level for a specific sibling.
        
        If progressive_detail is set, closer siblings get more detail.
        Otherwise, use the default level.
        
        Args:
            sibling: The sibling Context node
            index_in_visible: Position in the visible list (0 = oldest visible)
            total_visible: Total number of visible siblings
            default_level: The level from decay resolution
        """
        if not self.progressive_detail:
            return default_level
        
        # recency = 1 means most recent, 2 means second most recent, etc.
        recency = total_visible - index_in_visible
        
        for threshold_recency, level in self.progressive_detail:
            if recency <= threshold_recency:
                return level
        
        return default_level


# --- Preset policies ---
# These cover the most common patterns. Users can customize or create their own.

ORCHESTRATOR = ContextPolicy(
    level="result",      # Only see return values, not details
    siblings=-1,         # See ALL children's results
    depth=0,             # Orchestrator IS the top — no ancestors needed
    cache_stable=True,
)
"""For top-level orchestrators (navigate, main_loop).
Sees all children's results but no details. Big picture only."""

PLANNER = ContextPolicy(
    level="summary",     # One-line summaries
    siblings=5,          # Last 5 siblings
    depth=1,             # Parent's goal
    cache_stable=True,
    progressive_detail=[
        (1, "detail"),   # Most recent sibling: full detail
        (3, "summary"),  # 2nd-3rd: summary
        # 4th-5th: default (summary)
    ],
)
"""For planning/reasoning functions.
Sees parent goal + recent history with progressive detail."""

WORKER = ContextPolicy(
    level="summary",
    depth=1,             # Parent's goal
    decay=True,          # Recency decay based on sibling count
    decay_thresholds=[
        (5,  -1, "detail"),    # <5 calls: see all, full detail
        (15,  3, "summary"),   # 5-14 calls: last 3, summary
    ],
    decay_fallback_window=1,
    decay_fallback_level="result",  # 15+ calls: only the most recent
    cache_stable=True,
)
"""For worker functions (observe, act) called repeatedly in loops.
Automatically reduces context as call count grows.
Saves tokens when iterating many steps."""

LEAF = ContextPolicy(
    level="result",
    depth=0,             # No ancestors
    siblings=0,          # No siblings
    cache_stable=False,  # Nothing to cache
)
"""For leaf computation (OCR, detection, parsing).
Zero context overhead — just do the task."""

FOCUSED = ContextPolicy(
    level="detail",
    siblings=1,          # Only the most recent sibling
    depth=1,             # Only parent
    cache_stable=True,
)
"""For functions that only need the immediately preceding result.
Common for act() after observe() — "I just saw X, now click it."""



@dataclass
class Context:
    """
    Execution record for one Agentic Function call.
    
    All fields are managed automatically by @agentic_function and runtime.exec().
    Users write normal Python — they don't need to know this class exists.
    
    The tree structure:
        - parent: who called me
        - children: who I called
        - path: auto-computed address like "root/navigate_0/observe_1"
    """

    # === Auto-managed by @agentic_function ===
    name: str = ""                              # Function name (from __name__)
    prompt: str = ""                            # Docstring = LLM prompt (from __doc__)
    params: dict = field(default_factory=dict)  # Call arguments (from *args/**kwargs)
    output: Any = None                          # Return value
    error: str = ""                             # Error message if failed
    status: str = "running"                     # "running" → "success" or "error"
    children: list = field(default_factory=list) # Child Context nodes (sub-calls)
    parent: Optional["Context"] = field(default=None, repr=False)  # Parent node
    start_time: float = 0.0
    end_time: float = 0.0
    expose: str = "summary"                     # Rendering hint for summarize()
    #   trace:   prompt + full I/O + raw LLM reply
    #   detail:  full input and output
    #   summary: one-line summary (default)
    #   result:  return value only
    #   silent:  don't appear in summaries

    # === Auto-managed by runtime.exec() ===
    # NOTE: Currently single fields — only records the LAST runtime.exec() call.
    # TODO: Replace with llm_calls: list[LLMCall] for multiple calls per function.
    input: Optional[dict] = None                # Data sent to LLM
    media: Optional[list] = None                # Media file paths (screenshots etc.)
    raw_reply: str = ""                         # LLM's raw response text

    # === Optional user override ===
    summary_fn: Optional[Callable] = field(default=None, repr=False)
    # Custom function to render this node's summary. Bypasses expose levels.

    # === Context policy (set by @agentic_function) ===
    _policy: Optional["ContextPolicy"] = field(default=None, repr=False)
    # The ContextPolicy attached to this function via @agentic_function.
    # runtime.exec() checks this: if set, uses policy.apply(ctx) instead
    # of the default summarize().

    # === Cache stability ===
    _cached_render: Optional[str] = field(default=None, repr=False)
    # Frozen rendering for prompt cache optimization.
    # Once set by ContextPolicy.apply(), this never changes.
    # This ensures the same text appears in subsequent calls,
    # preserving the prompt cache prefix ($0.50 vs $5.00/MTok).

    # ------------------------------------------------------------------
    # Path — auto-computed address for precise node selection
    # ------------------------------------------------------------------
    # Format: "root/navigate_0/observe_1/run_ocr_0"
    # The index counts same-name siblings (0-based).
    # Not stored — computed on access from parent/children relationships.

    @property
    def path(self) -> str:
        """Auto-computed path like 'root/navigate_0/observe_1'. Not stored."""
        if not self.parent:
            return self.name
        # Count how many siblings with my name appear before me
        idx = 0
        for c in self.parent.children:
            if c is self:
                break
            if c.name == self.name:
                idx += 1
        return f"{self.parent.path}/{self.name}_{idx}"

    # ------------------------------------------------------------------
    # summarize() — flexible tree query for LLM context injection
    # ------------------------------------------------------------------
    # The core idea: the tree records EVERYTHING. This method lets each
    # function choose exactly what slice of the tree to feed to its LLM.
    #
    # Design evolution:
    #   v1: sibling_summaries() — only showed immediate siblings. Too rigid.
    #   v2: renamed to summarize() — added parent info. Better but still limited.
    #   v3: added depth, siblings, include/exclude/branch — full tree query.
    #
    # The name "summarize" was chosen because the original "sibling_summaries"
    # was too abstract and didn't convey what it actually does.

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
        
        The tree is complete. This method selects what to show.
        
        Args:
            level:        Override all nodes' expose levels (e.g. "trace" to see everything)
            max_tokens:   Approximate token budget. Drops oldest siblings first.
            max_siblings: Shorthand for siblings=N (legacy compat)
            depth:        Ancestor visibility. -1=all, 0=none, 1=parent only, 2=grandparent...
            siblings:     Sibling visibility. -1=all, 0=none, N=last N siblings
            include:      Path whitelist. Only show matching nodes. Supports * wildcard.
                          e.g. ["root/navigate_0/observe_1", "root/navigate_0/observe_1/*"]
            exclude:      Path blacklist. Hide matching nodes. Supports * wildcard.
                          e.g. ["root/navigate_0/observe_0"]
            branch:       Show entire subtree under nodes with these names.
                          e.g. ["observe"] shows observe + all its children (run_ocr, detect_all)
        
        Returns:
            Multi-line string ready to inject into LLM prompt.
        
        Examples:
            ctx.summarize()                              # default: all ancestors + all siblings
            ctx.summarize(depth=1)                       # only parent + siblings
            ctx.summarize(depth=0, siblings=0)           # isolated: nothing
            ctx.summarize(include=["root/nav_0/obs_1"])  # only one specific node
            ctx.summarize(branch=["observe"])             # observe + its children
            ctx.summarize(siblings=1)                    # only the most recent sibling
        """
        # Resolve effective sibling count
        effective_siblings = max_siblings if max_siblings is not None else (
            None if siblings == -1 else siblings
        )

        parts = []

        # --- Ancestor chain ---
        if depth != 0 and self.parent and self.parent.name:
            ancestors = []
            node = self.parent
            while node and node.name:
                ancestors.append(node)
                node = node.parent
                if depth > 0 and len(ancestors) >= depth:
                    break
            # Show ancestors from root → parent order
            for a in reversed(ancestors):
                if not self._path_allowed(a, include, exclude):
                    continue
                parts.append(f"[Ancestor: {a.name}({_fmt_params(a.params)})]")

        # --- Previous siblings ---
        if self.parent and (effective_siblings is None or effective_siblings > 0):
            sibling_parts = []
            for c in self.parent.children:
                if c is self:
                    break  # Only look at siblings BEFORE me
                if c.status == "running":
                    continue
                if not self._path_allowed(c, include, exclude):
                    continue
                expose = level or c.expose
                if expose == "silent":
                    continue
                rendered = c._render(expose)
                # Branch mode: also show children of matching siblings
                if branch and c.name in branch:
                    rendered += "\n" + c._render_branch(level)
                sibling_parts.append(rendered)

            # Apply sibling limit (keep most recent)
            if effective_siblings is not None:
                sibling_parts = sibling_parts[-effective_siblings:]

            # Apply token budget (drop oldest first)
            if max_tokens is not None:
                total = sum(len(s) for s in sibling_parts)
                while sibling_parts and total > max_tokens * 4:  # rough chars→tokens
                    removed = sibling_parts.pop(0)
                    total -= len(removed)

            parts.extend(sibling_parts)

        # --- Path-based inclusion from anywhere in the tree ---
        if branch and include:
            root = self
            while root.parent:
                root = root.parent
            for path_pattern in include:
                for node in self._find_by_path(root, path_pattern):
                    if node is not self and node not in (self.parent.children if self.parent else []):
                        parts.append(node._render(level or node.expose))

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Internal: path filtering and rendering helpers
    # ------------------------------------------------------------------

    def _path_allowed(self, node: "Context", include: Optional[list], exclude: Optional[list]) -> bool:
        """Check if a node passes the include/exclude path filters."""
        if include is not None:
            return any(_path_matches(node.path, pattern) for pattern in include)
        if exclude is not None:
            return not any(_path_matches(node.path, pattern) for pattern in exclude)
        return True

    def _render_branch(self, level: Optional[str], indent: int = 1) -> str:
        """Recursively render all children of this node."""
        lines = []
        for c in self.children:
            expose = level or c.expose
            if expose != "silent":
                prefix = "  " * indent
                lines.append(f"{prefix}{c._render(expose)}")
                if c.children:
                    lines.append(c._render_branch(level, indent + 1))
        return "\n".join(lines)

    @staticmethod
    def _find_by_path(root: "Context", pattern: str) -> list:
        """Find all nodes in the tree matching a path pattern."""
        results = []
        stack = [root]
        while stack:
            node = stack.pop()
            if _path_matches(node.path, pattern):
                results.append(node)
            stack.extend(node.children)
        return results

    def _render(self, level: str) -> str:
        """
        Render this single node at the given expose level.
        
        Levels (from most verbose to least):
            trace:   prompt + input + media + raw_reply + output + error
            detail:  name(params) → status | input | output
            summary: name: output (one line)
            result:  just the output value
            silent:  empty string (shouldn't be called)
        """
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
    # Tree visualization
    # ------------------------------------------------------------------

    @property
    def duration_ms(self) -> float:
        """Execution duration in milliseconds."""
        if self.end_time and self.start_time:
            return (self.end_time - self.start_time) * 1000
        return 0.0

    def tree(self, indent: int = 0) -> str:
        """
        Human-readable tree view of the entire execution.
        
        Example output:
            root …
              navigate ✓ 3200ms → {success: True}
                observe ✓ 1200ms → {found: True}
                  run_ocr ✓ 50ms → {texts: [...]}
                act ✓ 820ms → {clicked: True}
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
        Agentic Traceback — like Python's traceback but for agentic calls.
        
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
        """
        Save the Context tree to a file.
        
        .md  → human-readable tree view
        .jsonl → machine-readable (one JSON record per node)
        """
        if path.endswith(".md"):
            with open(path, "w") as f:
                f.write(self.tree())
        else:
            with open(path, "w") as f:
                for record in self._to_records():
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def _to_records(self, depth: int = 0) -> list[dict]:
        """Flatten the tree into a list of records for JSONL serialization."""
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
            "expose": self.expose,
            "duration_ms": self.duration_ms,
        }]
        for c in self.children:
            records.extend(c._to_records(depth + 1))
        return records


# ======================================================================
# Module-level functions
# ======================================================================

def get_context() -> Optional[Context]:
    """Get the current Context node (inside an @agentic_function).
    
    Returns None if called outside any @agentic_function.
    Most users don't need this — runtime.exec() uses it internally.
    """
    return _current_ctx.get(None)


def get_root_context() -> Optional[Context]:
    """Get the root of the Context tree.
    
    Walks up from the current node to find the root.
    Returns None if no Context exists.
    """
    ctx = _current_ctx.get(None)
    if ctx is None:
        return None
    while ctx.parent is not None:
        ctx = ctx.parent
    return ctx


def init_root(name: str = "root") -> Context:
    """Manually initialize a root Context.
    
    Usually not needed — @agentic_function(context="auto") creates one automatically.
    Use this if you want to control the root explicitly.
    """
    root = Context(name=name, start_time=time.time(), status="running")
    _current_ctx.set(root)
    return root


# ======================================================================
# Internal helpers
# ======================================================================

def _path_matches(path: str, pattern: str) -> bool:
    """
    Match a context path against a pattern.
    
    Supports:
        "root/navigate_0/observe_1"      — exact match
        "root/navigate_0/*"              — everything under navigate_0
        "root/*/observe_*"               — fnmatch wildcards
    """
    if pattern.endswith("/*"):
        prefix = pattern[:-2]
        return path.startswith(prefix + "/") or path == prefix
    if "*" in pattern:
        import fnmatch
        return fnmatch.fnmatch(path, pattern)
    return path == pattern


def _fmt_params(params: dict) -> str:
    """Format function params for display. Truncates long values."""
    if not params:
        return ""
    parts = []
    for k, v in params.items():
        v_str = json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v, str) else f'"{v}"'
        if len(v_str) > 50:
            v_str = v_str[:47] + "..."
        parts.append(f"{k}={v_str}")
    return ", ".join(parts)
