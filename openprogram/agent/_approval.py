"""Tool-approval gate — process-wide registry + wrapping.

Lifted out of ``dispatcher.py`` to keep that file from drowning. Three
moving parts:

* ``ApprovalRegistry`` — process-wide table of pending tool-approval
  requests keyed by ``request_id``. The dispatcher's ``_await_user_approval``
  registers a slot, posts an ``approval_request`` envelope, then blocks
  off the asyncio loop until the WS handler resolves the slot via
  ``approval_response``.
* ``_wrap_with_approval`` — returns a copy of the agent tool whose
  ``execute`` first awaits approval (unless permission_mode bypasses
  it). The wrapping happens inside the tool's coroutine because
  agent_loop schedules tool.execute eagerly — gating from outside is
  racey.
* ``_await_user_approval`` — the one-shot helper used by the wrapper.

Tests reach in via ``dispatcher.approval_registry()`` — that accessor
re-exports the singleton from this module, see dispatcher.py.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from openprogram.agent.dispatcher import TurnRequest

EventCallback = Callable[[dict], None]


class ApprovalRegistry:
    """Process-wide registry of pending tool-approval requests.

    Dispatcher posts an ``approval_request`` event with a request_id;
    the WS handler resolves the matching future when an
    ``approval_response`` action arrives. Times out at 5min so a
    forgotten approval doesn't pin a worker thread forever.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, threading.Event] = {}
        self._answer: dict[str, bool] = {}

    def register(self, request_id: str) -> threading.Event:
        ev = threading.Event()
        with self._lock:
            self._pending[request_id] = ev
        return ev

    def resolve(self, request_id: str, approved: bool) -> bool:
        """Return True if the request_id was waiting; False otherwise."""
        with self._lock:
            ev = self._pending.pop(request_id, None)
            if ev is None:
                return False
            self._answer[request_id] = approved
        ev.set()
        return True

    def consume(self, request_id: str) -> Optional[bool]:
        """Read the resolution after the wait completes. Pops the slot."""
        with self._lock:
            return self._answer.pop(request_id, None)


_approvals = ApprovalRegistry()


def approval_registry() -> ApprovalRegistry:
    return _approvals


def wrap_with_approval(
    agent_tool,
    req: "TurnRequest",
    on_event: EventCallback,
):
    """Return a copy of ``agent_tool`` whose ``execute`` first checks
    approval, awaiting (not blocking) the user's response. Falls back
    to the original tool when permission_mode is "bypass" or the
    tool's per-tool gate decides no approval is needed.

    Why a wrapper layer (vs. inspecting tool_execution_start in the
    drain): agent_loop schedules ``await tool.execute(...)`` directly
    after pushing tool_execution_start. The dispatcher's async-for
    consumer can't reliably block the tool from running because the
    tool already runs as a thread-pool task in parallel. Gating
    inside the tool's own coroutine is the only safe seam.
    """
    from openprogram.agent.types import AgentTool, AgentToolResult
    from openprogram.providers.types import TextContent
    from openprogram.functions._runtime import tool_requires_approval

    orig_execute = agent_tool.execute

    # Tools that MUST always ask for user input, even when the session's
    # permission_mode is "bypass". exit_plan_mode is the canonical case:
    # the whole point of submitting a plan is to get explicit user sign-
    # off; silently approving it under bypass would defeat the feature.
    _force_approval_tools = {"exit_plan_mode"}

    async def _gated_execute(call_id, args, cancel, on_update):
        force_ask = agent_tool.name in _force_approval_tools
        if req.permission_mode == "bypass" and not force_ask:
            return await orig_execute(call_id, args, cancel, on_update)

        per_tool_required, _per_tool_reason = tool_requires_approval(agent_tool, args)
        if req.permission_mode == "auto" and not force_ask:
            risky_default = agent_tool.name in {"bash", "exec", "shell",
                                                  "execute_code", "process"}
            if not per_tool_required and not risky_default:
                return await orig_execute(call_id, args, cancel, on_update)

        approved = await await_user_approval(
            req=req,
            tool_name=agent_tool.name,
            args=args,
            on_event=on_event,
        )
        if not approved:
            return AgentToolResult(
                content=[TextContent(text=f"[denied] user did not approve {agent_tool.name}")],
                details={"is_error": True, "denied": True},
            )
        return await orig_execute(call_id, args, cancel, on_update)

    return AgentTool(
        name=agent_tool.name,
        description=agent_tool.description,
        parameters=agent_tool.parameters,
        label=getattr(agent_tool, "label", agent_tool.name) or agent_tool.name,
        execute=_gated_execute,
    )


async def await_user_approval(
    *,
    req: "TurnRequest",
    tool_name: str,
    args: dict,
    on_event: EventCallback,
    timeout: float = 300.0,
) -> bool:
    """Post an approval_request envelope, await the user's response.

    Uses ``asyncio.to_thread`` to wait on the threading.Event so the
    asyncio loop stays free to process other events (e.g. tool
    progress updates from concurrent tools).
    """
    request_id = uuid.uuid4().hex[:12]
    waiter = _approvals.register(request_id)
    on_event({
        "type": "approval_request",
        "data": {
            "request_id": request_id,
            "session_id": req.session_id,
            "tool": tool_name,
            "args": args,
        },
    })
    fired = await asyncio.to_thread(waiter.wait, timeout)
    if not fired:
        return False
    return bool(_approvals.consume(request_id))
