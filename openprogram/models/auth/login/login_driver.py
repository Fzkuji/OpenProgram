"""Surface-agnostic login driver.

Given ``(provider, method, LoginUi)`` it runs the login and returns a
``Credential``; :func:`persist` adds it to the provider's list (a sign-in of
an account already listed renews that row). Every surface — the CLI terminal, the web UI, the TUI — supplies
its OWN ``LoginUi`` (forwarding ``open_url`` / ``prompt`` / ``show_progress`` /
``show_code`` to its frontend), so the flow logic lives here exactly once.

This is the piece that lets web and TUI offer the same native logins the CLI
has, instead of punting to "use the other surface". The method ids come from
``openprogram/models/auth/login/login_method_registry.py``:

  api_key          paste a static key (prompted through the ui if not supplied)
  import_from_cli  copy/link an existing vendor-CLI credential (no interaction)
"""
from __future__ import annotations

from typing import Optional

from ..types import AuthConfigError
from ..types import Credential
from ..types import LoginUi


async def run_login(
    provider: str,
    method: str,
    ui: LoginUi,
    *,
    api_key: Optional[str] = None,
    label: str = "",
    source: str = "web",
) -> Credential:
    """Run ``method`` for ``provider`` through ``ui`` and return the credential."""
    from openprogram.models.auth.provider_policy import (
        UNSUPPORTED_PROVIDERS, API_KEY_REQUIRED,
    )
    if provider in UNSUPPORTED_PROVIDERS or (provider == "anthropic" and method != "api_key"):
        raise AuthConfigError(API_KEY_REQUIRED)
    from .credential_login import api_key_credential, import_from_cli

    if method == "api_key":
        key = (api_key or "").strip() or (await ui.prompt("Paste the API key", secret=True)).strip()
        cred = api_key_credential(provider, key)

    elif method == "import_from_cli":
        await ui.show_progress(f"Importing your existing {provider} login…")
        cred = import_from_cli(provider)

    else:
        raise AuthConfigError(f"unsupported login method: {method!r}")

    from ..account.account_labels import apply_account_label

    if cred.source != "import":
        cred.source = source
    return apply_account_label(cred, label)


def persist(cred: Credential) -> Credential:
    """Add a freshly obtained credential to the provider's list and return
    the row as stored: a sign-in of an account already listed (same email
    or account id) renews that row in place instead of adding another."""
    from ..store import get_store
    return get_store().add_credential(cred)
