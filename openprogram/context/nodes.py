"""Flat DAG context model.

Every node is a ``Call`` — one recorded event where some role produced
something. The data structure is the same regardless of who acted:

    role="user"   ─ a user-typed message      output = the text
    role="llm"    ─ one LLM call              input  = prompt info
                                              output = reply
                                              reads  = context node ids
    role="code"   ─ one function invocation   input  = arguments
                                              output = result

Edges (dag/overview.md §3 — two edges, never conflated):
  - ``predecessor``  conversation chain: who I follow in chat order.
                     Single inbound per node. The session's first node
                     and explicit root forks carry the ``"ROOT"``
                     sentinel; spawn branch roots carry None.
  - ``caller``       sub-call nesting: who invoked me to execute.
                     ``"ROOT"`` or ``""`` on chain-level turns; only a
                     node inside an @agentic_function's execution
                     subtree names another call.
  - ``reads``        context edges. For LLM calls: the prior nodes
                     whose content shaped this prompt. Stored as a
                     list of ids on the node itself; not derived.

Time order is ``seq`` alone — never an edge. No tree, no containers:
nesting is expressed by ``caller``, chat order by ``predecessor``.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# Role constants


ROLE_USER = "user"
ROLE_LLM = "llm"
ROLE_CODE = "code"

VALID_ROLES = {ROLE_USER, ROLE_LLM, ROLE_CODE}


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# Call: the one and only node type


@dataclass
class Call:
    """One DAG node. Uniform across user input / LLM call / code call.

    Three things to know about a Call:

      WHO did it          ─ role + name
      WHAT they did       ─ input + output
      WHERE it fits       ─ seq (time order) + caller (caller)
                            + reads (context references)

    Fields:
      id:           unique node id
      created_at:   wall-clock seconds (human-readable; do NOT use for
                    sort — same-millisecond appends would tie)
      seq:          monotonically increasing integer, assigned at
                    append-time. -1 until stored. This is the
                    canonical time ordering — sort nodes by seq.

      role:         "user" | "llm" | "code"
      name:         specific actor — model id / function name / username
      input:        what was given to this actor — prompt blocks /
                    arguments dict / question text / None
      output:       what the actor produced — reply text / return value /
                    answer / None

      caller: id of the Call that invoked me. Empty string at the
                    very root. (DAG edge: caller → callee)
      reads:        ids of nodes whose content went into this call's
                    prompt. [] when not applicable. (DAG edge: context)

      metadata:     freeform passthrough for adapter-only fields
                    (source channel, attachments manifest, expose
                    setting, duration, error status, …)
    """

    id: str = field(default_factory=_new_id)
    created_at: float = field(default_factory=time.time)
    seq: int = -1   # assigned by Graph.add() / GraphStore.append()

    role: str = ""
    name: str = ""
    input: Any = None
    output: Any = None

    caller: str = ""
    # Conversation-chain parent (dag/overview.md). Top-level
    # schema field, the ONLY place the conv edge lives. ``None`` on the
    # session's first node and on spawn branch roots.
    predecessor: Optional[str] = None
    reads: list[str] = field(default_factory=list)

    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    # Convenience role checks — concise call sites.
    def is_user(self) -> bool:
        return self.role == ROLE_USER

    def is_llm(self) -> bool:
        return self.role == ROLE_LLM

    def is_code(self) -> bool:
        return self.role == ROLE_CODE

    # Backward-compat property accessors
    # Old code references UserMessage.content / ModelCall.model /
    # ModelCall.system_prompt / FunctionCall.function_name /
    # FunctionCall.arguments / FunctionCall.result. These map onto
    # unified Call fields. New code should use ``output`` / ``name`` /
    # ``input`` directly.

    @property
    def content(self) -> Any:
        """Legacy UserMessage.content — same as ``output``."""
        return self.output

    @property
    def model(self) -> str:
        """Legacy ModelCall.model — same as ``name``."""
        return self.name

    @property
    def system_prompt(self) -> Optional[str]:
        """Legacy ModelCall.system_prompt — pulled from input.system."""
        if isinstance(self.input, dict):
            v = self.input.get("system")
            return v if v else None
        return None

    @property
    def function_name(self) -> str:
        """Legacy FunctionCall.function_name — same as ``name``."""
        return self.name

    @property
    def arguments(self) -> dict:
        """Legacy FunctionCall.arguments — same as ``input`` (defaults to {})."""
        return self.input if isinstance(self.input, dict) else {}

    @property
    def result(self) -> Any:
        """Legacy FunctionCall.result — same as ``output``."""
        return self.output


# Node is an alias for backward import compatibility.
Node = Call


# Backward-compat factory functions
#
# Old code says ``UserMessage(content="...")`` / ``ModelCall(model=...)`` /
# ``FunctionCall(function_name=...)``. These wrappers return a Call so
# existing call sites keep working. ``x.is_user()`` etc.
# is intentionally NOT supported — use ``x.is_user()`` / ``x.is_llm()`` /
# ``x.is_code()`` for role checks.


def UserMessage(content: str = "", **kwargs) -> Call:
    """Construct a user-role Call. Backward-compat shim."""
    return Call(role=ROLE_USER, output=content, **kwargs)


def ModelCall(
    *,
    model: str = "",
    reads: Optional[list[str]] = None,
    output: Any = None,
    system_prompt: Optional[str] = None,
    **kwargs,
) -> Call:
    """Construct an llm-role Call. Backward-compat shim."""
    inp = {"system": system_prompt} if system_prompt else None
    return Call(
        role=ROLE_LLM,
        name=model,
        input=inp,
        output=output,
        reads=list(reads or []),
        **kwargs,
    )


def FunctionCall(
    *,
    function_name: str = "",
    arguments: Optional[dict] = None,
    result: Any = None,
    caller: str = "",
    **kwargs,
) -> Call:
    """Construct a code-role Call. Backward-compat shim."""
    return Call(
        role=ROLE_CODE,
        name=function_name,
        input=arguments or {},
        output=result,
        caller=caller,
        **kwargs,
    )


# Graph container


class Graph:
    """In-memory store. Append-only. Existing nodes never mutate."""

    def __init__(self):
        self.nodes: dict[str, Call] = {}
        self._next_seq: int = 0

    def add(self, node: Call) -> Call:
        """Append ``node`` to the graph. Assigns ``node.seq`` if it
        hasn't been set (seq < 0). Raises if the id is already present."""
        if node.id in self.nodes:
            raise ValueError(f"Node id {node.id!r} already in graph")
        if node.seq < 0:
            node.seq = self._next_seq
            self._next_seq += 1
        else:
            self._next_seq = max(self._next_seq, node.seq + 1)
        self.nodes[node.id] = node
        return node

    def update(self, node_id: str, **fields: Any) -> Call:
        """In-place update of an existing node (used at @agentic_function
        exit to fill ``output`` / status into the placeholder appended
        at entry). DAG-purists: this is intentional — function-call
        nodes are append-on-entry / fill-on-exit to support real-time
        observation; everything else is append-only."""
        if node_id not in self.nodes:
            raise KeyError(f"Node id {node_id!r} not in graph")
        node = self.nodes[node_id]
        for k, v in fields.items():
            if k == "metadata" and isinstance(v, dict):
                node.metadata = {**(node.metadata or {}), **v}
            else:
                setattr(node, k, v)
        return node

    @property
    def _last_id(self) -> Optional[str]:
        """Highest-seq node id, or None if graph is empty.
        Kept for backward compat — new code should sort by seq directly."""
        if not self.nodes:
            return None
        return max(self.nodes.values(), key=lambda n: n.seq).id

    # Convenience builders — all return Call.

    def add_user_message(self, content: str) -> Call:
        return self.add(Call(role=ROLE_USER, output=content))

    def add_model_call(
        self,
        *,
        model: str,
        reads: list[str],
        system_prompt: Optional[str] = None,
        output: Optional[str] = None,
        caller: str = "",
    ) -> Call:
        unknown = [r for r in reads if r not in self.nodes]
        if unknown:
            raise ValueError(f"ModelCall.reads contains unknown ids: {unknown}")
        node = Call(
            role=ROLE_LLM,
            name=model,
            input={"system": system_prompt} if system_prompt else None,
            output=output,
            reads=list(reads),
            caller=caller,
        )
        return self.add(node)

    def add_function_call(
        self,
        *,
        function_name: str,
        arguments: dict,
        caller: str,
        result: Any = None,
    ) -> Call:
        # ``caller`` may reference a node id that doesn't yet exist
        # (parent @agentic_function whose own node is appended after
        # its children). We don't enforce presence — read-side code does
        # the resolution.
        node = Call(
            role=ROLE_CODE,
            name=function_name,
            input=arguments,
            output=result,
            caller=caller,
        )
        return self.add(node)

    # --- Lookups ---------------------------------------------------

    def __getitem__(self, node_id: str) -> Call:
        return self.nodes[node_id]

    def __contains__(self, node_id: str) -> bool:
        return node_id in self.nodes

    def __len__(self) -> int:
        return len(self.nodes)

    def last(self) -> Optional[Call]:
        """Most recently appended node, by seq."""
        if not self.nodes:
            return None
        return max(self.nodes.values(), key=lambda n: n.seq)

    def __iter__(self):
        """Iterate in seq order (oldest first). Note: insertion order
        may differ from seq order if nodes are added out of sequence
        (e.g. loaded from storage)."""
        return iter(sorted(self.nodes.values(), key=lambda n: n.seq))

    # --- Serialization ---------------------------------------------

    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self],
            "next_seq": self._next_seq,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent,
                          ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, data: dict) -> "Graph":
        g = cls()
        for raw in data.get("nodes", []):
            raw.pop("type", None)
            n = Call(**raw)
            g.nodes[n.id] = n
            if n.seq >= g._next_seq:
                g._next_seq = n.seq + 1
        if "next_seq" in data:
            g._next_seq = max(g._next_seq, data["next_seq"])
        return g


