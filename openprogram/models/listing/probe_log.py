"""The requests OpenProgram sends a provider on its own behalf.

Outside any agent's turn, OpenProgram asks providers things by itself:
Settings' connectivity test, a credential's validation, a model list's
fetch, a quota read. Those left no trace, so a test that failed in a way its one-line
answer did not explain could not be looked into afterwards.

Each entry point is marked with :func:`probe` and the kind of test. Every
request sent under it, through a managed transport
(``safe_http.observing_requests``), is written down once as a row of the
usage ledger's ``probe_events`` table: provider, kind, method, origin and
path (never a query, which can carry a key), status or error, and how long
it took. These rows are never usage: they live apart from ``usage_events``
and count in no token or cost total. A model list that was answered is
left out, as lists are fetched by themselves whenever Settings opens.

When probes nest (a connectivity test runs a credential validation), the
outer kind names the request.

After magpie's probelog (yetone/magpie, MIT).
"""
from __future__ import annotations

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, TypeVar

from openprogram.host.security.safe_http import ObservedRequest, observing_requests

KIND_CONNECTIVITY = "connectivity"
KIND_CREDENTIAL = "credential"
KIND_MODEL_LIST = "model_list"
KIND_QUOTA = "quota"

_probing: ContextVar[str | None] = ContextVar("provider_probe_kind", default=None)

F = TypeVar("F", bound=Callable[..., Any])


def worth_keeping(kind: str, seen: ObservedRequest) -> bool:
    """Whether a probe request is written down: all but an answered model list."""
    answered = seen.status is not None and 200 <= seen.status < 300
    return not (kind == KIND_MODEL_LIST and answered)


@contextmanager
def probing(provider_id: str, kind: str) -> Iterator[None]:
    """Write down every request sent in this context as ``kind`` for ``provider_id``."""
    if _probing.get() is not None:  # the outer probe names it
        yield
        return

    def write(seen: ObservedRequest) -> None:
        if not worth_keeping(kind, seen):
            return
        from openprogram.models.usage.ledger import default_ledger

        default_ledger.append_probe_event(
            provider=provider_id,
            kind=kind,
            method=seen.method,
            url=seen.url,
            status=seen.status,
            elapsed_ms=seen.elapsed_ms,
            error=seen.error,
        )

    token = _probing.set(kind)
    try:
        with observing_requests(write):
            yield
    finally:
        _probing.reset(token)


def probe(kind: str) -> Callable[[F], F]:
    """Mark a function whose first argument is a provider id as a probe of ``kind``."""

    def decorate(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(provider_id: str, *args: Any, **kwargs: Any) -> Any:
            with probing(provider_id, kind):
                return fn(provider_id, *args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorate


__all__ = [
    "KIND_CONNECTIVITY",
    "KIND_CREDENTIAL",
    "KIND_MODEL_LIST",
    "KIND_QUOTA",
    "probe",
    "probing",
    "worth_keeping",
]
