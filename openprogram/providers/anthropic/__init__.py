"""Anthropic provider — Messages API, direct for both anthropic & claude-code.

Two providers share this wire:
  * ``anthropic`` (:class:`AnthropicRuntime`) — api.anthropic.com with an
    API key.
  * ``claude-code`` (:class:`ClaudeCodeRuntime`) — api.anthropic.com with a
    Claude SUBSCRIPTION OAuth token (Bearer + Claude Code beta headers),
    the same shape as openai-codex. No Meridian daemon. The model list comes
    from a live Fetch against /v1/models; a small seed keeps it visible in
    the UI before the first Fetch.
"""
from .anthropic import stream_simple
from .runtime import AnthropicRuntime
from ._claude_code_direct_runtime import ClaudeCodeRuntime
# Side-effect import: seeds the claude-code provider (DIRECT, anthropic-messages
# wire) into MODEL_REGISTRY so it appears in the UI picker even before a Fetch.
# (The retired Meridian-proxy seed has been removed entirely.)
from . import _claude_code_registry  # noqa: F401

__all__ = [
    "stream_simple",
    "AnthropicRuntime",
    "ClaudeCodeRuntime",
]
