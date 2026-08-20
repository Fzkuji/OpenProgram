"""Builtin image_generate providers (OpenAI, Gemini, FAL)."""

from __future__ import annotations

from ..registry import registry
from .fal import FalProvider
from .gemini import GeminiImagenProvider
from .openai import OpenAIImageProvider


def _register_builtins() -> None:
    # Priority order: OpenAI (reliable, widely-known quality) → Gemini
    # (Imagen-3 is strong on photorealism) → FAL (cheapest, widest
    # model menu but slower to cold-start).
    registry.register(OpenAIImageProvider())
    registry.register(GeminiImagenProvider())
    registry.register(FalProvider())


_register_builtins()


__all__ = ["FalProvider", "GeminiImagenProvider", "OpenAIImageProvider"]
