"""GeminiCLIRuntime — thin Runtime wrapper over the HTTP provider.

Reuses the ``gemini`` CLI's logged-in OAuth (``~/.gemini/oauth_creds.json``)
but talks to Cloud Code Assist (``cloudcode-pa.googleapis.com``) directly via
the registered ``gemini-subscription`` stream provider — no subprocess.

Mirrors :class:`.openai_codex.OpenAICodexRuntime`:

- CredentialProvider-first credential acquisition with one-shot import from
  ``~/.gemini/oauth_creds.json`` if no pool exists yet
  (:mod:`openprogram.models.providers.adapters.google_gemini_cli.credentials`)
- model id resolved via the ``gemini-subscription/<id>`` registry entries
- streaming + tool-loop + exec-tree recording flow through the default
  ``Runtime`` → ``AgentSession`` → provider path

Usage:
    from openprogram.runtime.agent.runtime.factory import create_runtime
    rt = create_runtime(provider="gemini-cli", model="gemini-2.5-flash")
    reply = rt.exec([{"type": "text", "text": "hi"}])
"""
from __future__ import annotations

from typing import Any, Optional

from openprogram.models.auth.context import get_active_account_id
from openprogram.models.auth.credential_provider import get_credential_provider
from openprogram.models.auth.types import AuthConfigError
from openprogram.models.providers.adapters.google_gemini_cli import auth_adapter
from openprogram.models.providers.adapters.google_gemini_cli.credentials import (
    access_token_for,
    ensure_credential,
)

from ..runtime import Runtime


_KNOWN_GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-3-flash-preview",
    "gemini-3-pro-preview",
]


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
        self._manager = get_credential_provider()
        self._account_id = profile or get_active_account_id()
        self._cached_access_token: str = ""

        cred = ensure_credential(self._manager, self._account_id)
        if cred.kind not in ("oauth", "cli_delegated"):
            raise AuthConfigError(
                f"gemini-subscription/{self._account_id} credential is "
                f"{cred.kind!r}, but this runtime needs OAuth (Cloud Code "
                "Assist backend). Run `gemini auth login` to populate "
                "~/.gemini/oauth_creds.json, then retry.",
                provider_id=auth_adapter.PROVIDER_ID,
                account_id=self._account_id,
            )
        access = access_token_for(cred)
        self._cached_access_token = access

        super().__init__(model=f"gemini-subscription:{model}", api_key=access)
        self.system = system

    def list_models(self) -> list[str]:
        return list(_KNOWN_GEMINI_MODELS)

    def exec(self, *args: Any, **kwargs: Any) -> Any:
        # Re-acquire on every call — CredentialProvider refreshes internally if
        # the access token is close to expiry, dedup'ing concurrent
        # refreshes. Cheap when nothing rotated.
        cred = self._manager.acquire_sync(auth_adapter.PROVIDER_ID, self._account_id)
        access = access_token_for(cred)
        if access != self._cached_access_token:
            self.api_key = access
            self._cached_access_token = access
        return super().exec(*args, **kwargs)


__all__ = ["GeminiCLIRuntime"]
