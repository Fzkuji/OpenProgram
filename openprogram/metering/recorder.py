"""UsageRecorder — turns a finished LLM call into a UsageEvent and appends
it to the ledger.

This is the single place that knows how to assemble an event from the
three inputs available at the stream.py chokepoint: the ``Model`` (carries
pricing), the final ``AssistantMessage`` (carries provider-reported
``Usage``), and the current ``UsageContext`` (carries the call source).

Everything here is BEST-EFFORT: a metering failure must never propagate
into the LLM response path. All public entry points swallow exceptions.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from .context import current_usage_context
from .event import UsageEvent
from .ledger import default_ledger

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
    try:
        from openprogram.providers.models import calculate_cost
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
            }, "model_catalog")
    except Exception:
        pass
    return ({
        "cost_input": 0.0, "cost_output": 0.0, "cost_cache_read": 0.0,
        "cost_cache_write": 0.0, "cost_total": 0.0,
    }, "unknown")


def record_message(model, message, *, session_id: Optional[str] = None,
                   token_source: str = "provider_usage") -> Optional[UsageEvent]:
    """Record one finished LLM call. ``model`` is the provider Model,
    ``message`` the final AssistantMessage. Returns the event (for callers
    that want a summary) or None if there was nothing to record.

    Never raises.
    """
    try:
        usage = getattr(message, "usage", None)
        if usage is None:
            return None
        inp = int(getattr(usage, "input", 0) or 0)
        out = int(getattr(usage, "output", 0) or 0)
        cr = int(getattr(usage, "cache_read", 0) or 0)
        cw = int(getattr(usage, "cache_write", 0) or 0)
        if not (inp or out or cr or cw):
            return None  # no tokens — nothing happened worth recording

        ctx = current_usage_context()
        cost, cost_source = _cost_from_model(model, usage)

        event = UsageEvent(
            ts=time.time(),
            session_id=session_id,
            parent_session_id=ctx.parent_session_id,
            agent_id=ctx.agent_id,
            call_kind=ctx.call_kind,
            call_label=ctx.call_label,
            provider=getattr(model, "provider", "") or "",
            api=getattr(model, "api", None),
            model_id=getattr(model, "id", "") or "",
            input_tokens=inp,
            output_tokens=out,
            cache_read_tokens=cr,
            cache_write_tokens=cw,
            total_tokens=inp + out,
            token_source=token_source,
            cost_source=cost_source,
            **cost,
        )
        default_ledger.append(event)
        for hook in _hooks:
            try:
                hook(event)
            except Exception:
                pass
        return event
    except Exception:
        return None


__all__ = ["record_message", "register_usage_hook"]
