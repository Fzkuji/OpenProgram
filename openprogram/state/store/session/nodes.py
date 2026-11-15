"""The persisted session DAG node schema.

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
                     node inside an Agent method's execution
                     subtree names another call.
  - ``reads``        context edges. For LLM calls: the prior nodes
                     whose content shaped this prompt. Stored as a
                     list of ids on the node itself; not derived.

Time order is ``seq`` alone — never an edge. No tree, no containers:
nesting is expressed by ``caller``, chat order by ``predecessor``.

This module is the schema the session store persists: the role
constants, the ``Call`` node, and ``Graph``, the in-memory append-only
container. What the context engine computes over a graph (render
context, spines, fold history) lives in ``openprogram.runtime.context.nodes``.
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
        # Ids a rewind retracted from the conversation; they stay stored.
        self.retracted: frozenset[str] = frozenset()

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
        """In-place update of an existing node (used at Agent method
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
        # (parent Agent method whose own node is appended after
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
]
