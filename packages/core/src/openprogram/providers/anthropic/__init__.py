"""Anthropic provider — Messages API, direct for both anthropic & claude-code.

Two providers share this wire:
  * ``anthropic`` — api.anthropic.com with an API key. Served by the base
    ``Runtime`` via ``create_runtime(provider="anthropic")`` (which resolves
    the credential and builds ``Runtime("anthropic:<id>")``) — no dedicated
    class.
  * ``claude-code`` (:class:`ClaudeCodeRuntime`) — api.anthropic.com with a
    Claude SUBSCRIPTION OAuth token (Bearer + Claude Code beta headers),
    the same shape as openai-codex. No Meridian daemon. The registry is
    built from config spec rows only; the default claude-code model set is
    enabled at login (``openprogram.auth.login_seed_models``), and the model
    list refreshes via a live Fetch against /v1/models.
"""
from .anthropic import stream_simple
from ._claude_code_direct_runtime import ClaudeCodeRuntime

__all__ = [
    "stream_simple",
    "ClaudeCodeRuntime",
]
