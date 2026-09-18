"""Host-owned execution evidence scoped to one tool invocation."""
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class ToolExecution:
    start: Callable[[], Awaitable[None]]
    started: bool = False
    automatic_refusal: bool = False


current_execution: ContextVar[ToolExecution | None] = ContextVar("permission_tool_execution", default=None)


async def mark_started():
    state = current_execution.get()
    if state is not None and not state.started:
        await state.start()
        state.started = True


def mark_refused(code):
    state = current_execution.get()
    if state is not None and not state.started and code == "AUTO_CLASSIFIER_DENY":
        state.automatic_refusal = True
