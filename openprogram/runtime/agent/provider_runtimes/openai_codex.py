"""
OpenAICodexRuntime — thin Runtime subclass that burns ChatGPT subscription.

A first run with no stored ChatGPT login imports the Codex CLI's
``~/.codex/auth.json`` (:mod:`openprogram.models.providers.adapters.openai_codex.credentials`).
Each request then goes out with the login the credential layer chooses
(``models/auth/selection.py``), refreshed when near expiry; the adapter
sets the ``chatgpt-account-id`` header from that login's token.

All streaming / tool-loop / exec-tree recording flows through the default
Runtime → AgentSession → provider path. This class only handles auth and
the ChatGPT backend headers.

Usage:
    from openprogram.runtime.agent.runtime.factory import create_runtime
    rt = create_runtime(provider="openai-codex", model="gpt-5.5")
    reply = rt.exec([{"type": "text", "text": "hi"}])
"""
from __future__ import annotations

from typing import Any

from openprogram.models.auth.credential_provider import get_credential_provider
from openprogram.models.providers.adapters.openai_codex import auth_adapter
from openprogram.models.providers.adapters.openai_codex.catalog import (
    codex_client_version,
    ensure_codex_model_registered,
)
from openprogram.models.providers.adapters.openai_codex.credentials import require_sign_in

from ..runtime import Runtime


class OpenAICodexRuntime(Runtime):
    """
    Args:
        model:    Default model id (e.g. "gpt-5.5-mini", "gpt-5.5").
        system:   Optional system prompt (forwarded as `instructions`).

    Extra kwargs are accepted-and-ignored for compatibility with callers
    that still pass subprocess-era fields (sandbox, full_auto, session_id).
    """

    def __init__(
        self,
        model: str = "gpt-5.5",
        system: str | None = None,
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

        # Only that a sign-in exists: one in a quota hold is chosen, waited
        # for or reported per request by the credential layer.
        require_sign_in(get_credential_provider())

        # A codex id can reach us from a live Fetch (models.dev) that the
        # static ENABLED_MODELS seed doesn't carry — e.g. after a restart, when only
        # _KNOWN_CODEX_MODELS was re-seeded but the user had fetched + enabled a
        # newer id. Register it on miss so the base Runtime's get_model() can
        # resolve it instead of raising "Unknown model".
        from openprogram.models.providers.catalog.models import get_model as _get_model
        if _get_model("openai-codex", model) is None:
            ensure_codex_model_registered(model)

        super().__init__(model=f"openai-codex:{model}")

        # ChatGPT backend wants extra headers alongside Bearer. Attach them
        # to a per-runtime Model copy so we don't mutate the registry.
        # ``originator: codex_cli_rs`` + ``version`` is the identity the backend
        # gates newer/greylisted ids behind — dispatch 404s a model like
        # ``gpt-5.6-luna`` for an unrecognised originator even though the list
        # endpoint returns it. Present the real CLI identity so listing and
        # dispatch agree on what's runnable.
        self.api_model = self.api_model.model_copy(update={
            "headers": {
                "originator": "codex_cli_rs",
                "version": codex_client_version(),
                "OpenAI-Beta": "responses=experimental",
            },
        })

        self.system = system

__all__ = ["OpenAICodexRuntime"]
