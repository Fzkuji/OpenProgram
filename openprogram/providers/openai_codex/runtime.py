"""
OpenAICodexRuntime — thin Runtime subclass that burns ChatGPT subscription.

Reads OAuth tokens from auth v2's :class:`AuthManager` (registered via
:mod:`.auth_adapter`). First run adopts the Codex CLI's
``~/.codex/auth.json`` into our own store; after that, AuthManager owns
refresh + rotation. Rotated tokens are mirrored back into
``~/.codex/auth.json`` so the Codex CLI stays in sync with us.

All streaming / tool-loop / exec-tree recording flows through the default
Runtime → AgentSession → provider path. This class only handles auth.

Usage:
    from openprogram.providers.openai_codex import OpenAICodexRuntime
    rt = OpenAICodexRuntime(model="gpt-5.5-mini")
    reply = rt.exec([{"type": "text", "text": "hi"}])
"""
from __future__ import annotations

from typing import Any, Optional

from openprogram.agentic_programming.runtime import Runtime
from openprogram.auth.context import get_active_profile_id
from openprogram.auth.manager import AuthManager, get_manager
from openprogram.auth.store import AuthStore
from openprogram.auth.types import (
    AuthConfigError,
    Credential,
    CredentialPool,
    OAuthPayload,
)

from . import auth_adapter


_KNOWN_CODEX_MODELS = [
    "gpt-5.5", "gpt-5.5-mini", "gpt-5.5-pro", "gpt-5.5-codex",
    "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-pro",
    "gpt-5.3-codex", "gpt-5.3-codex-spark",
    "gpt-5.2-codex", "gpt-5.1-codex", "gpt-5.1-codex-mini",
]


def _codex_supports_xhigh(model_id: str) -> bool:
    return any(tag in model_id for tag in ("gpt-5.2", "gpt-5.3", "gpt-5.4", "gpt-5.5"))


def _display_name_for_codex_model(model_id: str) -> str:
    parts = model_id.replace("gpt-", "").split("-")
    head = "GPT-" + parts[0]
    tail = " ".join(p.capitalize() if p != "codex" else "Codex" for p in parts[1:])
    return (head + " " + tail).strip()


def _augment_registry_with_codex_models() -> None:
    """Inject Codex-route model ids into the provider registry if the
    generated catalog is missing them. The ChatGPT backend has no public
    model-listing endpoint, so OpenClaw / pi-ai maintain their lists by
    hand; we mirror that list here and let the registry carry the rest
    (name, cost, context window) for whichever ids already exist. New
    entries get a sensible Codex default."""
    from openprogram.providers.models_generated import MODELS
    from openprogram.providers.thinking_catalog import derive_thinking_fields

    template = next(
        (m for m in MODELS.values()
         if m.provider == "openai-codex" and m.api == "openai-codex-responses"),
        None,
    )
    if template is None:
        return  # registry has no Codex entries at all; nothing to mirror.

    for mid in _KNOWN_CODEX_MODELS:
        key = f"openai-codex/{mid}"
        display = _display_name_for_codex_model(mid)
        model = MODELS.get(key) or template.model_copy(update={"id": mid, "name": display})
        levels, default, variant = derive_thinking_fields(
            "openai-codex",
            mid,
            True,
            _codex_supports_xhigh(mid),
        )
        MODELS[key] = model.model_copy(update={
            "id": mid,
            "name": model.name or display,
            "reasoning": True,
            "thinking_levels": levels,
            "default_thinking_level": default,
            "thinking_variant": variant,
        })


_augment_registry_with_codex_models()


# ---------------------------------------------------------------------------
# Credential acquisition — AuthManager-first, with one-shot import fallback
# ---------------------------------------------------------------------------

def _ensure_credential(manager: AuthManager, profile_id: str) -> Credential:
    """Resolve a Codex credential for the given profile.

    Try the store first. If no pool exists, attempt a one-shot import
    from the Codex CLI's ``auth.json``; register the imported credential
    and retry acquisition (which may also refresh if the JWT is close to
    expiry). If neither path yields a credential, raise — the caller
    should render a "please run `codex login --device-auth`" message.
    """
    try:
        return manager.acquire_sync(auth_adapter.PROVIDER_ID, profile_id)
    except AuthConfigError:
        pass  # fall through to import path

    imported = auth_adapter.import_from_codex_file(profile_id=profile_id)
    if imported is None:
        raise AuthConfigError(
            f"{auth_adapter.codex_auth_path()} not found or unusable. "
            "Run: codex login --device-auth",
            provider_id=auth_adapter.PROVIDER_ID,
            profile_id=profile_id,
        )
    manager.store.put_pool(CredentialPool(
        provider_id=auth_adapter.PROVIDER_ID,
        profile_id=profile_id,
        credentials=[imported],
    ))
    return manager.acquire_sync(auth_adapter.PROVIDER_ID, profile_id)


