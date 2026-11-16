"""Where a person gets a key or adds credit, and which providers to offer first.

What the provider pages read here:

  * :data:`POPULAR` — the providers offered first when nothing is connected
    and at the top of Add provider, in order. A fixed list in code, as
    OpenCode keeps one (``packages/app/src/providers/catalog/order.ts``);
    the catalogue (models.dev) has no popularity field. A tile also offers
    the other ways of its company (another region, a coding plan, the plain
    API key beside a sign-in) that the company table marks as offered
    (``login_method_registry.COMPANIES``, :func:`variants`).
  * :func:`console_url` — the provider's own page for API keys and for
    billing, from the provider's connection entry. Only pages the provider
    documents are listed; a provider without one gets no link.

Anthropic is offered only as an API key (``console.anthropic.com``); no
subscription sign-in is listed here or anywhere else.
"""
from __future__ import annotations

from typing import Optional

#: Provider ids in the order the empty state and Add provider show them.
POPULAR: tuple[str, ...] = (
    "openai-codex", "anthropic", "google", "xai", "deepseek", "openrouter", "moonshotai", "zai",
)

#: Local servers offered when one is found listening on this machine.
LOCAL_OFFERED: tuple[str, ...] = ("ollama", "lmstudio", "vllm", "llamacpp")

def console_url(provider_id: str, page: str = "keys") -> Optional[str]:
    """The provider's own ``keys`` or ``billing`` page, or ``None``; kept in
    the provider's connection entry (``auth/login/login_method_registry.py``)."""
    from openprogram.models.auth.login.login_method_registry import console_url as _url
    return _url(provider_id, page)


def popular_ids() -> list[str]:
    return list(POPULAR)


def variants(provider_id: str) -> list[str]:
    """The other ways of ``provider_id``'s company a tile offers, from the company table."""
    from openprogram.models.auth.login.login_method_registry import offered_ways
    return offered_ways(provider_id)


__all__ = ["LOCAL_OFFERED", "POPULAR", "console_url", "popular_ids", "variants"]
