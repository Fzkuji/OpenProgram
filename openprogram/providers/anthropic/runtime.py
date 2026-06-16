"""
AnthropicRuntime — thin Runtime subclass for Anthropic Claude API.

All streaming / tool-loop / exec-tree recording flows through the
default ``Runtime`` → ``AgentSession`` → pi-ai path. This class only
holds onto an API key and lets the base ``Runtime("anthropic:<id>",
api_key=...)`` resolution wire everything else.

Usage::

    from openprogram.providers.anthropic import AnthropicRuntime
    rt = AnthropicRuntime(api_key="sk-...", model="claude-sonnet-4-6")
    rt.exec(content=[{"type": "text", "text": "hi"}])
"""

from __future__ import annotations

from typing import Optional

from openprogram.agentic_programming.runtime import Runtime


class AnthropicRuntime(Runtime):
    """Runtime that targets the Anthropic Messages API via pi-ai.

    Args:
        api_key:     Anthropic API key. Falls back to ``ANTHROPIC_API_KEY``.
        model:       Model id under the ``anthropic`` provider namespace.
        max_retries: Retry budget forwarded to base ``Runtime``.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-6",
        max_retries: int = 2,
    ):
        if not api_key:
            # Unified resolution: plain api-key OR subscription OAuth token
            # (sk-ant-oat, from a Claude Code login adopted into the
            # AuthStore). The wire (anthropic.stream_simple) switches to
            # Bearer + Claude Code beta headers for OAuth tokens.
            from openprogram.auth.resolver import resolve_api_key_sync
            api_key = resolve_api_key_sync("anthropic")
        if not api_key:
            raise ValueError(
                "No Anthropic credential. Add an API key in Settings → "
                "Providers, pass api_key=, or log in with a Claude "
                "subscription (claude login) so the OAuth token is adopted."
            )
        super().__init__(
            model=f"anthropic:{model}",
            api_key=api_key,
            max_retries=max_retries,
        )

    def list_models(self) -> list[str]:
        """Return Anthropic model ids known to the pi-ai registry."""
        from openprogram.providers.models_generated import MODELS
        return sorted(
            m.id for m in MODELS.values() if m.provider == "anthropic"
        )
