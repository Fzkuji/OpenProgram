"""What the provider call path tells usage metering, and asks of it.

Usage metering (``openprogram.models.usage``) sits above the providers: it
reads their types, catalogue and errors, and the providers never import it.
The call path still reports to it at fixed moments — a reply's quota
headers, a finished or failed request, a credential's spent quota, a
switch to a fallback model — and asks it who is calling and what a Goal
allows. Those moments go through this port. The models layer fills it when
it loads (``openprogram.models.lower_layers``, through
``models.part_ports``); each behaviour imports its usage module when it is
called, so a test that replaces a usage function is what the providers see.

Without a registered behaviour each answer is the one of a process where
nothing is metered: observations and records do nothing, no quota is spent,
no call context is known, no Goal bounds a request and a prompt-cache check
finds nothing.
"""
from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any, ContextManager, Optional

from openprogram.models import part_ports

_providers: dict[str, Callable[..., Any]] = part_ports.filled(__name__)

BEHAVIOURS = frozenset({
    # Quota a reply carries, and a provider's spent quota.
    "observe_headers",
    "exhausted_report",
    "exhausted_limit",
    "after_failure",
    # Who is making the call.
    "call_context",
    "request_context",
    "usage_scope",
    # Recording a request.
    "build_message_event",
    "run_usage_hooks",
    "record_message",
    "record_failed_request",
    "record_route_switch",
    # A Goal's durable request receipt, and the prompt-cache check.
    "begin_request",
    "check_request",
    "settle_check",
})


def register(**providers: Callable[..., Any]) -> None:
    """Install usage metering's provider for each named behaviour."""
    unknown = set(providers) - BEHAVIOURS
    if unknown:
        raise ValueError(f"unknown usage behaviours: {sorted(unknown)}")
    _providers.update(providers)


def _call(name: str, default: Any, *args: Any, **kwargs: Any) -> Any:
    provider = _providers.get(name)
    return provider(*args, **kwargs) if provider else default


def observe_headers(provider: str, token: Optional[str], headers: Any, *,
                    account: Optional[str] = None) -> Any:
    """Record the limits a reply's headers carry. Never raises."""
    return _call("observe_headers", [], provider, token, headers, account=account)


def exhausted_report(provider: str, account: Optional[str] = None) -> Any:
    """``(account, spent limit, the key's other limits)`` for the spent limit
    that resets last, or None."""
    return _call("exhausted_report", None, provider, account)


def exhausted_limit(provider: str, account: Optional[str] = None) -> Any:
    """The spent limit of a provider's key that resets last, or None."""
    return _call("exhausted_limit", None, provider, account)


def after_failure(provider: str, exc: BaseException) -> None:
    """A call failed: read the provider's quota once if the failure was about it."""
    _call("after_failure", None, provider, exc)


def call_context() -> Any:
    """The running call's usage context with its execution, or None."""
    return _call("call_context", None)


def request_context(session_id: str | None = None) -> Any:
    """The running call's usage context with its Goal identity, or None."""
    return _call("request_context", None, session_id)


def usage_scope(**fields: Any) -> ContextManager[Any]:
    """The scope model calls made inside are counted under."""
    provider = _providers.get("usage_scope")
    return provider(**fields) if provider else contextlib.nullcontext()


def build_message_event(model: Any, message: Any, **kwargs: Any) -> Any:
    """The usage event a finished reply records, or None."""
    return _call("build_message_event", None, model, message, **kwargs)


def run_usage_hooks(event: Any) -> None:
    """Fire the hooks that follow a recorded event."""
    _call("run_usage_hooks", None, event)


def record_message(model: Any, message: Any, **kwargs: Any) -> Any:
    """Record one finished call."""
    return _call("record_message", None, model, message, **kwargs)


def record_failed_request(model: Any, partial: Any, **kwargs: Any) -> Any:
    """Record a request that reached the provider and ended without a final message."""
    return _call("record_failed_request", None, model, partial, **kwargs)


def record_route_switch(**fields: Any) -> None:
    """Keep a switch to a fallback model for the routing overview."""
    _call("record_route_switch", None, **fields)


def begin_request(model: Any, options: Any, **kwargs: Any) -> Any:
    """The Goal's durable receipt for this request, or None when no Goal bounds it."""
    return _call("begin_request", None, model, options, **kwargs)


def check_request(request: Any) -> Any:
    """Compare a captured request with its lane's last answered one, or None."""
    return _call("check_request", None, request)


def settle_check(check: Any, usage: Any) -> Any:
    """Add what the reply's usage says to ``check``."""
    return _call("settle_check", check, check, usage)


__all__ = sorted(BEHAVIOURS | {"BEHAVIOURS", "register"})
