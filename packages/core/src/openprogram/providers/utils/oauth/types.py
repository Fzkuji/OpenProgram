"""
OAuth type definitions.

Mirrors utils/oauth/types.ts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol

if TYPE_CHECKING:
    from openprogram.providers.types import Model


@dataclass
class OAuthCredentials:
    """Stored OAuth credentials for a provider."""

    refresh: str
    access: str
    expires: int  # Unix timestamp in milliseconds
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"refresh": self.refresh, "access": self.access, "expires": self.expires, **self.extra}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OAuthCredentials":
        refresh = d.get("refresh", "")
        access = d.get("access", "")
        expires = d.get("expires", 0)
        extra = {k: v for k, v in d.items() if k not in ("refresh", "access", "expires")}
        return cls(refresh=refresh, access=access, expires=expires, extra=extra)


OAuthProviderId = str


@dataclass
class OAuthPrompt:
    message: str
    placeholder: str | None = None
    allow_empty: bool = False


@dataclass
class OAuthAuthInfo:
    url: str
    instructions: str | None = None


@dataclass
class OAuthLoginCallbacks:
    on_auth: Callable[[OAuthAuthInfo], None]
    on_prompt: Callable[[OAuthPrompt], Awaitable[str]]
    on_progress: Callable[[str], None] | None = None
    on_manual_code_input: Callable[[], Awaitable[str]] | None = None
    signal: Any | None = None  # asyncio.Event for cancellation


class OAuthProviderInterface(Protocol):
    """Protocol for OAuth provider implementations."""

    @property
    def id(self) -> OAuthProviderId: ...

    @property
    def name(self) -> str: ...

    @property
    def uses_callback_server(self) -> bool:
        return False

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials: ...

    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials: ...

    def get_api_key(self, credentials: OAuthCredentials) -> str: ...

    def modify_models(
        self,
        models: list["Model"],
        credentials: OAuthCredentials,
    ) -> list["Model"]:
        return models
