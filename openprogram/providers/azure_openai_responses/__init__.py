"""Azure OpenAI Responses API provider."""
from .azure_openai_responses import (
    stream_azure_openai_responses,
    stream_simple_azure_openai_responses,
)

__all__ = ["stream_azure_openai_responses", "stream_simple_azure_openai_responses"]