# Helpers (operate purely on a Graph)


def last_user_message(graph: Graph) -> Optional[Call]:
    """Most recent user-role Call, or None."""
    for n in reversed(list(graph)):
        if n.is_user():
            return n
    return None


def linear_back_to(graph: Graph, target_id: str) -> list[str]:
    """All nodes with seq >= target.seq, in seq order (oldest first).
    Inclusive at both ends. Raises if target not in graph.
    """
    if target_id not in graph:
        raise ValueError(f"target {target_id!r} not in graph")
    target_seq = graph[target_id].seq
    return [n.id for n in graph if n.seq >= target_seq]


def spawn_job(function_call_id: str, graph: Graph) -> list[str]:
    """For a spawn-style code Call, extract the referenced task node id
    out of its ``input`` (legacy ``arguments``). Used by sub-agent ModelCalls.
    """
    node = graph[function_call_id]
    if not node.is_code():
        raise TypeError(f"{function_call_id!r} is not a code Call")
    args = node.input or {}
    task = args.get("task") if isinstance(args, dict) else None
    if isinstance(task, str) and task in graph:
        return [task]
    if isinstance(task, dict) and task.get("node_id") in graph:
        return [task["node_id"]]
    return []


def branch_terminals(spawn_function_call_id: str, graph: Graph) -> list[str]:
    """Walk the caller tree under ``spawn_function_call_id``,
    returning the terminal (deepest, latest-seq) descendant for each
    direct child branch.

    A "child branch" starts with a node whose ``caller`` points
    at the spawn call. The terminal is found by following caller
    children further down (max seq at each level).
    """
    direct_children = [
        n.id for n in graph if n.caller == spawn_function_call_id
    ]
    out: list[str] = []
    for child in direct_children:
        cur = child
        while True:
            descendants = [
                n.id for n in graph if n.caller == cur
            ]
            if not descendants:
                break
            cur = max(descendants, key=lambda nid: graph[nid].seq)
        out.append(cur)
    return out


