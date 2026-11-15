"""The prompt-cache key of a call that names no conversation.

A title, a topic sort, a memory step, a permission check or a summary has
no conversation of its own, but calls of one kind repeat their model,
instructions and tools. Keyed by those, they reach the replica that cached
that prefix instead of a random one. One derivation for every such call:
a ``Runtime`` call (``runtime.agent.runtime.providers._provider_session_id``)
and a call made straight to an adapter (``openai_codex._build_request_body``)
with the same prefix get the same key.

The key only routes the request and names its cache; no credential is
chosen by it. A Runtime call also passes it as its ``session_id`` option,
which OpenCode receives as its session header (``opencode_headers``); a call
made straight to an adapter leaves that option unset, as before.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


def prefix_cache_key(provider: Any, model_id: Any, system_prompt: Any,
                     tool_names: Iterable[Any]) -> str:
    """``op-`` and a hash of the call's model, instructions and tool names."""
    stable = json.dumps(
        [provider, model_id, system_prompt or "", list(tool_names)],
        ensure_ascii=False, separators=(",", ":"),
    )
    return "op-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]


__all__ = ["prefix_cache_key"]
