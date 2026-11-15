"""UsageRecorder — turns a finished LLM call into a UsageEvent and appends
it to the ledger.

This is the single place that knows how to assemble an event from the
three inputs available at the stream.py chokepoint: the ``Model`` (carries
pricing), the final ``AssistantMessage`` (carries provider-reported
``Usage``), and the current ``UsageContext`` (carries the call source).

Legacy record_message and publish hooks are best-effort. Event construction
is strict for callers that durably settle a budget or Goal request receipt.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from .context import call_context
from .event import CALL_KIND_DIRECT, UsageEvent
from .ledger import default_ledger
import logging

_log = logging.getLogger(__name__)

# post-record hooks (budget/alerting plugins subscribe; see design §9).
_hooks: list[Callable[[UsageEvent], None]] = []


def register_usage_hook(fn: Callable[[UsageEvent], None]) -> None:
    """Subscribe to every recorded event (after it's persisted). Hooks run
    best-effort inside the recorder's try/except — a throwing hook never
    breaks recording or the LLM call."""
    _hooks.append(fn)


def _cost_from_model(model, usage) -> tuple[dict, str]:
    """Return (cost dict, cost_source). Uses the catalog's per-MTok pricing
    via providers.models.calculate_cost. Falls back to a provider-reported
    cost if the model carries one and the catalog can't price it."""
    reported = getattr(usage, "provider_cost_usd", None)
    if isinstance(reported, (int, float)) and reported >= 0:
        return ({
            "cost_input": 0.0, "cost_output": 0.0, "cost_cache_read": 0.0,
            "cost_cache_write": 0.0, "cost_total": float(reported),
        }, "provider_reported")
    if (getattr(usage, "service_tier", None) in ("priority", "fast")
            or (getattr(usage, "requested_service_tier", None) in ("priority", "fast")
                and getattr(usage, "service_tier", None) is None)):
        return ({
            "cost_input": 0.0, "cost_output": 0.0, "cost_cache_read": 0.0,
            "cost_cache_write": 0.0, "cost_total": 0.0,
        }, "unknown")
    cost = getattr(model, "cost", None)
    if cost is None or not getattr(cost, "is_known", lambda: False)():
        return ({
            "cost_input": 0.0, "cost_output": 0.0, "cost_cache_read": 0.0,
            "cost_cache_write": 0.0, "cost_total": 0.0,
        }, "unknown")
    try:
        from openprogram.models.providers.catalog.models import calculate_cost
        # calculate_cost mutates usage.cost AND returns total; we read the
        # populated UsageCost off the usage object afterwards.
        calculate_cost(model, usage)
        c = usage.cost
        if c is not None:
            return ({
                "cost_input": float(c.input or 0.0),
                "cost_output": float(c.output or 0.0),
                "cost_cache_read": float(c.cache_read or 0.0),
                "cost_cache_write": float(c.cache_write or 0.0),
                "cost_total": float(c.total or 0.0),
            }, str(cost.source))
    except Exception:
        # The row is still recorded, with cost "unknown".
        _log.warning("cost calculation failed for %s", getattr(model, "id", model), exc_info=True)
    return ({
        "cost_input": 0.0, "cost_output": 0.0, "cost_cache_read": 0.0,
        "cost_cache_write": 0.0, "cost_total": 0.0,
    }, "unknown")


def build_message_event(
    model, message, *, session_id: Optional[str] = None,
    token_source: str = "provider_usage",
    context=None, request_id: str | None = None, timestamp: float | None = None,
    prefix=None,
) -> Optional[UsageEvent]:
    """Assemble the UsageEvent for one finished LLM call without storing it.

    Budgeted calls need the event and its append to happen inside the
    governor's settle transaction, so building is separate from appending.
    Legacy callers skip absent/zero counters. A request identity preserves
    known zero and separate token/cost availability. Raises on malformed
    request counters rather than silently under-billing. ``prefix`` is the
    request's prompt-cache check (``prefix_fingerprint.PrefixCheck``).
    """
    usage = getattr(message, "usage", None)
    if usage is None:
        return None
    inp = int(getattr(usage, "input", 0) or 0)
    out = int(getattr(usage, "output", 0) or 0)
    cr = int(getattr(usage, "cache_read", 0) or 0)
    cw = int(getattr(usage, "cache_write", 0) or 0)
    if not (inp or out or cr or cw) and request_id is None:
        return None  # no tokens — nothing happened worth recording

    ctx = context or call_context()
    reported_total = int(getattr(usage, "total_tokens", 0) or 0)
    known = bool(inp or out or cr or cw or reported_total) or bool(getattr(usage, "tokens_reported", False))
    if request_id is not None:
        known = reported_total > 0 or bool(getattr(usage, "tokens_reported", False))
    if request_id is not None and min(inp, out, cr, cw, reported_total) < 0:
        raise ValueError("Provider reported negative token counts")
    cost, cost_source = _cost_from_model(model, usage)
    if request_id is not None and getattr(usage, "provider_cost_usd", None) is None and (
            not getattr(usage, "tokens_reported", False) or reported_total > inp + out + cr + cw):
        # A total without its billable components proves tokens, not price.
        cost_source = "unknown"
    if not known and getattr(usage, "provider_cost_usd", None) is None:
        cost_source = "unknown"
        if not (inp or out or cr or cw):
            cost = {key: 0.0 for key in cost}
    # contextvar session wins (set by the turn's usage_scope) so a
    # compaction/summary call inside the turn attributes to the same
    # session even when its own options carried no session_id.
    eff_session = ctx.session_id or session_id

    return UsageEvent(
        **({"event_id": request_id} if request_id else {}),
        ts=time.time() if timestamp is None else timestamp,
        request_id=request_id, goal_id=ctx.goal_id, goal_revision=ctx.goal_revision,
        goal_session_id=ctx.goal_session_id, execution_id=ctx.execution_id, tokens_known=known,
        session_id=eff_session,
        parent_session_id=ctx.parent_session_id,
        agent_id=ctx.agent_id,
        call_kind=ctx.call_kind or CALL_KIND_DIRECT,
        call_label=ctx.call_label,
        provider=getattr(model, "provider", "") or "",
        api=getattr(model, "api", None),
        model_id=getattr(model, "id", "") or "",
        input_tokens=inp,
        output_tokens=out,
        cache_read_tokens=cr,
        cache_write_tokens=cw,
        # Every token the request carried, cached ones included, as a governed
        # request has always counted them. Adapters report ``input`` without
        # its cached part, so ``input + output`` alone undercounted any reply
        # with a cache hit (DeepSeek's cache reads exceeded its "total").
        total_tokens=(reported_total or inp + out + cr + cw) if request_id
        else max(reported_total, inp + out + cr + cw),
        token_source=token_source,
        cost_source=cost_source,
        first_token_ms=getattr(usage, "first_token_ms", None),
        first_text_ms=getattr(usage, "first_text_ms", None),
        **({} if prefix is None else prefix.event_fields()),
        **cost,
    )


def run_usage_hooks(event: UsageEvent) -> None:
    """Fire post-record hooks. Best-effort: a throwing hook is contained."""
    if event.goal_session_id or event.session_id:
        try:
            from openprogram.models.usage.goal_port import refresh_usage
            refresh_usage(event.goal_session_id or event.session_id)
        except Exception:
            # Metering is durable already; the chat boundary retries projection.
            _log.debug(
                "goal usage refresh failed for session %s",
                event.goal_session_id or event.session_id, exc_info=True)
    for hook in _hooks:
        try:
            hook(event)
        except Exception:
            # A throwing hook is contained; the usage row is already durable.
            _log.warning("usage hook %r failed", hook, exc_info=True)


def record_message(model, message, *, session_id: Optional[str] = None,
                   token_source: str = "provider_usage", prefix=None) -> Optional[UsageEvent]:
    """Record one finished LLM call. ``model`` is the provider Model,
    ``message`` the final AssistantMessage. Returns the event (for callers
    that want a summary) or None if there was nothing to record.

    Never raises. Unbudgeted calls keep this best-effort contract.
    """
    try:
        event = build_message_event(
            model, message, session_id=session_id, token_source=token_source, prefix=prefix,
        )
        if event is None:
            return None
        default_ledger.append(event)
        run_usage_hooks(event)
        return event
    except Exception:
        return None


def record_failed_request(model, partial=None, *, session_id: Optional[str] = None) -> Optional[UsageEvent]:
    """Record a request that reached the provider and ended without a final message.

    A reply that stalled or whose connection dropped may still be billed.
    ``partial`` is the last partial message the reply streamed, if any: the
    tokens it reported are recorded as they are. With none reported the
    request is still counted, as one event whose tokens and cost are
    unknown, so a summary shows the request and says its cost is not known
    instead of leaving it out. Never raises.
    """
    try:
        usage = getattr(partial, "usage", None)
        if usage is not None and (usage.input or usage.output or usage.cache_read or usage.cache_write):
            return record_message(model, partial, session_id=session_id)
        ctx = call_context()
        event = UsageEvent(
            ts=time.time(), tokens_known=False, cost_source="unknown",
            session_id=ctx.session_id or session_id,
            parent_session_id=ctx.parent_session_id, agent_id=ctx.agent_id,
            goal_id=ctx.goal_id, goal_revision=ctx.goal_revision,
            goal_session_id=ctx.goal_session_id, execution_id=ctx.execution_id,
            call_kind=ctx.call_kind or CALL_KIND_DIRECT, call_label=ctx.call_label,
            provider=getattr(model, "provider", "") or "", api=getattr(model, "api", None),
            model_id=getattr(model, "id", "") or "",
        )
        default_ledger.append(event)
        run_usage_hooks(event)
        return event
    except Exception:
        _log.warning("failed request not recorded for %s", getattr(model, "id", model), exc_info=True)
        return None


__all__ = [
    "build_message_event", "record_failed_request", "record_message", "register_usage_hook",
    "run_usage_hooks",
]
