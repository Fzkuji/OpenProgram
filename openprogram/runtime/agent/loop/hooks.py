"""The moments of the agent loop a Program joins.

The loop runs the requests and the tool calls; what a Program does at one of
its moments is the Program's, so the loop owns the moment and the Program
registers what it does there when it loads. The loop never names a Program.

* ``LoopScope`` — entered when a loop run starts, left when it ends (the
  tool guides keep the guides delivered in one run).
* ``request_observers`` — read the messages of each request before it is
  sent (the tool guides note which guides the model already has).
* ``result_attachments`` — rewrite a call's result before it is stored (the
  tool guides attach the guide a call is due).
* ``CallArgument`` — an argument the loop adds to every tool a request
  offers and takes out of each call before the tool sees it, applied before
  the call runs (``task_progress``, the task tools' progress on a real call).

Nothing registered is the loop of a process without those Programs: no
guides, no extra argument.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class LoopScope:
    """What a Program holds for one loop run: ``begin()`` returns a token
    that ``end(token)`` receives when the run ends, however it ends."""

    begin: Callable[[], Any]
    end: Callable[[Any], None]


@dataclass(frozen=True)
class CallArgument:
    """One argument the loop carries on calls for a Program.

    ``annotate(provider_tools, offered_tools)`` returns the request's tools
    array with the argument added; ``take(tool, args, offered_tools)``
    returns the arguments without it and its value (None when the call has
    none); ``apply(value, occurrence_id)`` applies a value before the call
    runs and returns the effect for the call's card and a note the model
    reads with the result.
    """

    key: str
    annotate: Callable[[list, Any], list]
    take: Callable[[Any, Any, Any], tuple[Any, Any]]
    apply: Callable[[Any, str], tuple[Optional[dict], str]]


loop_scopes: list[LoopScope] = []
request_observers: list[Callable[[Any], None]] = []
result_attachments: list[Callable[[str, Any, Any], Any]] = []
call_arguments: list[CallArgument] = []


def _add(registry: list, item: Any) -> None:
    if item not in registry:
        registry.append(item)


def register(*, loop_scope: LoopScope | None = None,
             request_observer: Callable[[Any], None] | None = None,
             result_attachment: Callable[[str, Any, Any], Any] | None = None,
             call_argument: CallArgument | None = None) -> None:
    """Join the loop at the moments given. Registering again is a no-op."""
    if loop_scope is not None:
        _add(loop_scopes, loop_scope)
    if request_observer is not None:
        _add(request_observers, request_observer)
    if result_attachment is not None:
        _add(result_attachments, result_attachment)
    if call_argument is not None:
        _add(call_arguments, call_argument)


def begin_run() -> list[tuple[LoopScope, Any]]:
    """Enter every loop scope; the result goes to ``end_run``."""
    return [(scope, scope.begin()) for scope in list(loop_scopes)]


def end_run(entered: list[tuple[LoopScope, Any]]) -> None:
    for scope, token in reversed(entered):
        scope.end(token)


def observe_request(messages: Any) -> None:
    for observer in list(request_observers):
        observer(messages)


def attach_to_result(tool_name: str, args: Any, result: Any) -> Any:
    for attachment in list(result_attachments):
        result = attachment(tool_name, args, result)
    return result


def annotate_tools(provider_tools: list, offered_tools: Any) -> list:
    for argument in list(call_arguments):
        provider_tools = argument.annotate(provider_tools, offered_tools)
    return provider_tools


__all__ = [
    "CallArgument",
    "LoopScope",
    "annotate_tools",
    "attach_to_result",
    "begin_run",
    "call_arguments",
    "end_run",
    "observe_request",
    "register",
]
