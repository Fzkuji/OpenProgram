"""Which way of a company a request goes through.

A request goes through the way its model was picked on. An API key is its
company's and every way of the company lists it, so the way picked can be one
the key does not work on: a MiniMax key of the China plan picked on the
international API. When that way has refused every key it has, the request is
sent again, for the same model, through another way of the company that lists
one of those keys and has not refused it (``company_keys.ways_for_refused_keys``),
and uses there only those keys, never another credential of that way.

Only a refusal moves a request. A limit, a spent quota or a server error stays
on the way picked: moving those could spend another plan's money. A request
whose conversation pins a credential, or that carries its own key, is not
moved. The reply says once per way which way answered; after that the picked
way fails before any request (its keys are marked refused) and the move costs
nothing.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable

#: (picked way, way that answered) pairs already said on a reply.
_said: set[tuple[str, str]] = set()


def _keys_refused(exc: BaseException) -> bool:
    """``exc`` is "no credential of this way can be used", every one of them
    refused (not waiting, not pinned)."""
    from openprogram.models.providers.utils.failures.errors import ErrorReason, LLMError
    failure = getattr(exc, "failure", None)
    return (isinstance(exc, LLMError) and isinstance(failure, dict)
            and failure.get("cause") == "credentials_unavailable" and not failure.get("pinned")
            and exc.reason == ErrorReason.AUTHENTICATION)


def _next_way(model: Any, tried: set[str]):
    """The same model on the next way of the company to try, with the ids
    of the keys the request follows there, or ``None``."""
    from openprogram.models.auth.company_keys import ways_for_refused_keys
    from openprogram.models.auth.selection import canonical_provider
    from openprogram.models.providers.catalog.enabled_models import use_model_in_process
    from openprogram.models.providers.catalog.models import get_model
    picked = canonical_provider(model.provider or "")
    for way, keys in ways_for_refused_keys(picked, model.id, frozenset(tried)):
        # The model need not be enabled on the other way: its row is read
        # from that way's list for this process, and nothing is enabled.
        if use_model_in_process(way, model.id):
            return get_model(way, model.id), keys
    return None


def _label(provider: str) -> str:
    from openprogram.models.auth.login.login_method_registry import company_of
    found = company_of(provider)
    return f"{found[0].label} {found[1].short}" if found is not None else provider


def _event_kind(event: Any):
    return getattr(event, "type", None) or (event.get("type") if isinstance(event, dict) else None)


async def routed(model: Any, send: Callable[[Any, Any], AsyncIterator[Any]]) -> AsyncIterator[Any]:
    """``send(model, None)``'s events; when the way refuses every key before
    anything is shown, ``send(other, keys)``: the same model on another way
    of the company, with only the keys the request follows there."""
    from openprogram.models.providers.types import AssistantMessage, EventRetry, EventStart
    current, keys = model, None
    tried = {model.provider}
    started = False
    moves = 0
    while True:
        shown = False
        try:
            async for event in send(current, keys):
                kind = _event_kind(event)
                if kind == "start":
                    if started:
                        continue
                    started = True
                elif kind != "retry":
                    shown = True
                yield event
            return
        except Exception as exc:
            if shown or not _keys_refused(exc):
                raise
            try:
                found = await asyncio.to_thread(_next_way, model, tried)
            except Exception:
                found = None
            if found is None:
                raise
            other, next_keys = found
            tried.add(other.provider)
            moves += 1
            pair = (current.provider, other.provider)
            if pair not in _said:
                _said.add(pair)
                if not started:
                    started = True
                    yield EventStart(partial=AssistantMessage.empty(model))
                to, source = _label(other.provider), _label(current.provider)
                yield EventRetry(attempt=moves, reason="credential",
                                 error=f"Switched to {to}: the key is refused on {source}",
                                 credential={"to": to, "from": source, "cause": "auth"})
            current, keys = other, next_keys


__all__ = ["routed"]
