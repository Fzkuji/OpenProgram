"""Ambient auth context — which profile/provider is "current" for this task.

Problem: tools run inside an agent's tool-call loop. When a tool like
``web_fetch`` / ``bash`` needs a credential, it shouldn't need the
caller to thread ``profile_id`` through every layer down to the helper.
But globals are wrong — one Python process can run many parallel agents,
each under a different profile, and a global would leak credentials
across them.

:mod:`contextvars` is the right primitive. Each async task inherits a
copy of the parent task's context automatically, so setting the context at
the top of a request (``async with auth_scope(profile="work"): …``)
propagates to every tool call inside without plumbing.

Values stored here:

  * ``active_profile_id`` — required. Identifies the :class:`Profile` the
    current task should consult for credential lookups. Defaults to
    ``"default"``.
  * ``active_provider_hint`` — optional. If the work in this scope is
    centered on one provider, setting it lets tools pick sensible
    fallbacks (e.g. a ``web_search`` tool needing an API key can fall back
    to whichever OpenAI key the active provider resolves to).
  * ``credential_overrides`` — dict used mostly by tests and
    dependency-injection callers that want to force a specific
    :class:`Credential` for a provider within this scope. Keyed by
    provider_id. Overrides the real pool pick.
  * ``subprocess_env_hook`` — callable producing an env dict for any
    subprocess the agent spawns inside this scope. Defaults to
    ``ProfileManager.subprocess_env`` once wired, None otherwise.

The :func:`auth_scope` async-context-manager is the only public way to
*enter* a scope. It guarantees the old values are restored on exit
regardless of exceptions — important so a crashed tool doesn't leave
the wrong profile active for whatever runs next.
"""
from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

from .profiles import DEFAULT_PROFILE_NAME
from .types import Credential


# ---------------------------------------------------------------------------
# ContextVars — the actual ambient state
# ---------------------------------------------------------------------------

_active_profile_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "openprogram.auth.active_profile_id",
    default=DEFAULT_PROFILE_NAME,
)
_active_provider_hint: contextvars.ContextVar[str] = contextvars.ContextVar(
    "openprogram.auth.active_provider_hint",
    default="",
)
_credential_overrides: contextvars.ContextVar[dict[str, Credential]] = contextvars.ContextVar(
    "openprogram.auth.credential_overrides",
    default={},
)
_subprocess_env_hook: contextvars.ContextVar[Optional[Callable[[], dict[str, str]]]] = \
    contextvars.ContextVar(
        "openprogram.auth.subprocess_env_hook",
        default=None,
    )


# ---------------------------------------------------------------------------
# Read-side API — what tools call
# ---------------------------------------------------------------------------

def get_active_profile_id() -> str:
    """Return the profile id for the current task. Always returns a
    non-empty string — defaults to ``"default"``."""
    return _active_profile_id.get()


def get_active_provider_hint() -> str:
    """Return the provider hint, or ``""`` if no hint is in effect.

    Tools should treat the empty string as "no preference" — not all
    scopes have a single dominant provider (e.g. a coordinator agent
    calling tools for two providers in turn)."""
    return _active_provider_hint.get()


def get_credential_override(provider_id: str) -> Optional[Credential]:
    """Test/DI hook — if the current scope forces a specific credential
    for ``provider_id``, return it. Otherwise ``None`` and the caller
    goes through normal pool resolution."""
    overrides = _credential_overrides.get()
    return overrides.get(provider_id)


def get_subprocess_env() -> Optional[dict[str, str]]:
    """Return the env dict the current scope wants subprocesses to use,
    or ``None`` if unset — the caller should then fall through to
    ``os.environ.copy()`` (i.e. "no profile isolation requested")."""
    hook = _subprocess_env_hook.get()
    if hook is None:
        return None
    return hook()


# ---------------------------------------------------------------------------
# Write-side API — how callers enter a scope
# ---------------------------------------------------------------------------

@dataclass
class AuthScope:
    """Declarative description of what to push into the context.

    Constructed separately from :func:`auth_scope` so callers can build it
    once (e.g. per-request middleware) and apply it to multiple tasks,
    or apply it synchronously via :meth:`apply_sync` inside threads that
    aren't using the asyncio loop.
    """

    profile_id: str = DEFAULT_PROFILE_NAME
    provider_hint: str = ""
    credential_overrides: dict[str, Credential] = field(default_factory=dict)
    subprocess_env_hook: Optional[Callable[[], dict[str, str]]] = None


@contextlib.contextmanager
def auth_scope(
    *,
    profile_id: str = DEFAULT_PROFILE_NAME,
    provider_hint: str = "",
    credential_overrides: Optional[dict[str, Credential]] = None,
    subprocess_env_hook: Optional[Callable[[], dict[str, str]]] = None,
) -> Iterator[AuthScope]:
    """Enter an auth scope for the duration of a ``with`` block.

    Works correctly for both sync and async code — ``contextvars``
    propagates into :func:`asyncio.create_task` children by copy, so
    spawning sub-tasks inside the block retains the scope. This sync
    context-manager form is the canonical entrypoint; :func:`auth_scope_async`
    exists only for symmetry with the rest of the async codebase.
    """
    overrides = dict(credential_overrides or {})
    tokens = [
        _active_profile_id.set(profile_id),
        _active_provider_hint.set(provider_hint),
        _credential_overrides.set(overrides),
        _subprocess_env_hook.set(subprocess_env_hook),
    ]
    try:
        yield AuthScope(
            profile_id=profile_id,
            provider_hint=provider_hint,
            credential_overrides=overrides,
            subprocess_env_hook=subprocess_env_hook,
        )
    finally:
        # Reset in reverse order — symmetric with set() order.
        _subprocess_env_hook.reset(tokens[3])
        _credential_overrides.reset(tokens[2])
        _active_provider_hint.reset(tokens[1])
        _active_profile_id.reset(tokens[0])


@contextlib.asynccontextmanager
async def auth_scope_async(**kwargs: Any):
    """Async variant of :func:`auth_scope`.

    Identical semantics — included because async code bases expect
    ``async with``. No actual awaiting happens; this is a thin wrapper so
    callers don't have to remember which flavor applies.
    """
    with auth_scope(**kwargs) as scope:
        yield scope


# ---------------------------------------------------------------------------
# Low-level — for integration with asyncio.Task / threading
# ---------------------------------------------------------------------------

def capture() -> contextvars.Context:
    """Capture the current context for later replay.

    Use when spawning a worker thread or queue-driven task that must see
    the caller's auth scope: pass this captured context to
    :meth:`contextvars.Context.run`. ``asyncio.create_task`` already does
    this automatically — you only need to call ``capture`` for non-asyncio
    paths."""
    return contextvars.copy_context()


__all__ = [
    "AuthScope",
    "auth_scope",
    "auth_scope_async",
    "get_active_profile_id",
    "get_active_provider_hint",
    "get_credential_override",
    "get_subprocess_env",
    "capture",
]
