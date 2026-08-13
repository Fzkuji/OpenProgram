"""ProviderAuthAdapter — the contract every provider's auth_adapter.py obeys.

Each provider with authenticated HTTP traffic ships an ``auth_adapter``
module that plugs its credential semantics into :mod:`openprogram.auth`.
Until now the shape was enforced by code review; this file pins it down.

Four things every adapter does:

1. Export a ``PROVIDER_ID`` string matching the registry key.
2. Call :func:`register_provider_config` at import time so
   :class:`AuthManager` knows about the provider (refresh fn, fallback
   chain, cooldowns). The adapter chooses what to pass.
3. Optionally provide an ``import_from_<source>`` function that reads
   an external credential store (the vendor CLI's auth.json, an env
   var, a keyring entry) and returns a :class:`Credential` the store
   can adopt on first use.
4. Optionally provide refresh helpers the registered config can point at.

The :class:`ProviderAuthAdapter` Protocol below describes (1)+(3) — the
parts a Runtime's ``_ensure_credential`` helper talks to. (2) and (4)
are side effects / dependency injection, not part of the call surface,
so they stay implicit.

Conformance check: :func:`validate_adapter` walks a module and confirms
it exposes the required attributes. Tests enumerate the known adapter
modules and call this, so renames / removals break CI instead of users.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from .types import Credential


@runtime_checkable
class ProviderAuthAdapter(Protocol):
    """Structural type for a provider auth_adapter module.

    Adapters are **modules**, not classes — `runtime_checkable` lets us
    use ``isinstance(module, ProviderAuthAdapter)`` to validate the
    shape at test time.

    - ``PROVIDER_ID``: matches the key the adapter registers with
      :func:`openprogram.auth.manager.register_provider_config`. Use
      this same string in :class:`Credential.provider_id`.
    - ``import_from_external``: read whatever external credential store
      this provider's CLI / extension populates, and return a
      :class:`Credential` ready to seed a pool. Return ``None`` when the
      external source is absent / unusable; the caller should surface a
      "log in first" error rather than crashing.

    Each adapter can expose additional helpers (JWT decode, endpoint
    discovery, env-var probes). Those stay provider-specific.
    """

    PROVIDER_ID: str

    def import_from_external(
        self, *, profile_id: str = "default",
    ) -> Optional[Credential]:
        ...


class AdapterConformanceError(Exception):
    """Raised by :func:`validate_adapter` for a bad adapter module."""


def validate_adapter(module, *, require_import: bool = False) -> None:
    """Assert that ``module`` looks like a ProviderAuthAdapter.

    - ``PROVIDER_ID`` must be a non-empty string attribute.
    - If the adapter offers a file-import path, it must be reachable as
      either ``import_from_external`` or ``import_from_<source>``. The
      latter is the legacy-looking shape (``import_from_codex_file``,
      ``import_from_gemini_cli``, ...); we accept either, but the
      Protocol itself speaks the canonical name.
    - When ``require_import=True``, at least one import helper must
      exist — use this for providers whose only credential source is
      external (e.g. subscription-only CLIs).

    Raises :class:`AdapterConformanceError` with a human-readable
    message on failure. Returns ``None`` on success.
    """
    pid = getattr(module, "PROVIDER_ID", None)
    if not isinstance(pid, str) or not pid:
        raise AdapterConformanceError(
            f"{module.__name__}: PROVIDER_ID must be a non-empty string"
        )

    has_canonical = callable(getattr(module, "import_from_external", None))
    import_candidates = [
        name for name in dir(module)
        if name.startswith("import_from_") and callable(getattr(module, name))
    ]
    if require_import and not (has_canonical or import_candidates):
        raise AdapterConformanceError(
            f"{module.__name__}: no import_from_external / "
            "import_from_<source> helper found"
        )


__all__ = [
    "ProviderAuthAdapter",
    "AdapterConformanceError",
    "validate_adapter",
]
