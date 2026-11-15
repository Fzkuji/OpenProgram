"""Anthropic credential requirements."""
from openprogram.models.auth.types import AuthConfigError

API_KEY_REQUIRED = (
    "Anthropic requires an API key. Unsupported provider or credential type."
)
UNSUPPORTED_PROVIDERS = frozenset({"claude-code", "claude-max", "claude_code", "claude-max-proxy", "claude_max_proxy", "claude_max"})


def require_api_key(key: str, *, is_subscription: bool = False) -> None:
    """Reject subscription credentials, including tokens pasted as API keys."""
    if is_subscription or "sk-ant-oat" in key:
        raise AuthConfigError(API_KEY_REQUIRED)


def require_supported_provider(provider: str) -> None:
    if provider in UNSUPPORTED_PROVIDERS:
        raise AuthConfigError(API_KEY_REQUIRED)


def require_api_headers(headers: dict) -> None:
    for name, value in headers.items():
        if name.lower() in {"authorization", "x-api-key"}:
            require_api_key(str(value))
