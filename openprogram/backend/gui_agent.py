"""Host-owned isolated GUI tool for the existing standard Agent runtime."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

from openprogram.agent import AgentTool
from openprogram.agent.run_control import get_current_execution_id
from openprogram.agentic_programming.function import current_call_id
from openprogram.execution.attempts import AttemptStatus
from openprogram.execution.model import ExecutionStatus
from openprogram.programs import ToolReturn
from openprogram.programs._execution_common import _normalize_result

from .gui_broker import GuiBroker
from .gui_browser import register_browser_page
from .gui_runner import GuiPythonRunner

_log = logging.getLogger(__name__)


class GuiAgentTools:
    """One invocation's interpreter and native AgentTool, without another loop.

    Construct inside the owning agentic function, then pass ``tool`` to normal
    ``agent()``. Only host code can bind resources. Page lifetime remains with
    its existing owner. Closing the context revokes input before killing Python.
    """

    def __init__(self):
        from openprogram.execution.control import default_control_service
        self._execution_id = get_current_execution_id()
        self._invocation_id = current_call_id()
        if not self._execution_id or not self._invocation_id:
            raise PermissionError("GUI tools require an active execution and invocation")
        self._service = default_control_service()
        execution = self._service.executions.get_execution(self._execution_id)
        self._attempt_id = execution.current_attempt_id if execution else None
        self._generation = execution.owner_lease.get("generation") if execution else None
        self._check()
        self.broker = GuiBroker(effects=self._service.effects, execution_id=self._execution_id,
                                attempt_id=self._attempt_id, generation=self._generation,
                                invocation_id=self._invocation_id)
        self.runner = GuiPythonRunner(broker=self.broker)
        self.tool = AgentTool(
            name="gui_exec", label="GUI Python",
            description="Execute persistent isolated Python with top-level await. Use await ui.call(handle, method, arguments, observation=frame_id) on host-provided handles. Print values to inspect them. Script completion does not verify a GUI action. No host filesystem, network or subprocess access.",
            parameters={"type": "object", "properties": {
                "code": {"type": "string"},
                "timeout": {"type": "number", "exclusiveMinimum": 0, "maximum": 30},
            }, "required": ["code"], "additionalProperties": False},
            execute=self._execute,
        )

    def _check(self):
        from openprogram.execution.control import default_control_service
        if (get_current_execution_id() != self._execution_id
                or default_control_service() is not self._service):
            raise PermissionError("GUI execution context changed")
        execution = self._service.executions.get_execution(self._execution_id)
        attempt = self._service.attempts.get(self._attempt_id) if self._attempt_id else None
        if (execution is None or execution.status is not ExecutionStatus.RUNNING
                or execution.current_attempt_id != self._attempt_id
                or execution.owner_lease.get("generation") != self._generation
                or attempt is None or attempt.status is not AttemptStatus.ACTIVE
                or attempt.execution_id != self._execution_id or attempt.generation != self._generation
                or attempt.lease_expires_at <= time.time()):
            raise PermissionError("GUI execution attempt is stale or not running")

    def __enter__(self):
        self._check()
        self.runner.__enter__()
        return self

    def bind_browser(self, registry, *, owner_id: str, web_session_id: str) -> str:
        self._check()
        return register_browser_page(self.broker, registry, owner_id=owner_id, web_session_id=web_session_id)

    def close(self):
        self.runner.close()

    def __exit__(self, *args):
        self.close()

    async def _execute(self, tool_call_id, arguments, signal, update_cb):
        cancelled = threading.Event()
        class Cancellation:
            def is_set(inner):
                return cancelled.is_set() or signal is not None and signal.is_set()
        worker = None
        try:
            self._check()
            if not isinstance(arguments, dict) or set(arguments) - {"code", "timeout"}:
                raise ValueError("unsupported GUI tool arguments")
            worker = asyncio.create_task(asyncio.to_thread(
                self.runner.execute, arguments["code"], timeout=arguments.get("timeout", 30),
                cancel=Cancellation(),
            ))
            result = await asyncio.shield(worker)
            self._check()
            payload = {"stdout": result.stdout, "stderr": result.stderr, "error": result.error}
            raw = ToolReturn(text=json.dumps(payload, ensure_ascii=False), json_data=payload,
                             images=list(result.images), is_error=result.error is not None)
        except asyncio.CancelledError:
            cancelled.set()
            self.close()
            if worker is not None:
                try:
                    await asyncio.shield(worker)
                except (Exception, asyncio.CancelledError):
                    _log.debug("GUI worker cleanup failed during cancellation", exc_info=True)
            raise
        except Exception as exc:
            raw = ToolReturn(text=f"{type(exc).__name__}: {exc}", is_error=True)
        return _normalize_result(raw, call_id=tool_call_id, max_chars=512 * 1024,
                                 persist_full=False, head_ratio=0.5)
