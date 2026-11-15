"""Anthropic API-key authentication."""
from typing import Any, Optional
from openprogram.models.auth.credential_provider import ProviderAuthConfig, register_provider_config
from openprogram.models.auth.types import Credential, CredentialData
from openprogram.models.auth.provider_policy import require_api_key

PROVIDER_ID = "anthropic"


def import_api_key(
    api_key: str,
    *,
    account_id: str = "default",
    metadata: Optional[dict[str, Any]] = None,
) -> Credential:
    """Wrap a pasted ANTHROPIC_API_KEY as a :class:`Credential`.

    Doesn't register it with the store — callers do that themselves via
    :meth:`AuthStore.add_credential`. Exists so every path that produces
    an Anthropic credential funnels through the same type construction,
    with uniform metadata.
    """
    require_api_key(api_key)
    md = {"imported_from": "paste"} | (metadata or {})
    return Credential(
        provider_id=PROVIDER_ID,
        account_id=account_id,
        kind="api_key",
        payload=CredentialData(kind="api_key", auth_value=api_key.strip()),
        source="anthropic_paste",
        metadata=md,
        read_only=False,
    )






def _anthropic_refresh(cred: Credential) -> Credential:
    require_api_key(cred.payload.auth_value, is_subscription=cred.kind != "api_key")
    return cred


def register_anthropic_auth() -> None:
    register_provider_config(ProviderAuthConfig(
        provider_id=PROVIDER_ID, refresh=_anthropic_refresh, async_refresh=None,
    ))


register_anthropic_auth()
