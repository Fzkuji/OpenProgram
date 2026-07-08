"""
OpenAIRuntime — thin Runtime subclass for OpenAI's Responses API.

Streaming, tool loops, and exec-tree recording all happen through the
default ``Runtime`` → ``AgentSession`` → pi-ai path. This class just
resolves the API key and lets ``Runtime("openai:<id>", api_key=...)``
do the rest.

Usage::

    from openprogram.providers.openai_responses import OpenAIRuntime
    rt = OpenAIRuntime(api_key="sk-...", model="gpt-4o")
    rt.exec(content=[{"type": "text", "text": "hi"}])
"""

from __future__ import annotations

from typing import Optional

from openprogram.agentic_programming.runtime import Runtime


class OpenAIRuntime(Runtime):
    """Runtime that targets the OpenAI Responses API via pi-ai.

    Args:
        api_key:     OpenAI API key. Falls back to ``OPENAI_API_KEY``.
        model:       Model id under the ``openai`` provider namespace.
        max_retries: Retry budget forwarded to base ``Runtime``.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o",
        max_retries: int = 2,
    ):
        if not api_key:
            from openprogram.providers.env_api_keys import resolve_provider_key
            api_key = resolve_provider_key("openai")
        if not api_key:
            raise ValueError(
                "OpenAI API key is required. Add one in Settings → "
                "Providers, pass api_key=, or set OPENAI_API_KEY."
            )
        super().__init__(
            model=f"openai:{model}",
            api_key=api_key,
            max_retries=max_retries,
        )

    def list_models(self) -> list[str]:
        """Return OpenAI model ids known to the pi-ai registry."""
        from openprogram.providers.models_generated import MODEL_REGISTRY
        return sorted(
            m.id for m in MODEL_REGISTRY.values() if m.provider == "openai"
        )
