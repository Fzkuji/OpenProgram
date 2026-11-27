"""
Provider API-key resolution outside a model request.

A provider's keys are its credentials (``models/auth/store.py``): the
keys added in Settings or the CLI, and a row for each of the provider's
standard environment variables that is set, read from the environment on
use and never copied. Model requests choose among them in
``models/auth/selection.py``; :func:`resolve_provider_key` gives the
callers outside a request (tools, probes, model listing) the key a
request would use now.

Amazon Bedrock and Google Vertex authenticate through the cloud SDK's own
credential chain (AWS SigV4 / GCP ADC) instead of a bearer key.
"""
from __future__ import annotations

import os
from pathlib import Path
import logging

_log = logging.getLogger(__name__)


def resolve_provider_key(provider: str) -> str | None:
    """The key (API key, environment variable or helper command) a request
    of ``provider`` would send now, or the ``"<authenticated>"`` sentinel
    for a satisfied Bedrock / Vertex chain, or ``None``. Sign-in tokens are
    left out: the callers put this value in a key header."""
    if provider == "amazon-bedrock":
        return "<authenticated>" if _bedrock_chain_ok() else None
    if provider == "google-vertex":
        return "<authenticated>" if _vertex_adc_ok() else None
    from openprogram.models.auth.resolver import resolve_store_api_key_sync
    from openprogram.models.auth.types import AuthCredentialProcessError
    try:
        return resolve_store_api_key_sync(provider)
    except AuthCredentialProcessError:
        # A configured helper that fails is an error the user must see.
        raise
    except Exception:
        _log.debug("key for %s not resolved", provider, exc_info=True)
        return None


# Providers whose credential is a cloud-credential chain (SigV4 / ADC), not a
# bearer key — resolve_provider_key returns the "<authenticated>" sentinel
# for them when the chain is satisfied.
_CLOUD_CRED_PROVIDERS = frozenset({"amazon-bedrock", "google-vertex"})


def env_vars_for(provider_id: str) -> list[str]:
    """The variables that hold ``provider_id``'s key, first preferred, from
    the provider's connection entry (``auth/login/login_method_registry.py``)."""
    from openprogram.models.auth.login.login_method_registry import env_vars
    return env_vars(provider_id)


def _bedrock_chain_ok() -> bool:
    return bool(
        os.environ.get("AWS_PROFILE")
        or (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"))
        or os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        or os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
        or os.environ.get("AWS_CONTAINER_CREDENTIALS_FULL_URI")
        or os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE")
    )


def _vertex_adc_ok() -> bool:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION")
    if not project or not location:
        return False
    explicit = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if explicit and Path(explicit).is_file():
        return True
    default_adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    return default_adc.is_file()


def is_configured(provider_id: str) -> bool:
    """True when the provider has working credentials — an AuthStore key OR a
    satisfied cloud-credential chain (Bedrock AWS chain / Vertex ADC)."""
    return resolve_provider_key(provider_id) is not None


def provider_id_for_env_var(env_var: str) -> str | None:
    """The provider a key variable belongs to (the first listed owner)."""
    from openprogram.models.auth.login.login_method_registry import provider_for_env_var
    return provider_for_env_var(env_var)

def resolve_api_key_with_auth_store(provider_id: str) -> str | None:
    """The bearer value a request of ``provider_id`` would use now, of any
    credential kind, or ``None`` (for checks and model listing)."""
    try:
        from openprogram.models.auth.resolver import resolve_api_key_sync
        return resolve_api_key_sync(provider_id)
    except Exception as resolve_exc:
        # No traceback: it could carry key material.
        _log.debug("stored key resolution failed for %s (%s)", provider_id, type(resolve_exc).__name__)
        return None