def branch_internal(
    spawn_function_call_id: str,
    terminal_id: str,
    graph: Graph,
) -> list[str]:
    """All nodes in the caller lineage from a spawn code Call
    down to ``terminal_id``, in seq order (oldest first), inclusive
    of both endpoints.
    """
    out: list[str] = []
    cur: Optional[str] = terminal_id
    seen: set[str] = set()
    while cur is not None and cur in graph:
        if cur in seen:
            break
        seen.add(cur)
        out.append(cur)
        if cur == spawn_function_call_id:
            break
        cur = graph[cur].caller or None
    if not out or out[-1] != spawn_function_call_id:
        raise ValueError(
            f"{terminal_id!r} is not in a caller lineage rooted at "
            f"{spawn_function_call_id!r}"
        )
    out.reverse()
    return out


def fold_history(current_node_id: str, graph: Graph) -> list[str]:
    """Fold prior turns into (opening user-Call, closing llm-Call) pairs,
    and include every node from the current turn through current_node_id.
    """
    if current_node_id not in graph:
        raise ValueError(f"{current_node_id!r} not in graph")

    # Find the user-Call that opened the current turn: the user-role
    # node with the largest seq that's still <= current.seq.
    current_seq = graph[current_node_id].seq
    current_turn_id: Optional[str] = None
    for n in graph:
        if n.seq > current_seq:
            break
        if n.is_user():
            current_turn_id = n.id
    if current_turn_id is None:
        # No user msg before current — treat current as the start.
        current_turn_id = current_node_id

    turns: list[list[str]] = []
    current: list[str] = []
    for n in graph:
        if n.is_user():
            if current:
                turns.append(current)
            current = [n.id]
        else:
            current.append(n.id)
        if n.id == current_node_id:
            cutoff = current.index(current_node_id) + 1
            current = current[:cutoff]
            break
    if current:
        turns.append(current)

    out: list[str] = []
    for bucket in turns:
        if current_turn_id in bucket:
            out.extend(bucket)
            break
        user_id = bucket[0]
        final_llm = None
        for nid in reversed(bucket):
            if graph[nid].is_llm():
                final_llm = nid
                break
        out.append(user_id)
        if final_llm is not None:
            out.append(final_llm)
    return out


