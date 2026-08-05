"""Google Generative AI provider.

The ``gemini`` provider is served by the base ``Runtime`` via
``create_runtime(provider="gemini")`` (which resolves the API key and
builds ``Runtime("google:<id>")``) — no dedicated class.
"""
from .google import stream_simple

__all__ = ["stream_simple"]
