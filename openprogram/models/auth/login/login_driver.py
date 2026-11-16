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
  pkce_oauth       browser PKCE OAuth via PkceLoginMethod (already LoginUi-based)
  device_code      browser device-code OAuth (bridged onto the ui below)
  paste_token      paste an access token (Grok; a GitHub token for Copilot)
"""
from __future__ import annotations

import contextlib

import asyncio
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

    elif method == "pkce_oauth":
        from ..methods.pkce_oauth import PkceLoginMethod
        cfg = _pkce_config(provider)
        store_provider = _credential_provider_id(provider)
        cred = await PkceLoginMethod(provider_id=store_provider, config=cfg).run(ui)

    elif method == "paste_token":
        if provider == "xai-subscription":
            from openprogram.models.providers.adapters.xai_subscription.auth_adapter import (
                import_pasted_token,
            )
            prompt = "Paste the Grok access token"
        elif provider == "github-copilot":
            from openprogram.models.providers.adapters.github_copilot.auth_adapter import (
                import_pasted_token,
            )
            prompt = "Paste a GitHub token"
        else:
            raise AuthConfigError(f"no paste-token login for {provider!r}")
        token = (api_key or "").strip() or (await ui.prompt(prompt, secret=True)).strip()
        cred = import_pasted_token(token)

    elif method == "device_code":
        cred = await _run_device_code(provider, ui)

    else:
        raise AuthConfigError(f"unsupported login method: {method!r}")

    from ..account.account_labels import apply_account_label

    if cred.source != "import":
        cred.source = source
    if provider == "openai-codex" and cred.kind == "oauth":
        from openprogram.models.providers.adapters.openai_codex import auth_adapter
        with contextlib.suppress(Exception):
            cred.identity.setdefault("account_id", auth_adapter.extract_account_id(cred.payload.auth_value))
        plan = auth_adapter.plan_of(str(cred.payload.data.get("id_token") or ""), cred.payload.auth_value)
        if plan:
            cred.identity.setdefault("plan", plan)
    return apply_account_label(cred, label)


def _credential_provider_id(provider: str) -> str:
    """The credential pool for this provider."""
    return provider


def _pkce_config(provider: str):
    """Build the provider's supported PKCE configuration."""
    if provider == "openai-codex":
        from openprogram.models.providers.adapters.openai_codex import auth_adapter
        return auth_adapter.build_pkce_config()
    if provider == "xai-subscription":
        from openprogram.models.providers.adapters.xai_subscription import auth_adapter
        return auth_adapter.build_pkce_config()
    raise AuthConfigError(f"no PKCE config registered for {provider!r}")


async def _run_device_code(provider: str, ui: LoginUi) -> Credential:
    """Device-code OAuth (GitHub Copilot today). Bridges the sync
    OAuthLoginCallbacks onto the async LoginUi: the user_code + verification URL
    are surfaced through the ui, then we poll for the token."""
    if provider != "github-copilot":
        raise AuthConfigError(f"no device-code login for {provider!r}")
    from openprogram.models.providers.utils.oauth.github_copilot import login_github_copilot
    from openprogram.models.providers.utils.oauth.types import OAuthLoginCallbacks
    from openprogram.models.providers.adapters.github_copilot.auth_adapter import import_oauth_credential

    # on_auth/on_progress are sync callbacks; the ui methods are async. Schedule
    # them as fire-and-forget tasks (these are one-way UI updates — device-code
    # has no blocking prompt) and keep references so they aren't GC'd.
    pending: list[asyncio.Task] = []

    def _spawn(coro) -> None:
        # Fire-and-forget UI update, but retrieve any exception so a failing ui
        # method surfaces instead of becoming a swallowed unretrieved-task
        # warning (and keep a reference so it isn't GC'd mid-flight).
        t = asyncio.ensure_future(coro)
        t.add_done_callback(lambda f: f.cancelled() or f.exception())
        pending.append(t)

    def _on_auth(info) -> None:
        _spawn(ui.open_url(info.url))
        msg = getattr(info, "instructions", "") or f"Open {info.url} to sign in"
        _spawn(ui.show_progress(msg))

    def _on_progress(m: str) -> None:
        _spawn(ui.show_progress(m))

    creds = await login_github_copilot(
        OAuthLoginCallbacks(on_auth=_on_auth, on_progress=_on_progress)
    )
    return import_oauth_credential(
        creds.access,
        getattr(creds, "refresh", "") or "",
        expires_at_ms=getattr(creds, "expires", 0) or 0,
    )


def persist(cred: Credential) -> Credential:
    """Add a freshly obtained credential to the provider's list and return
    the row as stored: a sign-in of an account already listed (same email
    or account id) renews that row in place instead of adding another."""
    from ..store import get_store
    return get_store().add_credential(cred)
