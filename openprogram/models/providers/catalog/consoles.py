"""Where a person gets a key or adds credit, and which providers to offer first.

Two small tables the provider pages read:

  * :data:`POPULAR` — the providers offered first when nothing is connected
    and at the top of Add provider, in order. A fixed list in code, as
    OpenCode keeps one (``packages/app/src/providers/catalog/order.ts``);
    the catalogue (models.dev) has no popularity field. Each entry may name
    variants of the same vendor (another region, a coding plan, the plain
    API key beside a sign-in) that the tile offers as a second choice.
  * :data:`CONSOLES` — the provider's own page for API keys and for
    billing. Only pages the provider documents are listed; a provider
    without an entry gets no link rather than a guessed one.

Anthropic is offered only as an API key (``console.anthropic.com``); no
subscription sign-in is listed here or anywhere else.
"""
from __future__ import annotations

from typing import Optional

#: (id, variants) in the order the empty state and Add provider show them.
POPULAR: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("openai-codex", ("openai",)),
    ("anthropic", ()),
    ("google", ()),
    ("xai", ("xai-subscription",)),
    ("deepseek", ()),
    ("openrouter", ()),
    ("moonshotai", ("kimi-coding", "moonshotai-cn")),
    ("zai", ("zai-coding-plan", "zhipuai")),
)

#: Local servers offered when one is found listening on this machine.
LOCAL_OFFERED: tuple[str, ...] = ("ollama", "lmstudio", "vllm", "llamacpp")

#: provider id -> {"keys": url, "billing": url}; either may be missing.
CONSOLES: dict[str, dict[str, str]] = {
    "openai": {
        "keys": "https://platform.openai.com/api-keys",
        "billing": "https://platform.openai.com/settings/organization/billing/overview",
    },
    "anthropic": {
        "keys": "https://console.anthropic.com/settings/keys",
        "billing": "https://console.anthropic.com/settings/billing",
    },
    "google": {"keys": "https://aistudio.google.com/apikey"},
    "xai": {"keys": "https://console.x.ai", "billing": "https://console.x.ai"},
    "deepseek": {
        "keys": "https://platform.deepseek.com/api_keys",
        "billing": "https://platform.deepseek.com/top_up",
    },
    "openrouter": {
        "keys": "https://openrouter.ai/settings/keys",
        "billing": "https://openrouter.ai/settings/credits",
    },
    "moonshotai": {
        "keys": "https://platform.moonshot.ai/console/api-keys",
        "billing": "https://platform.moonshot.ai/console/pay",
    },
    "moonshotai-cn": {
        "keys": "https://platform.moonshot.cn/console/api-keys",
        "billing": "https://platform.moonshot.cn/console/pay",
    },
    "zai": {"keys": "https://z.ai/manage-apikey/apikey-list"},
    "zai-coding-plan": {"keys": "https://z.ai/manage-apikey/apikey-list"},
    "groq": {"keys": "https://console.groq.com/keys"},
    "mistral": {"keys": "https://console.mistral.ai/api-keys"},
    "cerebras": {"keys": "https://cloud.cerebras.ai"},
    "huggingface": {"keys": "https://huggingface.co/settings/tokens"},
    "github-copilot": {"billing": "https://github.com/settings/copilot"},
}


def console_url(provider_id: str, page: str = "keys") -> Optional[str]:
    """The provider's own ``keys`` or ``billing`` page, or ``None``."""
    return CONSOLES.get(provider_id, {}).get(page)


def popular_ids() -> list[str]:
    return [pid for pid, _variants in POPULAR]


__all__ = ["CONSOLES", "LOCAL_OFFERED", "POPULAR", "console_url", "popular_ids"]
