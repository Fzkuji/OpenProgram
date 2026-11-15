"""Single source of truth for which login methods each provider offers,
shared by every surface (CLI / web / TUI).

Borrows the opencode/openclaw shape: a declarative ``provider -> ordered list
of login methods``. The first entry is the default (what ``--method`` / the UI
pre-selects). Each surface renders these and dispatches the chosen method id to
the shared login driver (``auth/login/login_driver.py`` over ``auth/methods/``).

Keeping the map here — not inline in the CLI — is what lets web and TUI offer
the *same* logins instead of punting to "use the other surface". Providers with
a real native OAuth show ONLY that (one-button UX, matching the Codex CLI /
OpenClaw style); others offer import-from-the-vendor-CLI then an API key.

Method ids (handlers live in auth/methods/ and auth/login/):
  pkce_oauth       browser PKCE OAuth, loopback callback     — openai-codex
  device_code      browser device-code OAuth                 — github-copilot
  import_from_cli  copy/link an external CLI credential file — gemini-subscription
  api_key          paste a static API key
"""
from __future__ import annotations

# (method_id, human-readable label)
LoginMethod = tuple[str, str]

_API_KEY: LoginMethod = ("api_key", "Paste a static API key")

_METHODS: dict[str, list[LoginMethod]] = {
    "openai-codex":        [("pkce_oauth",  "Sign in with ChatGPT (opens browser)")],
    "github-copilot":      [("device_code", "Sign in with GitHub (opens browser)")],
    "anthropic":           [_API_KEY],
    "claude-code":         [],
    # gemini-subscription has no api_key_env, so the web ApiKey field never
    # renders and an api_key method would have no entry point there. Offer
    # import only, so CLI and web agree (use the Google provider for a key).
    "gemini-subscription": [("import_from_cli", "Import from ~/.gemini/oauth_creds.json")],
    "xai-subscription":    [
        ("pkce_oauth",  "Sign in with Grok (opens browser)"),
        ("import_from_cli", "Import from ~/.grok/auth.json"),
        ("paste_token", "Paste Grok access token"),
    ],
    # NOTE: qwen has a credential source but no provider runtime / catalog entry,
    # so it can't run models and never appears in the web provider list. Don't
    # advertise a login for it — CLI and web agree it isn't a usable provider.
}


def login_methods(provider: str) -> list[LoginMethod]:
    """Ordered login methods for ``provider``; the first is the default.
    Unknown / plain-key providers get API-key paste."""
    from openprogram.models.auth.provider_policy import UNSUPPORTED_PROVIDERS
    if provider in UNSUPPORTED_PROVIDERS:
        return []
    return list(_METHODS.get(provider, [_API_KEY]))


def default_method(provider: str) -> str:
    """The method id a surface should pre-select for ``provider``."""
    methods = login_methods(provider)
    if not methods:
        from openprogram.models.auth.types import AuthConfigError
        from openprogram.models.auth.provider_policy import API_KEY_REQUIRED
        raise AuthConfigError(API_KEY_REQUIRED)
    return methods[0][0]
