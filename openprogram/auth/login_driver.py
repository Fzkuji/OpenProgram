"""Surface-agnostic login driver.

Given ``(provider, profile, method, LoginUi)`` it runs the login and returns a
``Credential``. Every surface — the CLI terminal, the web UI, the TUI — supplies
its OWN ``LoginUi`` (forwarding ``open_url`` / ``prompt`` / ``show_progress`` /
``show_code`` to its frontend), so the flow logic lives here exactly once.

This is the piece that lets web and TUI offer the same native logins the CLI
has, instead of punting to "use the other surface". The method ids come from
``openprogram/auth/login_methods.py``:

  api_key          paste a static key (prompted through the ui if not supplied)
  import_from_cli  copy/link an existing vendor-CLI credential (no interaction)
  pkce_oauth       browser PKCE OAuth via PkceLoginMethod (already LoginUi-based)
  device_code      browser device-code OAuth (bridged onto the ui below)
"""
from __future__ import annotations

import asyncio
from typing import Optional

from .types import AuthConfigError, Credential, LoginUi


async def run_login(
    provider: str,
    profile: str,
    method: str,
    ui: LoginUi,
    *,
    api_key: Optional[str] = None,
) -> Credential:
    """Run ``method`` for ``provider`` through ``ui`` and return the credential."""
    # Lazy import: the sync paste/import helpers live in cli.py, which imports
    # this module's caller — keep it lazy to avoid an import cycle.
    from . import cli as _cli

    if method == "api_key":
        key = (api_key or "").strip() or (await ui.prompt("Paste the API key", secret=True)).strip()
        return _cli._login_paste_api_key(provider, profile, api_key=key)

    if method == "import_from_cli":
        await ui.show_progress(f"Importing your existing {provider} login…")
        return _cli._login_import_from_cli(provider, profile)

    if method == "pkce_oauth":
        from .methods.pkce_oauth import PkceLoginMethod
        cfg = _pkce_config(provider)
        # Claude subscription credentials live in the `anthropic` pool even
        # when the user logged in via the `claude-code` provider entry, so
        # the direct runtime (which resolves from `anthropic`) finds them.
        store_provider = _credential_provider_id(provider)
        return await PkceLoginMethod(
            provider_id=store_provider, config=cfg, profile_id=profile
        ).run(ui)

    if method == "setup_token":
        token = (api_key or "").strip() or (
            await ui.prompt(
                "Paste your `claude setup-token` (run `claude setup-token` to mint one)",
                secret=True,
            )
        ).strip()
        if not token:
            raise AuthConfigError("no setup-token provided")
        from openprogram.providers.anthropic import auth_adapter as _anth
        return _anth.import_setup_token(token, profile_id=profile)

    if method == "device_code":
        return await _run_device_code(provider, profile, ui)

    raise AuthConfigError(f"unsupported login method: {method!r}")


def _credential_provider_id(provider: str) -> str:
    """The AuthStore pool a provider's credential belongs to.

    `claude-code` is an alias resolving from the `anthropic` pool, so a login
    through either entry stores under `anthropic`.
    """
    return "anthropic" if provider == "claude-code" else provider


def _pkce_config(provider: str):
    """Build the provider's PKCE config (Codex + Claude subscription today)."""
    if provider == "openai-codex":
        from openprogram.providers.openai_codex import auth_adapter
        return auth_adapter.build_pkce_config()
    if provider in ("anthropic", "claude-code"):
        from openprogram.providers.anthropic import auth_adapter
        return auth_adapter.build_pkce_config()
    raise AuthConfigError(f"no PKCE config registered for {provider!r}")


async def _run_device_code(provider: str, profile: str, ui: LoginUi) -> Credential:
    """Device-code OAuth (GitHub Copilot today). Bridges the sync
    OAuthLoginCallbacks onto the async LoginUi: the user_code + verification URL
    are surfaced through the ui, then we poll for the token."""
    if provider != "github-copilot":
        raise AuthConfigError(f"no device-code login for {provider!r}")
    from openprogram.providers.utils.oauth.github_copilot import login_github_copilot
    from openprogram.providers.utils.oauth.types import OAuthLoginCallbacks
    from openprogram.providers.github_copilot.auth_adapter import import_oauth_credential

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
        profile_id=profile,
        expires_at_ms=getattr(creds, "expires", 0) or 0,
    )


def persist(cred: Credential) -> list[str]:
    """Save a freshly-obtained credential to the store and retire superseded
    OAuth siblings — exactly what the CLI does after a login. Returns the pruned
    credential_ids. Shared so web and TUI store logins identically to the CLI."""
    from .store import get_store
    from .cli import _prune_superseded_oauth
    store = get_store()
    store.add_credential(cred)
    try:
        return _prune_superseded_oauth(store, cred)
    except Exception:
        return []
