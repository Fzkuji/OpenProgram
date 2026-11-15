"""The deferred tools a compaction summary carries past the turns it covers.

A request loads what its own messages loaded (``shared.tool_loads.loaded_tools``).
A summary takes the place of the turns it covers, and with them their load
records, so it names the tools they had loaded: the compaction writes
``carried_tool_names`` into the summary node, most recently used first and
cut where their definitions would take more than ``CARRIED_WINDOW_SHARE``
of the model's window. Only names are kept; a request that sends
definitions takes each from the load record that first sent it, so the
bytes are the ones the conversation already sent.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from openprogram.models.providers.shared.tool_loads import load_records, valid_tool_name

#: The summary node's metadata key: the names it carries, most recent first.
CARRIED_TOOL_NAMES = "carried_tool_names"

#: The share of the context window the carried definitions may take; the
#: names past it stay listed in the deferred catalog, a search away.
CARRIED_WINDOW_SHARE = 0.05


def _model_results(nodes: list[Any], reply_ids: set[str]) -> list[Any]:
    """The tool results of the replies in ``reply_ids``, as a request sends
    them: a model's call (it has a tool call id) that is not hidden."""
    return [
        node for node in nodes
        if node.is_code() and node.caller in reply_ids
        and (node.metadata or {}).get("tool_call_id")
        and ((node.metadata or {}).get("expose") or "io") != "hidden"
    ]


def _definition_tokens(definition: Any) -> int:
    return len(json.dumps(definition, ensure_ascii=False, default=str)) // 4 + 1


def first_definitions(nodes: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """Each loaded tool's definition as the conversation first sent it."""
    found: dict[str, dict[str, Any]] = {}
    for node in sorted(nodes, key=lambda n: n.seq):
        if not node.is_code():
            continue
        for entry in load_records((node.metadata or {}).get("details")):
            found.setdefault(entry["name"], entry["definition"])
    return found


def carried_tool_names(nodes: Iterable[Any], covers_ids: Iterable[str],
                       context_window: int = 0) -> list[str]:
    """The tools the turns in ``covers_ids`` had loaded, most recently used first.

    A tool is used when it is loaded and when it is called. With a
    ``context_window`` the list stops before the definitions would take
    more than ``CARRIED_WINDOW_SHARE`` of it.
    """
    nodes = sorted(nodes, key=lambda n: n.seq)
    last_use: dict[str, int] = {}
    definitions: dict[str, Any] = {}
    for node in _model_results(nodes, set(covers_ids)):
        for entry in load_records((node.metadata or {}).get("details")):
            definitions.setdefault(entry["name"], entry["definition"])
            last_use[entry["name"]] = node.seq
        if node.name in last_use:
            last_use[node.name] = node.seq
    # Stable: tools loaded together keep their load order.
    ordered = sorted(last_use, key=lambda name: -last_use[name])
    if context_window <= 0:
        return ordered
    budget = int(context_window * CARRIED_WINDOW_SHARE)
    kept: list[str] = []
    used = 0
    for name in ordered:
        used += _definition_tokens(definitions.get(name))
        if used > budget:
            break
        kept.append(name)
    return kept


def carried_tools(graph: Any, summary: Any) -> list[dict[str, Any]]:
    """``{"name", "definition"}`` records of the tools ``summary`` carries.

    A summary written before compactions recorded the names carries what
    its covered turns loaded, uncut.
    """
    nodes = list(graph.nodes.values()) if hasattr(graph, "nodes") else list(graph)
    md = summary.metadata or {}
    names = md.get(CARRIED_TOOL_NAMES)
    if not isinstance(names, list):
        names = carried_tool_names(nodes, md.get("covers_ids") or ())
    definitions = first_definitions(nodes)
    records: list[dict[str, Any]] = []
    for name in dict.fromkeys(names):
        if not valid_tool_name(name):
            continue
        record: dict[str, Any] = {"name": name}
        if name in definitions:
            record["definition"] = definitions[name]
        records.append(record)
    return records
