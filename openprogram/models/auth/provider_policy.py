"""Which credentials and providers OpenProgram accepts.

A vendor is reached only through its official API with an official key, or,
for a subscription, by running the vendor's own program as an outside-agent
harness (``runtime/harnesses/``). OpenProgram never presents itself as an
official client, and never calls an internal endpoint with a subscription
sign-in (docs/reference/design/providers/auth/subscription-policy.html).
"""
from openprogram.models.auth.types import AuthConfigError

API_KEY_REQUIRED = (
    "Anthropic requires an API key. Unsupported provider or credential type."
)
UNSUPPORTED_PROVIDERS = frozenset({"claude-code", "claude-max", "claude_code", "claude-max-proxy", "claude_max_proxy", "claude_max"})

#: Catalogue ids reachable only by acting as an official client against a
#: vendor's internal endpoints. Never listed, offered or signed in; a saved
#: setting that names one is skipped like any provider that is not there.
WITHDRAWN_PROVIDERS = frozenset({
    "openai-codex", "github-copilot", "gemini-subscription", "google-gemini-cli",
    "gemini-cli", "xai-subscription",
})


def is_withdrawn(provider: str) -> bool:
    return provider in WITHDRAWN_PROVIDERS


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
