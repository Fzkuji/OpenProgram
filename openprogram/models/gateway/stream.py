"""One upstream call for a gateway request.

The request goes through ``stream_simple``, the same provider path the agent
loop uses, so credential rotation, retries and the model's own fault
tolerance (docs/reference/design/providers/reliability/) apply unchanged and
no second retry layer is added here. The call runs in a task of its own,
started inside the request's usage scope, so the usage ledger attributes it
to the harness wherever the response body is written from.

:meth:`UpstreamCall.open` reads up to the first event that writes anything to
the client. A failure before it is a :class:`GatewayError` the route answers
with a plain error response; after it, the protocol's in-stream error event
is written instead and nothing is sent again (a reply that would restart
after content streamed ends with an error rather than repeating itself).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from openprogram.models.providers.types import AssistantMessage, Context, Model, SimpleStreamOptions

from .conversion import GatewayError

_log = logging.getLogger(__name__)
_END = object()
#: Events that write nothing to the client by themselves.
_SILENT = frozenset({"start", "retry", "structured_output_retry"})


def error_of_message(message: AssistantMessage | None, provider: str = "") -> GatewayError:
    """The gateway error for a provider reply that ended in error."""
    reason = (getattr(message, "error_reason", None) or "unknown") if message else "unknown"
    text = (getattr(message, "error_message", None) or "the model returned an error") if message else "the model returned an error"
    if getattr(message, "stop_reason", None) == "aborted":
        return GatewayError(499, "api_error", "the request was cancelled")
    return _error_for(str(reason), text, provider)


def error_of_exception(exc: BaseException, provider: str = "") -> GatewayError:
    if isinstance(exc, GatewayError):
        return exc
    try:
        from openprogram.models.providers.utils.failures.errors import classify_error
        reason, _ = classify_error(exc)
        value = reason.value
    except Exception:  # noqa: BLE001 - classification is best effort
        value = "unknown"
    return _error_for(value, str(exc) or type(exc).__name__, provider)


def _error_for(reason: str, text: str, provider: str) -> GatewayError:
    if reason == "rate_limit":
        return GatewayError(429, "rate_limit", text)
    if reason == "context":
        return GatewayError(400, "context_length", text)
    if reason in {"invalid", "policy"}:
        return GatewayError(400, "invalid_request", text)
    if reason in {"auth", "authz"}:
        # The upstream credential is OpenProgram's, not the harness's: a 401
        # would send the harness to its own sign-in.
        name = provider or "the provider"
        return GatewayError(502, "api_error", f"OpenProgram's credential for {name} was refused: {text}")
    return GatewayError(502, "api_error", text)


class UpstreamCall:
    """A running ``stream_simple`` call read event by event."""

    def __init__(self, model: Model, queue: asyncio.Queue, task: asyncio.Task) -> None:
        self.model = model
        self._queue = queue
        self._task = task
        self._head: list[Any] = []
        self.wrote = False

    @classmethod
    async def open(cls, model: Model, context: Context, options: SimpleStreamOptions,
                   *, harness: str) -> "UpstreamCall":
        from openprogram.models.providers import stream_simple
        from openprogram.models.usage import usage_scope

        queue: asyncio.Queue = asyncio.Queue()

        async def produce() -> None:
            try:
                async for event in stream_simple(model, context, options):
                    await queue.put(event)
                await queue.put(_END)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 - handed to the reader
                await queue.put(exc)

        with usage_scope(call_kind="harness", call_label=harness):
            task = asyncio.get_running_loop().create_task(produce())
        call = cls(model, queue, task)
        try:
            while True:
                item = await queue.get()
                if item is _END:
                    call._head.append(item)
                    break
                if isinstance(item, BaseException):
                    raise error_of_exception(item, model.provider)
                kind = getattr(item, "type", None)
                if kind == "error":
                    raise error_of_message(getattr(item, "error", None), model.provider)
                call._head.append(item)
                if kind not in _SILENT:
                    break
        except BaseException:
            await call.close()
            raise
        return call

    async def events(self) -> AsyncIterator[Any]:
        """The provider's events; raises :class:`GatewayError` on failure."""
        try:
            while True:
                item = self._head.pop(0) if self._head else await self._queue.get()
                if item is _END:
                    return
                if isinstance(item, BaseException):
                    raise error_of_exception(item, self.model.provider)
                kind = getattr(item, "type", None)
                if kind == "error":
                    raise error_of_message(getattr(item, "error", None), self.model.provider)
                if kind == "retry" and getattr(item, "restart", None) and self.wrote:
                    raise GatewayError(502, "api_error",
                                       "the model's reply was interrupted after it started")
                if kind not in _SILENT:
                    self.wrote = True
                yield item
        finally:
            await self.close()

    async def final(self) -> AssistantMessage:
        """The whole reply, for a request that did not ask for a stream."""
        final = None
        async for event in self.events():
            if getattr(event, "type", None) == "done":
                final = event.message
        if final is None:
            raise GatewayError(502, "api_error", "the model ended without a reply")
        return final

    async def close(self) -> None:
        if not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except BaseException:  # noqa: BLE001 - cancelled on purpose
                pass
