"""OpenAI Responses API provider.

The ``openai`` provider is served by the base ``Runtime`` via
``create_runtime(provider="openai")`` (which resolves the API key and
builds ``Runtime("openai:<id>")``) — no dedicated class.
"""
from .openai_responses import stream_openai_responses, stream_simple_openai_responses

__all__ = [
    "stream_openai_responses",
    "stream_simple_openai_responses",
]
