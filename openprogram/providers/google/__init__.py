"""Google Generative AI provider."""
from .google import stream_simple

__all__ = ["stream_simple", "GeminiRuntime"]


def __getattr__(name: str):
    if name == "GeminiRuntime":
        from .runtime import GeminiRuntime
        return GeminiRuntime
    raise AttributeError(f"module 'openprogram.providers.google' has no attribute {name!r}")
