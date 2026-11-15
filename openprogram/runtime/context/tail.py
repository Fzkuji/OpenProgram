"""The bounded tail a background turn reads of its conversation.

A background turn (an always-on check-in) looks at the conversation; it must
stay cheap however long the conversation has grown, and it must never compact
what belongs to the owner. So instead of shrinking the conversation it reads
less of it: the active summary, then only the latest steps that fit the
budget. What it did not read stays reachable through ``read_conversation``.

The cut falls between steps. A step is a top-level node (a user message, a
reply, a direct function call) or one call a reply made together with
everything under that call, so a tool call and its result are always kept or
dropped together. The reply that made a kept call is kept with it.

A step costs what the renderer sends for it, not what the node stores: a
spilled result reaches the model as head and tail, and a ``/skill`` message as its whole body.
"""
from __future__ import annotations

from typing import Any

from openprogram.runtime.context.nodes import summary_covers_ids
from openprogram.runtime.context.render import render_dag_messages
from openprogram.runtime.context.tokens import estimate_message_tokens

BACKGROUND_TAIL_TOKENS = 16_000

OMITTED_NOTE = (
    "[Earlier messages of this conversation are not shown here. "
    "Call read_conversation only if you need them.]"
)


def _rendered_tokens(graph: Any, node_ids: list[str]) -> int:
    return sum(estimate_message_tokens(message)
               for message in render_dag_messages(graph, node_ids))


def bounded_tail(graph: Any, read_ids: list[str], max_tokens: int) -> tuple[list[str], bool]:
    """Keep the summary and the newest steps of ``read_ids`` within ``max_tokens``.

    Returns the kept ids in their original order and whether anything was
    left out. The budget is a hard limit on the tail, so a newest step larger
    than the whole budget is left out too: the model then reads it on purpose
    rather than being handed it. The summary is not counted against the tail.
    """
    present = set(read_ids)
    owner_of: dict[str, str] = {}  # step -> its top-level node: itself, or the reply above it
    step_of: dict[str, str] = {}   # node -> the id naming its step

    def is_top(node: Any) -> bool:
        return not node.caller or node.caller not in present or node.caller == "ROOT"

    def locate(node_id: str) -> tuple[str, str]:
        chain = [node_id]
        while not is_top(graph.nodes[chain[-1]]):
            chain.append(graph.nodes[chain[-1]].caller)
        owner = chain[-1]
        return owner, (chain[-2] if len(chain) > 1 else owner)

    members: dict[str, list[str]] = {}
    order: list[str] = []
    for node_id in read_ids:
        if node_id not in graph.nodes:
            continue
        owner, step = locate(node_id)
        step_of[node_id] = step
        owner_of[step] = owner
        if step not in members:
            members[step] = []
            order.append(step)
        members[step].append(node_id)

    summaries = {nid for nid in step_of if summary_covers_ids(graph.nodes[nid]) is not None}
    kept: set[str] = set(summaries)
    charged: set[str] = set()  # replies already paid for by a later call of theirs
    used = 0
    complete = True
    for step in reversed(order):
        if step in summaries:
            continue
        cost = 0 if step in charged else _rendered_tokens(graph, members[step])
        owner = owner_of[step]
        owner_needed = owner != step and owner not in kept and owner in members
        if owner_needed:
            cost += _rendered_tokens(graph, members[owner])
        if used + cost > max_tokens:
            complete = False
            break
        used += cost
        kept.update(members[step])
        if owner_needed:
            kept.update(members[owner])
            charged.add(owner)
    if complete:
        return list(read_ids), False
    return [nid for nid in read_ids if nid in kept], True
