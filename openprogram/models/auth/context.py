"""Which conversation the current model call belongs to.

The agent loop enters :func:`conversation_affinity` around each turn's
model call, so :mod:`.selection` can keep the conversation on the
credential it is bound to (``credential_binding`` in the session's
metadata) even when the request options carry no session id.

A :mod:`contextvars` variable, so parallel agents in one process each see
their own conversation and asyncio tasks inherit it by copy.
"""
from __future__ import annotations

import contextlib
import contextvars
from typing import Iterator, Optional

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


__all__ = ["conversation_affinity", "get_affinity_key"]
