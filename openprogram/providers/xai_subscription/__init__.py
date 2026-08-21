"""xAI Grok subscription (SuperGrok / X Premium+ OAuth)."""

from openprogram.providers.xai_subscription.auth_adapter import (
    PROVIDER_ID,
    build_pkce_config,
    register_xai_subscription_auth,
)

__all__ = [
    "PROVIDER_ID",
    "build_pkce_config",
    "register_xai_subscription_auth",
]