def _is_root(graph: Graph, node_id: str) -> bool:
    """The session ROOT node (§2): ``metadata.display == "root"``."""
    n = graph.nodes.get(node_id)
    return bool(n is not None and (n.metadata or {}).get("display") == "root")


def render_spine(graph: Graph, head_id: str) -> list[str]:
    """The predecessor chain from ``head_id`` back to its branch
    terminus, oldest first (dag/overview.md §3 read invariant).

    Pure edge walk: no caller fallback, no seq stitching. A node whose
    ``predecessor`` is missing terminates the walk — spawn roots
    (``predecessor=None``, §4) therefore stop the spine at the spawn
    root and never leak into the parent branch via ``caller``. The
    ``"ROOT"`` sentinel (session first node / explicit root fork) is not
    a real node id and terminates too.
    """
    out: list[str] = []
    seen: set[str] = set()
    cur: Optional[str] = head_id
    while cur and cur in graph.nodes and cur not in seen:
        seen.add(cur)
        out.append(cur)
        cur = graph.nodes[cur].predecessor
    out.reverse()
    return out


def render_path(graph: Graph, head_id: str) -> list[str]:
    """Every node admitted by the §6 membership rule, in seq order.

    > A node is rendered iff its nearest ROOT-level ancestor (walking
    > ``caller`` edges upward) lies on the predecessor chain of
    > ``head_id``.

    Implemented as the mirror image: take the spine (:func:`render_spine`)
    and expand each spine node's caller-subtree. Same set, one pass, and
    it never has to walk a node that will be discarded. ``seq`` plays no
    part in the selection — only in the final ordering.

    ROOT is the one node whose caller-subtree is NOT expanded. Every
    top-level conversational node carries ``caller="ROOT"`` (§3: caller
    makes top-level nodes converge onto ROOT so the graph stays
    connected), so expanding ROOT would re-admit every sibling branch in
    the session and dissolve branch isolation. A ROOT-level node is its
    own nearest ROOT-level ancestor; ROOT itself is never one.

    Spawn-branch roots (``metadata.spawn_branch_root``) are likewise not
    entered via the ``caller`` edge: a spawn branch is its own
    conversation whose result flows back through the return value /
    attach pointer, so its internals never render into the spawning
    branch's context. Without this, a Goal working agent could read the
    judge's spawned instructions and verdicts from its own DAG history.
    The exclusion is directional — rendering *from* a head inside the
    spawn branch still sees the branch itself via the spine.
    """
    spine = render_spine(graph, head_id)
    if not spine:
        return []
    children: dict[str, list[str]] = {}
    for n in graph.nodes.values():
        if n.caller:
            children.setdefault(n.caller, []).append(n.id)

    members: set[str] = set(spine)
    stack = list(spine)
    while stack:
        nid = stack.pop()
        if not _is_root(graph, nid):
            for cid in children.get(nid, ()):
                if cid in members:
                    continue
                child = graph.nodes.get(cid)
                if child is not None and (child.metadata or {}).get(
                        "spawn_branch_root"):
                    continue
                members.add(cid)
                stack.append(cid)
    return [n.id for n in graph if n.id in members]


