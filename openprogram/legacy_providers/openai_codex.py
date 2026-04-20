"""Backward-compat shim.

``OpenAICodexRuntime`` moved to ``openprogram.providers.openai_codex``.
Import from there in new code.
"""
from openprogram.providers.openai_codex.runtime import OpenAICodexRuntime

__all__ = ["OpenAICodexRuntime"]
