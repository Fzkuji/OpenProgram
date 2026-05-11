"""Backward-compat shim.

``ChatGPTSubscriptionRuntime`` lives in ``openprogram.providers.openai_codex``.
Import from there in new code. The old name ``ChatGPTSubscriptionRuntime`` is kept as
an alias for backward compatibility.
"""
from openprogram.providers.openai_codex.runtime import ChatGPTSubscriptionRuntime

# Backward-compat alias
ChatGPTSubscriptionRuntime = ChatGPTSubscriptionRuntime

__all__ = ["ChatGPTSubscriptionRuntime", "ChatGPTSubscriptionRuntime"]
