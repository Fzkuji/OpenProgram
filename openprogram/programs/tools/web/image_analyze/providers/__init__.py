"""Builtin image_analyze providers (OpenAI / Anthropic / Gemini)."""

from __future__ import annotations

from ..registry import registry
from .anthropic import AnthropicVisionProvider
from .gemini import GeminiVisionProvider
from .openai import OpenAIVisionProvider


def _register_builtins() -> None:
    # OpenAI first because gpt-4o-mini is cheap + fast and most users
    # already have an OPENAI_API_KEY; Anthropic second because Claude
    # vision reads well on typography / diagrams; Gemini third (also
    # strong; ordering is arbitrary among the three).
    registry.register(OpenAIVisionProvider())
    registry.register(AnthropicVisionProvider())
    registry.register(GeminiVisionProvider())


_register_builtins()


__all__ = [
    "AnthropicVisionProvider",
    "GeminiVisionProvider",
    "OpenAIVisionProvider",
]
