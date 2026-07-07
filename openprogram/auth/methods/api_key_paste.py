"""API key paste flow — the degenerate "login" that's just a secret prompt.

Used by providers that don't have OAuth: OpenAI direct, Groq, Together,
custom vLLM deployments with a shared key, anything where the user is
expected to have a raw key string. Still wrapped in the :class:`LoginMethod`
interface so the settings UI can treat every auth flow uniformly — a
user adding a new profile doesn't need to know whether it's OAuth or a
key paste, they just pick the provider.

Validation is intentionally minimal: we only reject empty strings. Format
validation lives at the HTTP layer when the first real call happens
(where we have the actual "does this key work" signal). False-positive
format checks here would let obsolete validation rules block valid keys;
the API itself is authoritative.
"""
from __future__ import annotations

from ..types import (
    Credential,
    CredentialData,
    LoginMethod,
    LoginUi,
)


class ApiKeyPasteMethod(LoginMethod):
    """Prompts the user for a key and returns an api_key credential.

    The ``profile_id`` is supplied by the caller so one provider can hold
    several keys under named profiles ("personal", "work", etc).
    ``metadata`` lets the UI pass through display-only fields (nickname,
    org name) so the settings page has something human-readable to show
    next to each pool member.
    """

    method_id = "api_key_paste"

    def __init__(
        self,
        provider_id: str,
        *,
        profile_id: str = "default",
        prompt_message: str = "",
        metadata: dict | None = None,
    ) -> None:
        self.provider_id = provider_id
        self._profile_id = profile_id
        self._prompt_message = prompt_message or f"Paste your {provider_id} API key"
        self._metadata = dict(metadata or {})

    async def run(self, ui: LoginUi) -> Credential:
        # The ``secret=True`` flag asks the UI not to echo the input back
        # (terminal: no tty echo; webui: password input). A UI that can't
        # hide input treats it as a regular prompt — better than silently
        # echoing, since the user at least chose to see it.
        key = await ui.prompt(self._prompt_message, secret=True)
        key = key.strip()
        if not key:
            # Caller decides whether this is fatal. Returning a stub
            # credential with an empty key would silently mask the
            # mistake and confuse later 401s.
            raise ValueError("empty API key")
        return Credential(
            provider_id=self.provider_id,
            profile_id=self._profile_id,
            kind="api_key",
            payload=CredentialData(kind="api_key", auth_value=key),
            source=f"{self.method_id}:{self.provider_id}",
            metadata=self._metadata,
        )
