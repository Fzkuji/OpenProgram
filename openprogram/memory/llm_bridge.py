"""Glue between the memory subsystem and the LLM provider stack.

Sleep (deep / REM) and session-end summarization both need to call an
LLM but don't want to import the agent runtime. This module exposes
one function::

    callable_or_none = build_default_llm()

returning a ``(system_prompt, user_text) -> str`` callable that uses
the default agent's configured (provider, model). If no default agent
is set up yet the function returns ``None`` and callers should skip
the LLM phase gracefully.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

logger = logging.getLogger(__name__)


def _read_default_model() -> tuple[str, str] | None:
    """Resolve the default agent's (provider, model_id).

    Delegates to ``agents.manager.get_default()`` rather than re-reading
    ``agents.json`` by hand. The hand-rolled version returned None
    whenever the ``agents.json`` *index* file was absent — but a fresh
    install often has only the per-agent record
    (``agents/<id>/agent.json`` with ``"default": true``) and no index
    yet. ``manager.get_default()`` has the fallback chain (index →
    DEFAULT_AGENT_ID → first agent) that handles exactly that case, so
    the memory subsystem was silently disabled (build_default_llm →
    None → session-end ingest dropped every conversation) on any
    machine where the index hadn't been written. Reuse the one
    authoritative resolver instead of duplicating a buggy subset.
    """
    try:
        from openprogram.agents import manager as _agents
        spec = _agents.get_default()
    except Exception:
        return None
    if spec is None or spec.model is None:
        return None
    provider = (spec.model.provider or "").strip()
    model_id = (spec.model.id or "").strip()
    if not provider or not model_id:
        return None
    return provider, model_id


def build_default_llm() -> Callable[[str, str], str] | None:
    """Return a synchronous ``(system_prompt, user_text) -> str`` callable.

    Returns ``None`` if no default agent is configured or its provider /
    model cannot be resolved. The callable runs the provider's stream
    interface synchronously and concatenates ``text_delta`` events.
    """
    pair = _read_default_model()
    if pair is None:
        return None
    provider_name, model_id = pair

    try:
        from openprogram.providers.register import register_builtins
        register_builtins()
        from openprogram.providers.api_registry import get_api_provider
        from openprogram.providers.models import get_model
    except Exception as e:  # noqa: BLE001
        logger.debug("provider stack unavailable: %s", e)
        return None

    model = get_model(provider_name, model_id)
    if model is None:
        logger.debug("model %r/%r not found", provider_name, model_id)
        return None

    api_provider = get_api_provider(model.api)
    if api_provider is None:
        logger.debug("API provider %r not registered", model.api)
        return None

    # The claude-code provider (meridian / claude-max-api-proxy) routes
    # through the Claude Code SDK, which ignores the OpenAI ``system``
    # role and answers the user message in-character instead of following
    # instructions. Workaround: fold the system prompt into the user
    # turn for any provider known not to honour system messages.
    _proxy_providers = {"claude-code"}
    _inline_system = provider_name in _proxy_providers

    def _call(system_prompt: str, user_text: str) -> str:
        from openprogram.providers.types import (
            Context, SimpleStreamOptions, UserMessage,
        )
        import time
        if _inline_system and system_prompt:
            merged = system_prompt.rstrip() + "\n\n---\n\n" + user_text
            ctx_system = ""
            ctx_user = merged
        else:
            ctx_system = system_prompt
            ctx_user = user_text
        ctx = Context(
            system_prompt=ctx_system,
            messages=[
                UserMessage(content=ctx_user, timestamp=int(time.time() * 1000)),
            ],
            tools=[],
        )
        opts = SimpleStreamOptions(temperature=0.2, max_tokens=2000)

        async def _drive() -> str:
            stream = api_provider.stream_simple(model, ctx, opts)
            chunks: list[str] = []
            async for ev in stream:
                t = getattr(ev, "type", None)
                if t == "text_delta":
                    chunks.append(getattr(ev, "delta", "") or "")
                elif t == "done":
                    break
                elif t == "error":
                    raise RuntimeError(
                        getattr(getattr(ev, "error", None), "error_message", "llm error")
                    )
            return "".join(chunks)

        try:
            return asyncio.run(_drive())
        except RuntimeError as e:
            # asyncio.run inside a running loop — try a fresh loop.
            if "already running" not in str(e):
                raise
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_drive())
            finally:
                loop.close()

    return _call