def summary_covers_ids(node: "Call") -> Optional[list[str]]:
    """The chain-node ids a summary replaces, or None for non-summaries.

    Written by the compaction persister as ``metadata.covers_ids`` —
    ids, not a seq interval, because seqs of sibling branches interleave
    (context/compaction.md §2).
    """
    raw = (node.metadata or {}).get("covers_ids")
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    return [str(x) for x in raw]


def active_summary(graph: Graph) -> Optional["Call"]:
    """The one summary rendering consults — the newest, by seq.

    Compaction is a rolling summary (context/compaction.md §1): each new
    summary absorbs the previous one, so older summary nodes are inert
    relics and never elide anything.
    """
    cands = [n for n in graph.nodes.values()
             if summary_covers_ids(n) is not None]
    if not cands:
        return None
    return max(cands, key=lambda n: n.seq)


def render_context(
    graph: Graph,
    *,
    head_id: Optional[str] = None,
    head_seq: Optional[int] = None,
    frame_entry_seq: int = -1,
    render_range: Optional[dict] = None,
) -> list[str]:
    """Pick the ids that go into the next LLM call's ``reads``.

    Pure function over a Graph + a few parameters. No ContextVar
    access, no disk writes, no mutation of ``graph`` — the caller
    decides what "current frame" means.

    Membership is path-native (dag/overview.md §6): a node enters the
    rendering iff its nearest ROOT-level ancestor along ``caller`` lies
    on ``head_id``'s predecessor chain, and the frame/expose rules
    admit it. Branch isolation is therefore a property of the walk;
    callers do no post-hoc set filtering.

    Args:
        graph:            the DAG to read from.
        head_id:          the branch tip. Its predecessor chain is the
                          spine; only spine nodes and their
                          caller-subtrees are candidates. Defaults to
                          the highest-seq node (``head_seq`` is applied
                          first, so the default head is the newest node
                          at or below the cap).
        head_seq:         ordering cap — drop candidates with
                          seq > head_seq. Not a selector; membership
                          comes from ``head_id``.
        frame_entry_seq:  seq value when the current ``@agentic_function``
                          started. Nodes with seq > frame_entry_seq are
                          "in-frame" (the function's own sub-calls);
                          nodes with seq <= frame_entry_seq are
                          "pre-frame". Use -1 (the default) for
                          top-level chat — that frame simply has no
                          pre-frame, and all nodes are in-frame.
        render_range:     ``{"callers": int, "subcalls": int}`` limits.
                          ``callers`` — caps pre-frame nodes (history
                            before this frame started); ``None``
                            (default) uncapped, ``0`` walls off prior
                            context, ``N`` keeps the last N nodes by
                            seq.
                          ``subcalls`` — caps in-frame nodes (what
                            happened since the frame started); ``-1``
                            (default) uncapped — the frame naturally
                            sees its own progress. Trimming a
                            sub-function's internals is done by that
                            sub-function's ``expose`` setting, not by
                            subcalls counting. Set ``N>=0`` only to
                            actively bound prompt size in a long loop.

    Visibility filtering (per-function ``metadata.expose``):
        ``io``   (default) — drop the function's direct llm Calls;
                  the caller sees only its input/output.
        ``llm``  — drop the function's own node and its direct code
                  sub-calls; the caller sees only its llm exchanges.
        ``full`` — drop nothing; internals fully visible.
        ``hidden`` — the function writes no node at all (the decorator
                  enforces this; nothing to filter here).

    Returns:
        Node ids in seq order (oldest first), ready to be the
        ``reads`` of the next LLM call.
    """
    if head_seq is None:
        head_seq = max((n.seq for n in graph.nodes.values()), default=-1)
    if head_seq < 0:
        return []
    if head_id is None:
        # Default head: the newest ROOT-level node at or below the cap
        # — i.e. the top-level chain tip. A sub-called node (caller set)
        # is never a branch tip: its predecessor is None, so it would
        # yield a one-node spine and drop the whole conversation. A
        # caller that knows its real branch tip passes head_id; this
        # fallback only serves single-branch graphs.
        candidates = [
            n for n in graph.nodes.values()
            if n.seq <= head_seq and not n.caller
        ]
        if not candidates:
            return []
        head_id = max(candidates, key=lambda n: n.seq).id
    if head_id not in graph.nodes:
        return []

    # callers_cap → caps pre-frame (history before the frame started).
    #   None = uncapped (the conversation stays fully visible).
    # subcalls_cap → caps in-frame (what happened since the frame
    #   started — earlier exec calls and direct sub-functions).
    #   DEFAULT -1 (uncapped). A frame naturally sees everything that
    #   happened inside it so far. Trimming a sub-function's internals
    #   is the job of that sub-function's ``expose`` setting, not of
    #   subcalls counting. Set N>=0 to actively trim to the N most
    #   recent in-frame nodes (e.g. to bound prompt size in a long loop).
    callers_cap: Optional[int] = None
    subcalls_cap: int = -1
    if isinstance(render_range, dict):
        cv = render_range.get("callers")
        if cv is not None:
            callers_cap = int(cv)
        sv = render_range.get("subcalls")
        if sv is not None:
            subcalls_cap = int(sv)

    # TODO(render_range): today render_range only expresses *distance*
    # — callers (how far up the conversation) and subcalls (how far
    # into the current frame). It cannot pin SPECIFIC nodes. A planned
    # but unimplemented extension: select particular functions/nodes by
    # name or position and force them into the prompt regardless of
    # distance, e.g. render_range={"pin": ["plan_next_action", ...]}
    # or an explicit node-id selector on runtime.exec. Until then a
    # function that needs a specific earlier result must thread it in
    # by hand via runtime.exec(content=[...]).

    # §6 membership: spine of head_id + each spine node's caller-subtree.
    # ``head_seq`` only trims the tail for ordering; it selects nothing.
    visible = [
        graph.nodes[nid] for nid in render_path(graph, head_id)
        if graph.nodes[nid].seq <= head_seq
    ]
    in_frame = [n for n in visible if n.seq > frame_entry_seq]
    pre_frame = [n for n in visible if n.seq <= frame_entry_seq]

    # callers_cap: keep the most-recent ``callers_cap`` pre-frame nodes.
    if callers_cap is not None:
        if callers_cap <= 0:
            pre_frame = []
        else:
            pre_frame = pre_frame[-callers_cap:]

    chain = pre_frame + in_frame

    # Expose filtering — how much of a function the caller's context
    # sees, set per-function via ``metadata.expose``:
    #   io   (default)  the function's own input/output; its internal
    #                   llm exchanges are hidden.
    #   llm             the function's llm exchanges; its own
    #                   input/output node and its nested code
    #                   sub-calls are hidden.
    #   full            everything — input/output AND llm exchanges.
    #   hidden          the function writes no node at all (enforced
    #                   by the decorator, not here).
    io_owners: set[str] = set()
    llm_owners: set[str] = set()
    try:
        from openprogram.agentic_programming.function import default_expose
        _fallback = default_expose()
    except Exception:
        _fallback = "io"
    for n in chain:
        if n.is_code():
            ex = (n.metadata or {}).get("expose") or _fallback
            if ex == "io":
                io_owners.add(n.id)
            elif ex == "llm":
                llm_owners.add(n.id)

    # Compaction — segment substitution (context/compaction.md §3): if
    # the active summary's covered segment lies fully on this chain,
    # drop the segment (plus the caller subtrees hanging off it) and
    # admit the summary node at the segment's position. A chain that
    # does not contain the whole segment — a fork from inside the
    # covered range, a dead sibling of the same era — renders raw: its
    # context was never compacted.
    summary = active_summary(graph)
    elided: set[str] = set()
    splice_before: Optional[str] = None
    if summary is not None:
        seg = summary_covers_ids(summary) or []
        chain_ids = {n.id for n in chain}
        if seg and summary.id not in chain_ids \
                and all(sid in chain_ids for sid in seg):
            elided = set(seg)
            kids: dict[str, list[str]] = {}
            for n in chain:
                if n.caller:
                    kids.setdefault(n.caller, []).append(n.id)
            stack = list(seg)
            while stack:
                for cid in kids.get(stack.pop(), ()):
                    if cid not in elided:
                        elided.add(cid)
                        stack.append(cid)
            splice_before = seg[0]

    kept = []
    for n in chain:
        if splice_before is not None and n.id == splice_before:
            kept.append(summary)
        if n.id in elided:
            continue
        # io function: hide its internal llm exchanges.
        if n.is_llm() and n.caller in io_owners:
            continue
        # llm function: hide its own input/output node and its nested
        # code sub-calls — only its llm exchanges survive.
        if n.id in llm_owners or (n.is_code() and n.caller in llm_owners):
            continue
        kept.append(n)

    # subcalls_cap: keep at most N in-frame nodes (most recent).
    # -1 (default) means uncapped — skip the trim entirely. Top-level
    # chat (frame_entry_seq == -1) is just a frame with no pre-frame;
    # it goes through the same path with no special-casing.
    if subcalls_cap >= 0:
        in_frame_ids = {n.id for n in in_frame}
        in_frame_kept = 0
        final: list = []
        for n in reversed(kept):
            if n.id in in_frame_ids:
                in_frame_kept += 1
                if in_frame_kept > subcalls_cap:
                    continue
            final.append(n)
        kept = list(reversed(final))

    return [n.id for n in kept]


__all__ = [
    "Call",
    "Node",
    "Graph",
    "ROLE_USER",
    "ROLE_LLM",
    "ROLE_CODE",
    "VALID_ROLES",
    # Backward-compat factory shims (return Call):
    "UserMessage",
    "ModelCall",
    "FunctionCall",
    # Helpers:
    "last_user_message",
    "linear_back_to",
    "spawn_job",
    "branch_terminals",
    "branch_internal",
    "fold_history",
    "summary_covers_ids",
    "active_summary",
    "render_spine",
    "render_path",
    "render_context",
]
