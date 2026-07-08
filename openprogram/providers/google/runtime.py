"""
GeminiRuntime — thin Runtime subclass for Google Gemini's Generative
Language API.

Streaming, tool loops, and exec-tree recording all happen through the
default ``Runtime`` → ``AgentSession`` → pi-ai path. This class only
resolves the API key and lets ``Runtime("google:<id>", api_key=...)``
do the rest.

Usage::

    from openprogram.providers.google import GeminiRuntime
    rt = GeminiRuntime(api_key="...", model="gemini-2.5-pro")
    rt.exec(content=[{"type": "text", "text": "hi"}])
"""

from __future__ import annotations

from typing import Optional

from openprogram.agentic_programming.runtime import Runtime


class GeminiRuntime(Runtime):
    """Runtime that targets the Google Gemini API via pi-ai.

    Args:
        api_key:     Google API key. Falls back to ``GOOGLE_API_KEY``
                     or ``GOOGLE_GENERATIVE_AI_API_KEY``.
        model:       Model id under the ``google`` provider namespace.
        max_retries: Retry budget forwarded to base ``Runtime``.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-2.5-flash",
        max_retries: int = 2,
    ):
        if not api_key:
            from openprogram.providers.env_api_keys import resolve_provider_key
            api_key = resolve_provider_key("google")
        if not api_key:
            raise ValueError(
                "Google API key is required. Add one in Settings → "
                "Providers, pass api_key=, or set GEMINI_API_KEY / "
                "GOOGLE_API_KEY."
            )
        super().__init__(
            model=f"google:{model}",
            api_key=api_key,
            max_retries=max_retries,
        )

    def list_models(self) -> list[str]:
        """Return Gemini model ids known to the pi-ai registry."""
        from openprogram.providers.enabled_models import ENABLED_MODELS
        return sorted(
            m.id for m in ENABLED_MODELS.values() if m.provider == "google"
        )
