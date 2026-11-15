"""Shared provider-registry scaffolding for tools with pluggable backends.

Some tools — web_search, image_generate — can talk to several third-party
services (Tavily, Exa, DuckDuckGo; OpenAI, Gemini, FAL). Each backend has:

  * a stable ``name`` the user picks in config / env
  * a set of env vars required to operate (``requires_env``)
  * a ``priority`` so we can pick the "best" one automatically when the
    user doesn't specify
  * an ``is_available()`` check (typically ``has_env(requires_env)``)

``ProviderRegistry`` is a thin generic container for these. Each tool
instantiates its own registry and provider files register into it; the
tool's ``execute`` asks the registry for the right backend at call time.

Design notes:

* We deliberately don't enforce a particular *interface* on providers
  here — a WebSearch provider has ``.search()`` while an ImageGen one
  has ``.generate()``. The registry only cares about discovery +
  availability. Tools type their own Protocol.
* Priority is descending (higher = tried first) so the palette file
  reads top-down in preference order.
* Duplicates silently overwrite. This is intentional so experimental
  local overrides (e.g. in tests) can shadow the builtin without
  stepping around registration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar, runtime_checkable


@runtime_checkable
class Provider(Protocol):
    """Minimal surface every backend must expose to the registry.

    Tool-specific extensions (``.search()``, ``.generate()``, …) live on
    subclasses — the registry doesn't look at them.
    """

    name: str
    priority: int
    requires_env: list[str]

    def is_available(self) -> bool: ...


P = TypeVar("P", bound=Provider)


class ProviderRegistry(Generic[P]):
    """Named registry of interchangeable backends for a single tool.

    Instantiate one per tool (``web_search_registry``, ``image_gen_registry``
    etc.) and have provider modules register into it at import time.
    """

    def __init__(self, kind: str) -> None:
        # ``kind`` is used purely for error messages so missing-provider
        # errors read "no image_generate provider is configured" rather
        # than "none is configured".
        self._kind = kind
        self._providers: dict[str, P] = {}

    def register(self, provider: P) -> P:
        """Register a provider. Returns it for easy decorator-style use."""
        self._providers[provider.name] = provider
        return provider

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)

    def get(self, name: str) -> P:
        """Look up by name. Raises ``KeyError`` if unknown."""
        return self._providers[name]

    def has(self, name: str) -> bool:
        return name in self._providers

    def all(self) -> list[P]:
        """Every registered provider, sorted by priority descending."""
        return sorted(self._providers.values(), key=lambda p: -p.priority)

    def available(self) -> list[P]:
        """Only the providers whose ``is_available()`` returns True."""
        return [p for p in self.all() if _safe_available(p)]

    def availability_problem(self, name: str) -> str | None:
        """Why provider ``name`` cannot run right now, or None when it can.

        The text is short and safe to show to the model and the user: it
        names missing environment variables, never their values.
        """
        p = self._providers.get(name)
        if p is None:
            return "not a registered provider"
        try:
            if p.is_available():
                return None
        except Exception as exc:
            return f"its availability check failed ({type(exc).__name__})"
        explain = getattr(p, "unavailable_reason", None)
        if callable(explain):
            try:
                reason = explain()
            except Exception:
                reason = None
            if reason:
                return str(reason)
        names = list(p.requires_env)
        missing = [e for e in names if not _env_set(e)] or names
        if missing:
            return f"not configured (needs {' / '.join(missing)})"
        return "not configured"

    def select(self, prefer: str | None = None) -> P:
        """Pick a provider: prefer one by name, else the highest-priority available.

        Raises ``LookupError`` (with a user-actionable message) when nothing
        usable is registered — tools should let this bubble up so the
        model sees a clear "no backend configured" error and can surface
        it to the user.
        """
        if prefer:
            if prefer in self._providers:
                p = self._providers[prefer]
                if _safe_available(p):
                    return p
                usable = [q.name for q in self.available()]
                raise LookupError(
                    f"{self._kind} provider {prefer!r} is registered but not available: "
                    f"{self.availability_problem(prefer)}. "
                    f"Usable now: {', '.join(usable) or 'none'}. "
                    f"Configure it, or omit the 'provider' arg to auto-select."
                )
            raise LookupError(
                f"{self._kind} provider {prefer!r} is not registered. "
                f"Registered: {sorted(self._providers)}"
            )
        avail = self.available()
        if avail:
            return avail[0]
        if not self._providers:
            raise LookupError(
                f"No {self._kind} provider is registered. This is a bug — "
                f"the tool module should have imported its builtins at init."
            )
        missing = {p.name: list(p.requires_env) for p in self.all()}
        raise LookupError(
            f"No {self._kind} provider is available. Add an API key for one "
            f"of the registered providers (Settings -> Providers, or "
            f"`openprogram providers login <provider> --api-key`): {missing}"
        )


def _env_set(name: str) -> bool:
    import os

    return bool(os.environ.get(name))


def _safe_available(p: Provider) -> bool:
    """Call ``p.is_available()`` with exceptions swallowed.

    A provider failing its own availability check (e.g. optional import
    blew up) must not take down the whole registry — we just treat it as
    unavailable and move to the next one.
    """
    try:
        return bool(p.is_available())
    except Exception:
        return False


@dataclass
class ProviderBase:
    """Small convenience base for providers that don't need custom init.

    Subclasses set ``name`` / ``priority`` / ``requires_env`` as class
    attributes and inherit a default ``is_available()`` that checks every
    env var listed in ``requires_env``.
    """

    name: str = ""
    priority: int = 0
    requires_env: list[str] = field(default_factory=list)

    def is_available(self) -> bool:
        return all(_env_set(e) for e in self.requires_env)


__all__ = [
    "Provider",
    "ProviderRegistry",
    "ProviderBase",
]
