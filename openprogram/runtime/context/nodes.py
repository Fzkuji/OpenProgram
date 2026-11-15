"""Context computations over the session DAG.

The node schema (``Call``, ``Graph``, the role constants) is the session
store's: ``openprogram.state.store.session.nodes``. This module reads a
graph the way the context engine needs it — the user turn, spawn branches,
fold history, the rendered spine and path, the active summary, and
``render_context``, which picks the nodes an LLM call reads.
"""

from __future__ import annotations

from typing import Optional

from openprogram.state.store.session.nodes import Call, Graph
from openprogram.state.store.session.summaries import active_summary_for_chain, summary_covers_ids


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
        frame_entry_seq:  seq value when the current ``Agent method``
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
        from openprogram.runtime.context.scope import default_expose
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
    summary = active_summary_for_chain(graph, (n.id for n in chain))
    elided: set[str] = set()
    splice_before: Optional[str] = None
    if summary is not None:
        seg = summary_covers_ids(summary) or []
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

    def _hidden(n) -> bool:
        # io function: hide its internal llm exchanges.
        if n.is_llm() and n.caller in io_owners:
            return True
        # llm function: hide its own input/output node and its nested
        # code sub-calls — only its llm exchanges survive.
        return n.id in llm_owners or (n.is_code() and n.caller in llm_owners)

    kept = []
    for n in chain:
        if splice_before is not None and n.id == splice_before:
            kept.append(summary)
        if n.id in elided:
            continue
        if _hidden(n):
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
    "last_user_message",
    "linear_back_to",
    "spawn_job",
    "branch_terminals",
    "branch_internal",
    "fold_history",
    "summary_covers_ids",
    "active_summary_for_chain",
    "render_spine",
    "render_path",
    "render_context",
]
