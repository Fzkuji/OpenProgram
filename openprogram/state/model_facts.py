"""What the models layer tells the state layer.

State sits below ``openprogram.models`` and never imports it. A few state
readers still need a fact only the models layer has: whether each provider
is configured (the settings list's read-only Providers rows), how a model
call's failure classifies (the memory writer's failure taxonomy) and the
usage scope a memory write's model calls are counted under. The models
layer registers one provider per fact here when it loads
(``openprogram.models.lower_layers``, installed by the root package); the
functions below ask for it.

Without a registered provider each answer is what it is in a process where
no model was ever reached: no providers, nothing to classify (``unknown``,
not retryable) and no usage to count.
"""
from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any, ContextManager

_providers: dict[str, Callable[..., Any]] = {}

FACTS = frozenset({
    "provider_status",
    "classify_failure",
    "usage_scope",
})

#: The failure reason ``classify_failure`` gives when it cannot tell.
UNKNOWN_REASON = "unknown"


def register(**providers: Callable[..., Any]) -> None:
    """Install the models layer's provider for each named fact."""
    unknown = set(providers) - FACTS
    if unknown:
        raise ValueError(f"unknown model facts: {sorted(unknown)}")
    _providers.update(providers)


def provider_status() -> dict[str, dict[str, Any]]:
    """Each provider's ``{"available": bool, "method": str}``, by name."""
    provider = _providers.get("provider_status")
    return dict(provider()) if provider else {}


def classify_failure(exc: BaseException) -> tuple[str, bool]:
    """A model call failure's reason (an ``ErrorReason`` value) and whether
    retrying it can succeed."""
    provider = _providers.get("classify_failure")
    return provider(exc) if provider else (UNKNOWN_REASON, False)


def usage_scope(*, call_kind: str, session_id: str) -> ContextManager[Any]:
    """The scope model calls made inside are counted under."""
    provider = _providers.get("usage_scope")
    return provider(call_kind=call_kind, session_id=session_id) if provider else contextlib.nullcontext()


__all__ = [
    "FACTS",
    "UNKNOWN_REASON",
    "classify_failure",
    "provider_status",
    "register",
    "usage_scope",
]
