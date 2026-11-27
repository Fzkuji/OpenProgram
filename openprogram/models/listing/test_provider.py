"""Connectivity probe — the one-shot "PING" behind the Settings →
Provider → Connectivity check button.

Credential validation now lives in ``credentials.validate_credential`` — a
model-independent auth probe (``GET /key`` / ``GET /models`` / ``x-api-key
/v1/models`` / ``?key=`` / CredentialProvider) with an inference ping only as layer 2
when a model is named. ``test_provider`` is a thin adapter that delegates to it
and reshapes the result into the legacy ``{ok, latency_ms, model?, note?,
error?}`` dict the React ``Connectivity`` component reads. See
``docs/reference/design/providers/auth/credential-validation-unification.html``.
"""
from __future__ import annotations

from typing import Any

from openprogram.models.usage.probe_log import KIND_CONNECTIVITY, probe


@probe(KIND_CONNECTIVITY)
def test_provider(
    provider_id: str,
    model: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """One-shot connectivity check, through the unified validator."""
    from .credentials import validate_credential

    return validate_credential(
        provider_id, model=model, timeout=timeout, use_cache=False
    ).to_legacy()

