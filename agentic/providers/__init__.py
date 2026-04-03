"""
agentic.providers — Built-in Runtime implementations for popular LLM providers.

Each provider is an optional dependency. Import will give a clear error
if the required SDK is not installed.

Available providers:
    AnthropicRuntime  — Anthropic Claude (text + image, prompt caching)
    OpenAIRuntime     — OpenAI GPT (text + image, response_format)
    GeminiRuntime     — Google Gemini (text + image)

Usage:
    from agentic.providers import AnthropicRuntime
    rt = AnthropicRuntime(api_key="sk-...", model="claude-sonnet-4-20250514")

    from agentic.providers import OpenAIRuntime
    rt = OpenAIRuntime(api_key="sk-...", model="gpt-4o")

    from agentic.providers import GeminiRuntime
    rt = GeminiRuntime(api_key="...", model="gemini-2.5-flash")
"""


def __getattr__(name):
    """Lazy imports — only load a provider when accessed."""
    if name == "AnthropicRuntime":
        from agentic.providers.anthropic import AnthropicRuntime
        return AnthropicRuntime
    if name == "OpenAIRuntime":
        from agentic.providers.openai import OpenAIRuntime
        return OpenAIRuntime
    if name == "GeminiRuntime":
        from agentic.providers.gemini import GeminiRuntime
        return GeminiRuntime
    raise AttributeError(f"module 'agentic.providers' has no attribute {name!r}")


__all__ = ["AnthropicRuntime", "OpenAIRuntime", "GeminiRuntime"]
