"""What hangs off a reply: cards, runtime children, call trees, and which block owns each."""
from __future__ import annotations

import json
from typing import Any

SPAWNING_TOOLS = ("agent", "task", "send_message")
_AGENT_STARTING_TOOLS = {"agent", "task"}
_RUNTIME_CHILD_TOOLS = {"gui_agent"}
_STORED_PROMPT_CHARS = 500


def is_reference_card(attach: dict | None, session_id: str | None) -> bool:
    """A card this conversation does not own: attached by hand or from elsewhere."""
    facts = attach or {}
    target = facts.get("session_id")
    return bool(facts.get("manual")) or bool(session_id and target and target != session_id)


def _is_provider_search(block: dict) -> bool:
    return block.get("provider_executed") is True and block.get("tool") == "web_search"


def _input_field(block: dict, field: str) -> str:
    raw = block.get("input")
    if not raw:
        return ""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    found = value.get(field) if isinstance(value, dict) else None
    return found.strip() if isinstance(found, str) else ""


def _prompt_names(block: dict, card: dict) -> bool:
    prompt = str((card.get("attach") or {}).get("prompt") or "").strip()
    if not prompt:
        return False
    said = _input_field(block, "message" if block.get("tool") == "send_message" else "prompt")
    return bool(said) and (said == prompt or said[:_STORED_PROMPT_CHARS].strip() == prompt)


def assign_block_owners(row: dict, session_id: str | None) -> None:
    """Set ``blockId`` on each child of ``row``; an unpaired child gets None and is still drawn."""
    calls = [block for block in row.get("blocks") or [] if block.get("type") == "tool" and not _is_provider_search(block)]
    call_ids = {block.get("tool_call_id") for block in calls}
    taken: set[str] = set()
    cards = row.get("attachCards") or []
    for card in cards:
        card["blockId"] = None

    def free(names) -> list[dict]:
        return [block for block in calls if block.get("tool") in names and block["id"] not in taken]

    def claim(card: dict, block: dict) -> None:
        card["blockId"] = block["id"]
        taken.add(block["id"])

    waiting = []
    for card in cards:
        if is_reference_card(card.get("attach"), session_id):
            continue
        named = (card.get("attach") or {}).get("tool_call_id")
        owner = next((b for b in free(SPAWNING_TOOLS) if b.get("tool_call_id") == named), None) if named else None
        if owner:
            claim(card, owner)
        elif not named:
            waiting.append(card)
    # Evidence first, so order never takes a block a later card's prompt names.
    for card in list(waiting):
        ordered = [block for name in SPAWNING_TOOLS for block in free((name,))]
        owner = next((b for b in ordered if _prompt_names(b, card)), None)
        if owner:
            claim(card, owner)
            waiting.remove(card)
    for card in waiting:
        owner = next(iter(free(_AGENT_STARTING_TOOLS)), None)
        if owner:
            claim(card, owner)

    children = row.get("runtimeChildren") or []
    pool = list(children)
    for child in children:
        child["blockId"] = None
    runtime_calls = free(_RUNTIME_CHILD_TOOLS)
    with_tree: set[str] = set()

    def give(child: dict, block: dict) -> None:
        child["blockId"] = block["id"]
        pool.remove(child)
        if child.get("contextTree"):
            with_tree.add(block["id"])

    filled: set[str] = set()
    for block in runtime_calls:
        child = next((c for c in pool if c.get("toolCallId") and c["toolCallId"] == block.get("tool_call_id")), None)
        if child:
            give(child, block)
            filled.add(block["id"])
    for block in runtime_calls:
        if block["id"] in filled:
            continue
        child = next((c for c in pool if not c.get("toolCallId") or c["toolCallId"] not in call_ids), None)
        if child:
            give(child, block)
            filled.add(block["id"])

    roots = list(row.get("callRoots") or [])
    for root in roots:
        root["blockId"] = None
    treeless = [block for block in calls if block["id"] not in taken and block["id"] not in with_tree]
    placed: set[str] = set()
    for block in treeless:
        root = next((r for r in roots if r.get("tool_call_id") and r["tool_call_id"] == block.get("tool_call_id")), None)
        if root:
            root["blockId"] = block["id"]
            roots.remove(root)
            placed.add(block["id"])
    for block in treeless:
        if block["id"] in placed:
            continue
        root = next((r for r in roots if r.get("name") == block.get("tool")
                     and (not r.get("tool_call_id") or r["tool_call_id"] not in call_ids)), None)
        if root:
            root["blockId"] = block["id"]
            roots.remove(root)
            placed.add(block["id"])


def execution_rows(chain: list[dict], raw_rows: list[dict]) -> list[dict]:
    """The nested call trees under the chain's assistant replies, depth-first.

    A function the model called is stored as a tree of rows hanging off its
    reply by ``caller``. The client folds these rows into the reply's call tree
    and never draws them as messages. Aggregation has folded each tree's root
    into the reply's ``tool_calls``, so the walk reads raw rows. A call with
    nothing under it is already complete as that entry and sends no row,
    unless another call of that name in the reply has a tree: same-named blocks
    each need their own root. Each reply's tree stays within one byte budget.
    """
    from openprogram.interfaces.server.ws_actions.session.tool_output import call_tree_rows

    chain_ids = {row.get("id") for row in chain}
    kids_by_caller: dict[Any, list[dict]] = {}
    for row in raw_rows:
        caller = row.get("caller")
        if caller and caller != "ROOT":
            kids_by_caller.setdefault(caller, []).append(row)

    def descendants(root: dict) -> list[dict]:
        found: list[dict] = []
        seen = {root.get("id")}
        stack = [(kid, root) for kid in reversed(kids_by_caller.get(root.get("id"), []))]
        while stack:
            row, parent = stack.pop()
            row_id = row.get("id")
            # A spawned branch's root (role=user) and an attach pointer
            # (its parent is an assistant) match neither rule.
            is_tool = row.get("role") == "tool" and row.get("function")
            is_leaf = row.get("role") == "assistant" and parent.get("role") == "tool"
            if row_id in seen or row_id in chain_ids or not (is_tool or is_leaf):
                continue
            seen.add(row_id)
            found.append(row)
            stack.extend((kid, row) for kid in reversed(kids_by_caller.get(row_id, [])))
        return found

    rows: list[dict] = []
    for message in chain:
        # A top-level runtime card already carries its complete caller subtree
        # in context_tree; its descendants must not read as chat rows here.
        if message.get("role") != "assistant" or message.get("display") == "runtime":
            continue
        trees = [
            (root, descendants(root))
            for root in kids_by_caller.get(message.get("id"), [])
            if root.get("role") == "tool" and root.get("function") and root.get("id") not in chain_ids
        ]
        deep_names = {root.get("function") for root, below in trees if below}
        rows.extend(call_tree_rows([
            row
            for root, below in trees if root.get("function") in deep_names
            for row in (root, *below)
        ]))
    return rows
