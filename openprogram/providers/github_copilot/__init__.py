"""GitHub Copilot auth adapter + helpers.

No provider stream module lives here — Copilot stream traffic routes
through :mod:`openai_responses` with custom headers. This package exists
purely to centralize the GitHub-OAuth / device-code bookkeeping under
:mod:`openprogram.auth`.
"""
from __future__ import annotations

from .auth_adapter import (
    PROVIDER_ID,
    import_from_env_tokens,
    register_github_copilot_auth,
)

__all__ = [
    "PROVIDER_ID",
    "import_from_env_tokens",
    "register_github_copilot_auth",
]
