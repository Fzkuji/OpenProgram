"""Requests a provider refused for their shape, mended and sent again.

Some refusals say exactly what to change: the body is over the provider's
byte cap (:mod:`body_cap`), or the model takes no conversation ending with
the assistant (:mod:`prefill`). Each such repair is a module with two
hooks:

* ``before_sending(model, context) -> Context`` applies what was learned
  from earlier refusals, so the same refusal is not met again;
* ``after_refusal(model, context, refusal) -> Context | None`` answers
  the mended request when ``refusal`` is one it knows, else ``None``.

:func:`prepared` runs the first hook of every repair before a request
goes; :func:`repairing` reads a provider's events and, when an attempt is
refused before it said anything, asks the repairs in turn and sends the
first mended request again, at most :data:`MAX_RESENDS` times. Events are
never held back: the consumer sees the reply begin once (a resent
attempt's first ``start`` is dropped), and the refusal itself is never
passed on. Every provider stream goes through here
(``streaming.stream._metered``).
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from openprogram.models.providers.types import AssistantMessage, Context, Model
from openprogram.models.providers.utils import body_cap, prefill
from openprogram.models.providers.utils.event_stream import close_stream

REPAIRS = (prefill, body_cap)
MAX_RESENDS = 3

StreamFn = Callable[[Model, Context, Any], AsyncIterator[Any]]


def prepared(model: Model, context: Context) -> Context:
    """``context`` with what earlier refusals taught applied."""
    for repair in REPAIRS:
        context = repair.before_sending(model, context)
    return context


def mended(model: Model, context: Context, refusal: AssistantMessage) -> Context | None:
    """The request mended for ``refusal`` by the first repair that knows it."""
    for repair in REPAIRS:
        fixed = repair.after_refusal(model, context, refusal)
        if fixed is not None:
            return fixed
    return None


class RepairingStream:
    """A provider's events, sent again mended when the provider refuses the request's shape.

    Events pass through as they come. Only an error that ends an attempt
    before it said anything is a refusal the repairs may mend; a resent
    attempt's first ``start`` is dropped, as the reply already began for the
    consumer, and its later events carry their own partial reply.

    ``cancel_producer`` closes whichever provider stream is current, so a
    caller's bounded close (``event_stream.close_stream``) reaches it as it
    reached the provider's stream before.
    """

    def __init__(
        self,
        events: AsyncIterator[Any],
        stream_fn: StreamFn,
        model: Model,
        context: Context,
        options: Any,
    ) -> None:
        self.current = events
        self._stream_fn = stream_fn
        self._model = model
        self._context = context
        self._options = options

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._events()

    async def cancel_producer(self) -> None:
        await close_stream(self.current)

    async def _events(self) -> AsyncIterator[Any]:
        for resend in range(MAX_RESENDS + 1):
            said = False
            restart_dropped = resend == 0
            fixed: Context | None = None
            async for event in self.current:
                kind = _kind(event)
                if kind == "start":  # a reply beginning is not the reply saying anything
                    if restart_dropped:
                        yield event
                    restart_dropped = True  # a resent attempt's: the reply already began
                    continue
                if not said and kind == "error" and resend < MAX_RESENDS:
                    refusal = _error_of(event)
                    fixed = mended(self._model, self._context, refusal) if refusal is not None else None
                    if fixed is not None:
                        break
                said = True
                yield event
            if fixed is None:
                return
            await close_stream(self.current)
            self._context = fixed
            # The mended request is the one answered: it replaces the
            # refused one in the attempt's request slot.
            from ..recording.capture import reopen_slot
            reopen_slot()
            self.current = self._stream_fn(self._model, fixed, self._options)


def repairing(
    events: AsyncIterator[Any],
    stream_fn: StreamFn,
    model: Model,
    context: Context,
    options: Any,
) -> RepairingStream:
    """``events`` wrapped so that a refusal of the request's shape is mended and sent again."""
    return RepairingStream(events, stream_fn, model, context, options)


def _kind(event: Any) -> str | None:
    return event.get("type") if isinstance(event, dict) else getattr(event, "type", None)


def _error_of(event: Any) -> AssistantMessage | None:
    error = event.get("error") if isinstance(event, dict) else getattr(event, "error", None)
    if isinstance(error, dict):
        error = AssistantMessage.model_validate(error)
    return error if isinstance(error, AssistantMessage) else None
