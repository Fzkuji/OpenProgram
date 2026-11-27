"""Which conversation the current model call belongs to.

The agent loop enters :func:`conversation_affinity` around each turn's
model call, so :mod:`.selection` can keep the conversation on the
credential it is bound to (``credential_binding`` in the session's
metadata) even when the request options carry no session id.

A :mod:`contextvars` variable, so parallel agents in one process each see
their own conversation and asyncio tasks inherit it by copy.

The call's usage context (``models/usage/context.py``) names the
conversation before the affinity does. Usage metering sits above
credentials, so :func:`call_conversation` asks it through a provider the
models layer registers when it loads (``openprogram.models.lower_layers``,
through ``models.part_ports``); without one the call names no conversation.
"""
from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Callable
from typing import Any, Iterator, Optional

from openprogram.models import part_ports

_affinity_key: contextvars.ContextVar[str] = contextvars.ContextVar(
    "openprogram.models.auth.affinity_key", default="")


def get_affinity_key() -> str:
    """The conversation the current model call belongs to, or ``""``."""
    return _affinity_key.get()


@contextlib.contextmanager
def conversation_affinity(key: Optional[str]) -> Iterator[None]:
    """Model calls inside the block belong to conversation ``key``."""
    token = _affinity_key.set(key or "")
    try:
        yield
    finally:
        _affinity_key.reset(token)


_providers: dict[str, Callable[..., Any]] = part_ports.filled(__name__)


def register(*, call_conversation: Callable[[], str]) -> None:
    """Install the reader of the conversation the running call's usage
    context names."""
    _providers.update(call_conversation=call_conversation)


def call_conversation() -> str:
    """The conversation the running call's usage context names — its parent
    conversation, its Goal's, its session — or ``""``."""
    reader = _providers.get("call_conversation")
    return (reader() or "") if reader else ""


__all__ = ["call_conversation", "conversation_affinity", "get_affinity_key", "register"]
