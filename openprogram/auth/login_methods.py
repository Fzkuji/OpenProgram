"""Single source of truth for which login methods each provider offers,
shared by every surface (CLI / web / TUI).

Borrows the opencode/openclaw shape: a declarative ``provider -> ordered list
of login methods``. The first entry is the default (what ``--method`` / the UI
pre-selects). Each surface renders these and dispatches the chosen method id to
the shared handlers (``auth/methods/`` + ``auth/cli.py`` ``_run_login_method``).

Keeping the map here — not inline in the CLI — is what lets web and TUI offer
the *same* logins instead of punting to "use the other surface". Providers with
a real native OAuth show ONLY that (one-button UX, matching the Codex CLI /
OpenClaw style); others offer import-from-the-vendor-CLI then an API key.

Method ids (handlers live in auth/methods/ and auth/cli.py):
  pkce_oauth       browser PKCE OAuth, loopback callback     — openai-codex
  device_code      browser device-code OAuth                 — github-copilot
  import_from_cli  copy/link an external CLI credential file — anthropic / gemini-subscription / qwen
  api_key          paste a static API key
"""
from __future__ import annotations

# (method_id, human-readable label)
LoginMethod = tuple[str, str]

_API_KEY: LoginMethod = ("api_key", "Paste a static API key")

_METHODS: dict[str, list[LoginMethod]] = {
    "openai-codex":        [("pkce_oauth",  "Sign in with ChatGPT (opens browser)")],
    "github-copilot":      [("device_code", "Sign in with GitHub (opens browser)")],
    "anthropic":           [("import_from_cli", "Import from Claude Code's ~/.claude/.credentials.json"), _API_KEY],
    "gemini-subscription": [("import_from_cli", "Import from ~/.gemini/oauth_creds.json"), _API_KEY],
    "qwen":                [("import_from_cli", "Import from ~/.qwen/oauth_creds.json"), _API_KEY],
}


def login_methods(provider: str) -> list[LoginMethod]:
    """Ordered login methods for ``provider``; the first is the default.
    Unknown / plain-key providers get API-key paste."""
    return list(_METHODS.get(provider, [_API_KEY]))


def default_method(provider: str) -> str:
    """The method id a surface should pre-select for ``provider``."""
    return login_methods(provider)[0][0]


def known_providers() -> list[str]:
    """Providers with an explicit (non-default) login-method list."""
    return list(_METHODS.keys())