def _account_id_for(cred: Credential) -> str:
    """Return the chatgpt_account_id for the credential.

    Prefers metadata (cheap, survives refreshes since _codex_refresh
    preserves metadata). Falls back to decoding the JWT."""
    account_id = cred.metadata.get("account_id")
    if isinstance(account_id, str) and account_id.strip():
        return account_id.strip()
    payload = cred.payload
    assert isinstance(payload, OAuthPayload)
    return auth_adapter.extract_account_id(payload.access_token)


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

class OpenAICodexRuntime(Runtime):
    """
    Args:
        model:    Default model id (e.g. "gpt-5.5-mini", "gpt-5.5").
        system:   Optional system prompt (forwarded as `instructions`).
        profile:  Auth profile to consult. Defaults to the current
                  ContextVar scope (typically "default"). Explicit
                  override is useful for scripts that want to pin a
                  profile regardless of ambient scope.

    Extra kwargs are accepted-and-ignored for compatibility with callers
    that still pass subprocess-era fields (sandbox, full_auto, session_id).
    """

    def __init__(
        self,
        model: str = "gpt-5.5-mini",
        system: str | None = None,
        *,
        profile: Optional[str] = None,
        **_ignored: Any,
    ) -> None:
        self._manager = get_manager()
        self._profile_id = profile or get_active_profile_id()
        self._cached_access_token: str = ""

        cred = _ensure_credential(self._manager, self._profile_id)
        if cred.kind != "oauth":
            # OpenAICodexRuntime targets the ChatGPT Responses backend
            # (requires a chatgpt-account-id header minted from the JWT).
            # Bare API keys don't carry that, so they belong under the
            # `openai` pool, not `openai-codex`. Surface this clearly
            # instead of crashing on a missing .access_token attribute.
            raise AuthConfigError(
                f"openai-codex/{self._profile_id} credential is "
                f"{cred.kind!r}, but this runtime needs OAuth (ChatGPT "
                "Responses backend). Run `codex login` (don't pick the "
                "API-key option) to get OAuth tokens into "
                "~/.codex/auth.json, then `openprogram providers adopt "
                "codex_cli`. If you only have a bare OpenAI key, switch "
                "your chat provider to `openai`, not `openai-codex`.",
                provider_id=auth_adapter.PROVIDER_ID,
                profile_id=self._profile_id,
            )
        access = cred.payload.access_token
        account_id = _account_id_for(cred)
        self._cached_access_token = access

        super().__init__(model=f"openai-codex:{model}", api_key=access)

        # ChatGPT backend wants extra headers alongside Bearer. Attach them
        # to a per-runtime Model copy so we don't mutate the registry.
        self.api_model = self.api_model.model_copy(update={
            "headers": {
                "chatgpt-account-id": account_id,
                "originator": "openprogram",
                "OpenAI-Beta": "responses=experimental",
            },
        })

        self.system = system

    def list_models(self) -> list[str]:
        return list(_KNOWN_CODEX_MODELS)

    def exec(self, *args: Any, **kwargs: Any) -> Any:
        # Re-acquire on every call — AuthManager refreshes internally if
        # the access token is close to expiry, dedup'ing concurrent
        # refreshes. If the cred pointer is unchanged we skip the header
        # update to keep this cheap.
        cred = self._manager.acquire_sync(auth_adapter.PROVIDER_ID, self._profile_id)
        access = cred.payload.access_token
        if access != self._cached_access_token:
            self.api_key = access
            self._cached_access_token = access
            account_id = _account_id_for(cred)
            new_headers = dict(self.api_model.headers or {})
            new_headers["chatgpt-account-id"] = account_id
            self.api_model = self.api_model.model_copy(update={"headers": new_headers})
        return super().exec(*args, **kwargs)
