"""GeminiCLIRuntime — thin Runtime wrapper over the HTTP provider.

Replaces the subprocess ``GeminiCLIRuntime`` in legacy_providers. Reuses
the ``gemini`` CLI's logged-in OAuth (``~/.gemini/oauth_creds.json``)
but talks to Cloud Code Assist (``cloudcode-pa.googleapis.com``)
directly via the registered ``gemini-subscription`` stream provider — no
subprocess, no argv juggling, no stdout JSON parsing.

Mirrors :class:`openprogram.providers.openai_codex.runtime.OpenAICodexRuntime`:

- AuthManager-first credential acquisition with one-shot import from
  ``~/.gemini/oauth_creds.json`` if no pool exists yet
- model id resolved via the ``gemini-subscription/<id>`` registry entries
  already populated in ``providers/models_generated.py``
- streaming + tool-loop + exec-tree recording flow through the default
  ``Runtime`` → ``AgentSession`` → provider path

Usage:
    from openprogram.providers.google_gemini_cli import GeminiCLIRuntime
    rt = GeminiCLIRuntime(model="gemini-2.5-flash")
    reply = rt.exec([{"type": "text", "text": "hi"}])
"""
from __future__ import annotations

from typing import Any, Optional

from openprogram.agentic_programming.runtime import Runtime
from openprogram.auth.context import get_active_profile_id
from openprogram.auth.manager import AuthManager, get_manager
from openprogram.auth.types import (
    AuthConfigError,
    CliDelegatedPayload,
    Credential,
    CredentialPool,
    OAuthPayload,
)

from . import auth_adapter


_KNOWN_GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-3-flash-preview",
    "gemini-3-pro-preview",
]


def _ensure_credential(manager: AuthManager, profile_id: str) -> Credential:
    """Resolve a Gemini CLI credential, importing the CLI's OAuth file on miss."""
    try:
        return manager.acquire_sync(auth_adapter.PROVIDER_ID, profile_id)
    except AuthConfigError:
        pass

    imported = auth_adapter.import_from_gemini_cli(profile_id=profile_id)
    if imported is None:
        raise AuthConfigError(
            f"{auth_adapter.gemini_cli_credentials_path()} not found or unusable. "
            "Run: gemini auth login",
            provider_id=auth_adapter.PROVIDER_ID,
            profile_id=profile_id,
        )
    manager.store.put_pool(CredentialPool(
        provider_id=auth_adapter.PROVIDER_ID,
        profile_id=profile_id,
        credentials=[imported],
    ))
    return manager.acquire_sync(auth_adapter.PROVIDER_ID, profile_id)


def _access_token_for(cred: Credential) -> str:
    """Pull the current access token out of whatever payload shape was returned.

    The credential can be either a first-class ``OAuthPayload`` (after
    AuthManager refreshed it) or a ``CliDelegatedPayload`` that still
    points at the on-disk JSON. Both carry an access token; the store
    path just differs.
    """
    payload = cred.payload
    if isinstance(payload, OAuthPayload):
        return payload.access_token
    if isinstance(payload, CliDelegatedPayload):
        import json
        from pathlib import Path
        data = json.loads(Path(payload.store_path).read_text(encoding="utf-8"))
        for key in payload.access_key_path:
            data = data[key]
        return str(data)
    raise AuthConfigError(
        f"gemini-subscription credential payload {type(payload).__name__} "
        "has no access token path this runtime understands.",
        provider_id=auth_adapter.PROVIDER_ID,
        profile_id=cred.profile_id,
    )


class GeminiCLIRuntime(Runtime):
    """Runtime burning a Gemini CLI (Google account) subscription via HTTP.

    Args:
        model:   Gemini model id (default ``gemini-2.5-flash``). Must
                 match a ``gemini-subscription/<id>`` entry in the registry.
        system:  Optional system prompt forwarded as ``instructions``.
        profile: Auth profile to consult. Defaults to current ContextVar
                 scope. Explicit override is for scripts that want to pin
                 a profile regardless of ambient scope.

    Extra kwargs are accepted-and-ignored for compatibility with callers
    that still pass subprocess-era fields (sandbox, yolo, cli_path).
    """

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        system: Optional[str] = None,
        *,
        profile: Optional[str] = None,
        **_ignored: Any,
    ) -> None:
        self._manager = get_manager()
        self._profile_id = profile or get_active_profile_id()
        self._cached_access_token: str = ""

        cred = _ensure_credential(self._manager, self._profile_id)
        if cred.kind not in ("oauth", "cli_delegated"):
            raise AuthConfigError(
                f"gemini-subscription/{self._profile_id} credential is "
                f"{cred.kind!r}, but this runtime needs OAuth (Cloud Code "
                "Assist backend). Run `gemini auth login` to populate "
                "~/.gemini/oauth_creds.json, then retry.",
                provider_id=auth_adapter.PROVIDER_ID,
                profile_id=self._profile_id,
            )
        access = _access_token_for(cred)
        self._cached_access_token = access

        super().__init__(model=f"gemini-subscription:{model}", api_key=access)
        self.system = system

    def list_models(self) -> list[str]:
        return list(_KNOWN_GEMINI_MODELS)

    def exec(self, *args: Any, **kwargs: Any) -> Any:
        # Re-acquire on every call — AuthManager refreshes internally if
        # the access token is close to expiry, dedup'ing concurrent
        # refreshes. Cheap when nothing rotated.
        cred = self._manager.acquire_sync(auth_adapter.PROVIDER_ID, self._profile_id)
        access = _access_token_for(cred)
        if access != self._cached_access_token:
            self.api_key = access
            self._cached_access_token = access
        return super().exec(*args, **kwargs)


__all__ = ["GeminiCLIRuntime"]
