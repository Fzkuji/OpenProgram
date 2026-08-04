"""Anthropic provider — Messages API, direct for both anthropic & claude-code.

Two providers share this wire:
  * ``anthropic`` (:class:`AnthropicRuntime`) — api.anthropic.com with an
    API key.
  * ``claude-code`` (:class:`ClaudeCodeRuntime`) — api.anthropic.com with a
    Claude SUBSCRIPTION OAuth token (Bearer + Claude Code beta headers),
    the same shape as openai-codex. No Meridian daemon. The registry is
    built from config spec rows only; the default claude-code model set is
    enabled at login (``openprogram.auth.login_enable``), and the model
    list refreshes via a live Fetch against /v1/models.
"""
from .anthropic import stream_simple
from .runtime import AnthropicRuntime
from ._claude_code_direct_runtime import ClaudeCodeRuntime

__all__ = [
    "stream_simple",
    "AnthropicRuntime",
    "ClaudeCodeRuntime",
]
