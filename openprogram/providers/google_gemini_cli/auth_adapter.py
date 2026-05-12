"""Google Gemini CLI auth adapter.

Gemini CLI (``@google/gemini-cli``) keeps OAuth state at
``~/.gemini/oauth_creds.json`` with shape:

  {
    "access_token": "ya29...",
    "refresh_token": "1//...",
    "scope": "https://www.googleapis.com/auth/cloud-platform openid email",
    "token_type": "Bearer",
    "id_token": "eyJ...",
    "expiry_date": 1712345678901     # unix ms
  }

Google rotates access_tokens hourly but refresh_tokens are long-lived.
We adopt the file as a delegated credential so Gemini CLI rotations
propagate to us for free. Refreshing the access_token ourselves would
require Google's OAuth client_id/secret, which the CLI embeds — using
it from our process would violate Google's terms. So: stay delegated,
point users at ``gemini auth login`` when the CLI's token goes stale.

Env-var route: ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` produce an
``api_key`` credential via :mod:`auth.sources.env`; no adapter work
needed beyond registering the provider id.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from openprogram.auth.manager import (
    ProviderAuthConfig,
    register_provider_config,
)
from openprogram.auth.types import (
    CliDelegatedPayload,
    Credential,
)


PROVIDER_ID = "gemini-subscription"


def gemini_cli_credentials_path() -> Path:
    return Path.home() / ".gemini" / "oauth_creds.json"


def import_from_gemini_cli(
    *,
    profile_id: str = "default",
    path: Optional[Path] = None,
) -> Optional[Credential]:
    """Read Gemini CLI's OAuth file; return a delegated credential or None."""
    target = Path(path) if path else gemini_cli_credentials_path()
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not data.get("access_token"):
        return None

    metadata: dict[str, Any] = {
        "imported_from": "gemini_cli",
        "source_path": str(target),
    }
    if data.get("scope"):
        metadata["scope"] = data["scope"]
    if data.get("token_type"):
        metadata["token_type"] = data["token_type"]

    return Credential(
        provider_id=PROVIDER_ID,
        profile_id=profile_id,
        kind="cli_delegated",
        payload=CliDelegatedPayload(
            store_path=str(target),
            access_key_path=["access_token"],
            refresh_key_path=["refresh_token"],
            expires_key_path=["expiry_date"],
        ),
        source="gemini_cli_import",
        metadata=metadata,
        read_only=True,
    )


def register_gemini_cli_auth() -> None:
    register_provider_config(
        ProviderAuthConfig(
            provider_id=PROVIDER_ID,
            refresh_skew_seconds=60,
            refresh=None,
            async_refresh=None,
        )
    )


register_gemini_cli_auth()


__all__ = [
    "PROVIDER_ID",
    "gemini_cli_credentials_path",
    "import_from_gemini_cli",
    "register_gemini_cli_auth",
]
