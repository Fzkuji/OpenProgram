"""Flat DAG context model.

Every node is a ``Call`` — one recorded event where some role produced
something. The data structure is the same regardless of who acted:

    role="user"   ─ a user-typed message      output = the text
    role="llm"    ─ one LLM call              input  = prompt info
                                              output = reply
                                              reads  = context node ids
    role="code"   ─ one function invocation   input  = arguments
                                              output = result

Edges:
  - ``predecessor``  time order. Single inbound per node. None on the
                     very first node of a session.
  - ``reads``        context edges. For LLM calls: the prior nodes
                     whose content shaped this prompt. Stored as a
                     list of ids on the node itself; not derived.

No tree, no parent/child, no containers. Nesting is just a temporal
sequence with role transitions (user→llm→code→llm→…).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# ── Role constants ─────────────────────────────────────────────────


ROLE_USER = "user"
ROLE_LLM = "llm"
ROLE_CODE = "code"

VALID_ROLES = {ROLE_USER, ROLE_LLM, ROLE_CODE}


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# ── Call: the one and only node type ──────────────────────────────


@dataclass
class Call:
    """One DAG node. Uniform across user input / LLM call / code call.

    Three things to know about a Call:

      WHO did it          ─ role + name
      WHAT they did       ─ input + output
      WHERE it fits       ─ seq (time order) + called_by (caller)
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

      called_by: id of the Call that invoked me. Empty string at the
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

    called_by: str = ""
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

    # ── Backward-compat property accessors ──
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


# ── Backward-compat factory functions ────────────────────────────────
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
    called_by: str = "",
    **kwargs,
) -> Call:
    """Construct a code-role Call. Backward-compat shim."""
    return Call(
        role=ROLE_CODE,
        name=function_name,
        input=arguments or {},
        output=result,
        called_by=called_by,
        **kwargs,
    )


# ── Graph container ─────────────────────────────────────────────────


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
        called_by: str = "",
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
            called_by=called_by,
        )
        return self.add(node)

    def add_function_call(
        self,
        *,
        function_name: str,
        arguments: dict,
        called_by: str,
        result: Any = None,
    ) -> Call:
        # ``called_by`` may reference a node id that doesn't yet exist
        # (parent @agentic_function whose own node is appended after
        # its children). We don't enforce presence — read-side code does
        # the resolution.
        node = Call(
            role=ROLE_CODE,
            name=function_name,
            input=arguments,
            output=result,
            called_by=called_by,
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
            raw.pop("predecessor", None)   # silently drop legacy field
            n = Call(**raw)
            g.nodes[n.id] = n
            if n.seq >= g._next_seq:
                g._next_seq = n.seq + 1
        if "next_seq" in data:
            g._next_seq = max(g._next_seq, data["next_seq"])
        return g


# ── Helpers (operate purely on a Graph) ────────────────────────────


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


def spawn_task(function_call_id: str, graph: Graph) -> list[str]:
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
    """Walk the called_by tree under ``spawn_function_call_id``,
    returning the terminal (deepest, latest-seq) descendant for each
    direct child branch.

    A "child branch" starts with a node whose ``called_by`` points
    at the spawn call. The terminal is found by following called_by
    children further down (max seq at each level).
    """
    direct_children = [
        n.id for n in graph if n.called_by == spawn_function_call_id
    ]
    out: list[str] = []
    for child in direct_children:
        cur = child
        while True:
            descendants = [
                n.id for n in graph if n.called_by == cur
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
    """All nodes in the called_by lineage from a spawn code Call
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
        cur = graph[cur].called_by or None
    if not out or out[-1] != spawn_function_call_id:
        raise ValueError(
            f"{terminal_id!r} is not in a called_by lineage rooted at "
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


def compute_reads(
    graph: Graph,
    *,
    head_seq: Optional[int] = None,
    frame_entry_seq: int = -1,
    render_range: Optional[dict] = None,
) -> list[str]:
    """Pick the ids that go into the next LLM call's ``reads``.

    Pure function over a Graph + a few parameters. No ContextVar
    access — the caller decides what "current frame" means.

    Args:
        graph:            the DAG to read from.
        head_seq:         only consider nodes with seq <= head_seq.
                          Defaults to the graph's current max seq.
        frame_entry_seq:  seq value when the current ``@agentic_function``
                          started. Nodes with seq > frame_entry_seq are
                          considered "in-frame" (always visible to this
                          function's prompts); nodes with seq <=
                          frame_entry_seq are "pre-frame" and subject to
                          ``render_range["depth"]`` capping. Use -1 (the
                          default) for top-level chat (no frame).
        render_range:     ``{"depth": int, "siblings": int}`` limits.
                          ``depth=0`` walls off the frame from prior
                          context entirely. ``siblings=N`` keeps only
                          the N most recent in-frame nodes.

    Visibility filtering:
        Whenever the chain includes a code-role Call with
        ``metadata.expose == "io"``, llm-role Calls whose
        ``called_by == that_code_call.id`` are dropped — the function
        is opaque (only its summary is visible).
        ``expose == "full"`` shows internals.

    Returns:
        Node ids in seq order (oldest first), ready to be the
        ``reads`` of the next LLM call.
    """
    if head_seq is None:
        head_seq = max((n.seq for n in graph.nodes.values()), default=-1)
    if head_seq < 0:
        return []

    depth_cap: Optional[int] = None
    siblings_cap: Optional[int] = None
    if isinstance(render_range, dict):
        if render_range.get("depth") is not None:
            depth_cap = int(render_range["depth"])
        if render_range.get("siblings") is not None:
            siblings_cap = int(render_range["siblings"])

    visible = [n for n in graph if n.seq <= head_seq]
    in_frame = [n for n in visible if n.seq > frame_entry_seq]
    pre_frame = [n for n in visible if n.seq <= frame_entry_seq]

    # depth_cap: keep the most-recent ``depth_cap`` pre-frame nodes.
    if depth_cap is not None:
        if depth_cap <= 0:
            pre_frame = []
        else:
            pre_frame = pre_frame[-depth_cap:]

    chain = pre_frame + in_frame

    # Expose filtering: pre-collect ``io`` code Calls that should hide
    # their internal llm Calls.
    suppressed_owners: set[str] = set()
    for n in chain:
        if n.is_code():
            ex = (n.metadata or {}).get("expose") or "io"
            if ex == "io":
                suppressed_owners.add(n.id)

    kept = [n for n in chain
            if not (n.is_llm() and n.called_by in suppressed_owners)]

    # siblings_cap: keep at most N in-frame nodes (most recent).
    if siblings_cap is not None and frame_entry_seq >= 0:
        in_frame_ids = {n.id for n in in_frame}
        in_frame_kept = 0
        final: list = []
        for n in reversed(kept):
            if n.id in in_frame_ids:
                in_frame_kept += 1
                if in_frame_kept > siblings_cap:
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
    "spawn_task",
    "branch_terminals",
    "branch_internal",
    "fold_history",
    "compute_reads",
]
