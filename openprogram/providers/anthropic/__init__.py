"""Anthropic provider — Messages API + Claude Max API proxy.

The legacy Claude Code CLI provider (``ClaudeCodeRuntime``) and its
plugin scaffolding (``CLAUDE_CODE_PLUGIN`` / ``CLAUDE_CODE_CONFIG``)
have been removed. Anthropic access now goes exclusively through
HTTP — either ``api.anthropic.com`` directly (:class:`AnthropicRuntime`)
or a local ``claude-max-api-proxy`` daemon for Max-plan users
(:class:`ClaudeCodeRuntime`). Both share OpenProgram's tool
registry, so the long-standing asymmetry where the CLI provider
silently shipped its own Read/Write/Edit tools is gone.
"""
from .anthropic import stream_simple
from .runtime import AnthropicRuntime
from ._max_proxy_runtime import ClaudeCodeRuntime
# Side-effect import: registers Claude models under provider
# `claude-max-proxy` in the global MODELS registry so the UI picker
# surfaces them.
from . import _claude_max_proxy_registry  # noqa: F401

__all__ = [
    "stream_simple",
    "AnthropicRuntime",
    "ClaudeCodeRuntime",
]
