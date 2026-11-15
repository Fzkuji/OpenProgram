"""
OpenAICodexRuntime — thin Runtime subclass that burns ChatGPT subscription.

Reads OAuth tokens from the auth :class:`CredentialProvider` through the
Codex provider's credential helpers
(:mod:`openprogram.models.providers.adapters.openai_codex.credentials`). First run
adopts the Codex CLI's ``~/.codex/auth.json`` into our own store; after
that, CredentialProvider owns refresh + rotation.

All streaming / tool-loop / exec-tree recording flows through the default
Runtime → AgentSession → provider path. This class only handles auth and
the ChatGPT backend headers.

Usage:
    from openprogram.runtime.agent.runtime.factory import create_runtime
    rt = create_runtime(provider="openai-codex", model="gpt-5.5")
    reply = rt.exec([{"type": "text", "text": "hi"}])
"""
from __future__ import annotations

from typing import Any, Optional

from openprogram.models.auth.account.account_selection import get_active_account
from openprogram.models.auth.credential_provider import get_credential_provider
from openprogram.models.auth.types import AuthConfigError
from openprogram.models.providers.adapters.openai_codex import auth_adapter
from openprogram.models.providers.adapters.openai_codex.catalog import (
    codex_client_version,
    ensure_codex_model_registered,
)
from openprogram.models.providers.adapters.openai_codex.credentials import (
    account_id_for,
    acquire as acquire_credential,
    ensure_credential,
    oauth_view,
)

from ..runtime import Runtime


class OpenAICodexRuntime(Runtime):
    """
    Args:
        model:    Default model id (e.g. "gpt-5.5-mini", "gpt-5.5").
        system:   Optional system prompt (forwarded as `instructions`).
        profile:  Auth profile to consult. Defaults to the provider's
                  active account, falling back to the current scope. Explicit
                  override is useful for scripts that want to pin a
                  profile regardless of ambient scope.

    Extra kwargs are accepted-and-ignored for compatibility with callers
    that still pass subprocess-era fields (sandbox, full_auto, session_id).
    """

    def __init__(
        self,
        model: str = "gpt-5.5",
        system: str | None = None,
        *,
        profile: Optional[str] = None,
        **_ignored: Any,
    ) -> None:
        # Normalize a still-prefixed id to a bare one. Persisted session
        # metadata stores ``runtime.model`` verbatim (= ``openai-codex:gpt-5.5``),
        # and the restore path feeds that back as ``model=``. Left prefixed it
        # would miss ``_get_model`` and register a ghost model whose display
        # name is "GPT-openai Codex:5.5". Strip repeatedly to also undo the
        # ``openai-codex:openai-codex:gpt-5.5`` double-prefix already in old
        # meta.json files.
        while isinstance(model, str) and model.startswith(f"{auth_adapter.PROVIDER_ID}:"):
            model = model.split(":", 1)[1]

        self._manager = get_credential_provider()
        self._account_id = profile or get_active_account(auth_adapter.PROVIDER_ID)
        self._cached_access_token: str = ""

        cred = oauth_view(ensure_credential(self._manager, self._account_id), self._manager)
        if cred.kind != "oauth":
            # OpenAICodexRuntime targets the ChatGPT Responses backend
            # (requires a chatgpt-account-id header minted from the JWT).
            # Bare API keys don't carry that, so they belong under the
            # `openai` pool, not `openai-codex`. Surface this clearly
            # instead of crashing on a missing .access_token attribute.
            raise AuthConfigError(
                f"openai-codex/{self._account_id} credential is "
                f"{cred.kind!r}, but this runtime needs OAuth (ChatGPT "
                "Responses backend). Run `codex login` (don't pick the "
                "API-key option) to get OAuth tokens into "
                "~/.codex/auth.json, then `openprogram providers adopt "
                "codex_cli`. If you only have a bare OpenAI key, switch "
                "your chat provider to `openai`, not `openai-codex`.",
                provider_id=auth_adapter.PROVIDER_ID,
                account_id=self._account_id,
            )
        access = cred.payload.auth_value
        account_id = account_id_for(cred)
        self._cached_access_token = access

        # A codex id can reach us from a live Fetch (models.dev) that the
        # static ENABLED_MODELS seed doesn't carry — e.g. after a restart, when only
        # _KNOWN_CODEX_MODELS was re-seeded but the user had fetched + enabled a
        # newer id. Register it on miss so the base Runtime's get_model() can
        # resolve it instead of raising "Unknown model".
        from openprogram.models.providers.catalog.models import get_model as _get_model
        if _get_model("openai-codex", model) is None:
            ensure_codex_model_registered(model)

        super().__init__(model=f"openai-codex:{model}", api_key=access)

        # ChatGPT backend wants extra headers alongside Bearer. Attach them
        # to a per-runtime Model copy so we don't mutate the registry.
        # ``originator: codex_cli_rs`` + ``version`` is the identity the backend
        # gates newer/greylisted ids behind — dispatch 404s a model like
        # ``gpt-5.6-luna`` for an unrecognised originator even though the list
        # endpoint returns it. Present the real CLI identity so listing and
        # dispatch agree on what's runnable.
        self.api_model = self.api_model.model_copy(update={
            "headers": {
                "chatgpt-account-id": account_id,
                "originator": "codex_cli_rs",
                "version": codex_client_version(),
                "OpenAI-Beta": "responses=experimental",
            },
        })

        self.system = system

    def exec(self, *args: Any, **kwargs: Any) -> Any:
        # Re-acquire on every call — CredentialProvider refreshes internally if
        # the access token is close to expiry, dedup'ing concurrent
        # refreshes. If the cred pointer is unchanged we skip the header
        # update to keep this cheap.
        cred = acquire_credential(self._manager, self._account_id)
        access = cred.payload.auth_value
        if access != self._cached_access_token:
            self.api_key = access
            self._cached_access_token = access
            account_id = account_id_for(cred)
            new_headers = dict(self.api_model.headers or {})
            new_headers["chatgpt-account-id"] = account_id
            self.api_model = self.api_model.model_copy(update={"headers": new_headers})
        return super().exec(*args, **kwargs)


__all__ = ["OpenAICodexRuntime"]
