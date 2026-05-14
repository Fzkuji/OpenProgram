"""chat — a chat loop built on DagRuntime.

This is the new chat entrypoint. It runs one "turn" of conversation:

  1. Append the user's message to the Graph.
  2. Loop:
     a. Call the LLM with the user message and (folded) history.
     b. If the reply asks for a tool call, execute the tool, record a
        FunctionCall, and loop again — feeding the FunctionCall back
        in the next ModelCall's ``reads``.
     c. Otherwise, return the reply text.

No Context tree. No @agentic_function. The Graph IS the conversation
state. Persistence is whatever GraphStore the runtime is attached to.

The chat dispatcher only knows how to execute tools by looking up
callables in a passed-in registry. The framework doesn't impose any
particular tool format — the contract is just "given a (name, args)
tuple, return the result".
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from openprogram.context.nodes import (
    Call,
    Graph,
    fold_history,
)
from openprogram.context.runtime import DagRuntime


# A tool registry maps tool name -> callable(**args) -> result
ToolRegistry = dict[str, Callable[..., Any]]


# Default extractor for tool calls — accepts simple JSON of shape
#   {"call": "tool_name", "args": {...}}
# Returns (name, args) or None.
def parse_tool_call(text: str) -> Optional[tuple[str, dict]]:
    """Best-effort: pull a {"call": "...", "args": {...}} JSON from text."""
    if not text:
        return None
    # Allow a fenced or bare JSON object.
    fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fenced:
        try:
            obj = json.loads(fenced.group(1))
            if isinstance(obj, dict) and "call" in obj:
                return obj["call"], obj.get("args") or {}
        except json.JSONDecodeError:
            pass
    # Try bare JSON object.
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and "call" in obj:
                return obj["call"], obj.get("args") or {}
        except json.JSONDecodeError:
            pass
    return None


def chat_turn(
    user_input: str,
    *,
    runtime: DagRuntime,
    tools: Optional[ToolRegistry] = None,
    max_iterations: int = 10,
    system: Optional[str] = None,
    tool_call_parser: Callable[[str], Optional[tuple[str, dict]]] = parse_tool_call,
) -> str:
    """Run one turn of conversation through the DAG.

    Args:
        user_input: text of the user's message for this turn.
        runtime: DagRuntime instance carrying the Graph (and optional store).
        tools: name → callable map. Each callable accepts kwargs and
               returns a JSON-serializable result. If a ModelCall's
               output parses as ``{"call": "name", "args": {...}}`` and
               ``name`` is in this registry, the tool is invoked,
               recorded as a FunctionCall, and the loop continues.
        max_iterations: cap on the LLM→tool loop. Prevents infinite loops.
        system: optional system prompt for every LLM call this turn.
        tool_call_parser: pluggable extractor — defaults to JSON shape.

    Returns:
        Final assistant reply text. The whole exchange is recorded in
        ``runtime.graph``.
    """
    tools = tools or {}
    user_node = runtime.add_user_message(user_input)

    for _ in range(max_iterations):
        # The reads for this LLM call: fold history + everything since
        # the user message (so a subsequent iteration can see prior
        # FunctionCalls and ModelCalls from this same turn).
        reads = fold_history(runtime.last_node_id(), runtime.graph)

        reply = runtime.exec(
            content=[],  # everything is in reads; the user message is already in the graph
            reads=reads,
            system=system,
        )

        # Did the model ask for a tool?
        parsed = tool_call_parser(reply)
        if parsed is None:
            return reply

        tool_name, args = parsed
        if tool_name not in tools:
            # Treat as final reply — model said something tool-shaped
            # but the tool isn't available.
            return reply

        # Execute and record.
        try:
            result = tools[tool_name](**args)
        except Exception as e:
            result = {"error": f"{type(e).__name__}: {e}"}

        last_model_id = runtime.last_node_id()
        runtime.record_function_call(
            function_name=tool_name,
            arguments=args,
            called_by=last_model_id,
            result=result,
        )

    # Iteration cap hit — return the last reply with a note.
    return reply


__all__ = ["chat_turn", "parse_tool_call", "ToolRegistry"]
