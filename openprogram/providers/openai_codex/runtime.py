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

import logging
from typing import Any, Optional

_log = logging.getLogger(__name__).warning

from openprogram.agentic_programming.runtime import Runtime
from openprogram.auth.context import get_active_profile_id
from openprogram.auth.manager import AuthManager, get_manager
from openprogram.auth.store import AuthStore
from openprogram.auth.types import (
    AuthConfigError,
    Credential,
    CredentialPool,
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


def ensure_codex_model_registered(mid: str) -> None:
    """Register one Codex model id into the static ENABLED_MODELS registry so the
    runtime can actually resolve it.

    A still-prefixed id (``openai-codex:gpt-5.5``) must NEVER be registered:
    it becomes a picker-visible ghost row (``_display_name_for_codex_model``
    prettifies it to "GPT-openai Codex:5.5") that shadows the real model.
    Callers are expected to pass a BARE id; refuse ``:`` ids with a warning
    naming the caller so the runtime surfaces the honest "Unknown model"
    instead of a half-working ghost.

    This matters because the ChatGPT/Codex backend has no list-models endpoint
    and OpenAICodexRuntime resolves a model id against the static ENABLED_MODELS dict —
    a codex id that isn't registered raises ``Unknown model`` at dispatch, with
    no custom-model fallback on this path. So anything we *list* must also be
    *registered* here. Idempotent; missing fields (cost / context) ride on the
    template until enrichment fills the listing.

    Used both by the import-time seed (the hand list, for offline / pre-Fetch)
    and by the live Fetch (``fetchers/codex.py``), which discovers current ids
    from models.dev and registers each one on demand."""
    if ":" in mid:
        import traceback
        caller = "".join(traceback.format_stack(limit=3)[:-1]).strip()
        _log(f"[codex] refusing to register model id with ':' (prefixed?): "
             f"{mid!r} — pass a bare id. Caller:\n{caller}")
        return

    from openprogram.providers.enabled_models import ENABLED_MODELS
    from openprogram.providers.thinking_spec import derive_thinking_fields

    template = next(
        (m for m in ENABLED_MODELS.values()
         if m.provider == "openai-codex" and m.api == "openai-codex"),
        None,
    )
    if template is None:
        return  # registry has no Codex entries at all; nothing to mirror.

    key = f"openai-codex/{mid}"
    display = _display_name_for_codex_model(mid)
    model = ENABLED_MODELS.get(key) or template.model_copy(update={"id": mid, "name": display})
    levels, default, variant = derive_thinking_fields(
        "openai-codex",
        mid,
        True,
        _codex_supports_xhigh(mid),
    )
    from openprogram.providers.enabled_models import default_fast
    ENABLED_MODELS[key] = model.model_copy(update={
        "id": mid,
        "name": model.name or display,
        "fast": default_fast(mid),
        "reasoning": True,
        "thinking_levels": levels,
        "default_thinking_level": default,
        "thinking_variant": variant,
    })


# No import-time registry seeding. The registry is built from config spec
# rows only (docs/design/providers/models/models.md §4.2). The default Codex
# model set is written to config as an *enable* on the user's behalf at login
# — see ``openprogram.auth.login_enable``. ``ensure_codex_model_registered``
# below stays as the runtime-registration helper the live Fetch and the
# runtime miss-path use once a config-backed codex template exists.
# ``_KNOWN_CODEX_MODELS`` remains the offline hand-list for ``list_models``.


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
    assert payload.kind in ("oauth", "device_code")
    return auth_adapter.extract_account_id(payload.auth_value)


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
        access = cred.payload.auth_value
        account_id = _account_id_for(cred)
        self._cached_access_token = access

        # A codex id can reach us from a live Fetch (models.dev) that the
        # static ENABLED_MODELS seed doesn't carry — e.g. after a restart, when only
        # _KNOWN_CODEX_MODELS was re-seeded but the user had fetched + enabled a
        # newer id. Register it on miss so the base Runtime's get_model() can
        # resolve it instead of raising "Unknown model".
        from openprogram.providers.models import get_model as _get_model
        if _get_model("openai-codex", model) is None:
            ensure_codex_model_registered(model)

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
        access = cred.payload.auth_value
        if access != self._cached_access_token:
            self.api_key = access
            self._cached_access_token = access
            account_id = _account_id_for(cred)
            new_headers = dict(self.api_model.headers or {})
            new_headers["chatgpt-account-id"] = account_id
            self.api_model = self.api_model.model_copy(update={"headers": new_headers})
        return super().exec(*args, **kwargs)
