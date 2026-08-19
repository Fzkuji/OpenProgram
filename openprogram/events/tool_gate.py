"""tool.before gate — a thin shell over the bus's gate dispatch.

``register_tool_gate`` / ``decide_tool_gate`` / ``ToolGateDenied`` keep
their public signatures (agent_loop and the proactive engine call them);
internally they are the ``tool.before`` slot of ``EventBus.subscribe_gate``
/ ``emit_gate``. Semantics unchanged:

* gates must be fast — this is the synchronous hot path; no LLM calls, no
  slow IO (docs/reference/design/proactive/execution-model.md §2).
* every gate is asked; any deny reason blocks the tool, reasons merge.
* a raising gate is fail-open (stderr).
* subagents are covered too: the ask sits outside the permission_mode
  approval wrapper, so ``permission_mode="bypass"`` cannot turn it off.
"""
from __future__ import annotations

from typing import Callable

from openprogram.events.bus import Event, get_event_bus

# gate 函数：拿到 tool.before 事件，返回 None（放行）或 deny 理由字符串。
ToolGate = Callable[[Event], "str | None"]


class ToolGateDenied(Exception):
    """Raised inside the tool-execution try block when a gate denies the
    call — caught by the existing error path, so the model receives the
    deny reason as an error tool result."""


def register_tool_gate(gate: ToolGate) -> Callable[[], None]:
    """Register a ``tool.before`` gate on the process bus. Returns an
    unregister function."""
    return get_event_bus().subscribe_gate("tool.before", gate)


def decide_tool_gate(event: Event) -> "str | None":
    """Ask every gate; return the merged deny reason, or None to allow."""
    outcome = get_event_bus().emit_gate(event)
    if outcome.allowed:
        return None
    return "; ".join(outcome.reasons)
