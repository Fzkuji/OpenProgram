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
import json
import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def _read_default_model() -> tuple[str, str] | None:
    from openprogram.paths import get_state_dir
    state = get_state_dir()
    agents_meta = state / "agents.json"
    if not agents_meta.exists():
        return None
    try:
        meta = json.loads(agents_meta.read_text(encoding="utf-8"))
    except Exception:
        return None
    default_id = meta.get("default_id") or (meta.get("order") or [None])[0]
    if not default_id:
        return None
    agent_file = state / "agents" / default_id / "agent.json"
    if not agent_file.exists():
        return None
    try:
        agent = json.loads(agent_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    model = agent.get("model") or {}
    provider = model.get("provider")
    model_id = model.get("id")
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

    def _call(system_prompt: str, user_text: str) -> str:
        from openprogram.providers.types import (
            Context, SimpleStreamOptions, UserMessage,
        )
        import time
        ctx = Context(
            system_prompt=system_prompt,
            messages=[
                UserMessage(content=user_text, timestamp=int(time.time() * 1000)),
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
