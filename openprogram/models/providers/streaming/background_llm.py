"""A plain text-in/text-out callable on the last used model.

Turn titling, memory, commit messages and MCP sampling have no
conversation of their own, so they run on the model the user ran last.
There is no default model: with nothing used yet ``build_background_llm``
returns ``None`` and callers skip their LLM step.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

logger = logging.getLogger(__name__)


def background_model() -> tuple[str, str] | None:
    """``(provider, model_id)`` background work runs on: the last used model."""
    try:
        from openprogram.models.providers.catalog.last_used import read_last_used_model
        return read_last_used_model()
    except Exception:
        return None


def build_background_llm(call_kind: str = "memory", max_tokens: int = 2000) -> Callable[[str, str], str] | None:
    """Return a synchronous ``(system_prompt, user_text) -> str`` callable.

    Returns ``None`` if no model has been used yet or it cannot be
    resolved. The callable runs the provider's stream
    interface synchronously and concatenates ``text_delta`` events.
    """
    pair = background_model()
    if pair is None:
        return None
    provider_name, model_id = pair

    try:
        from openprogram.models.providers.catalog.register import register_builtins
        register_builtins()
        from openprogram.models.providers.catalog.models import get_model
    except Exception as e:  # noqa: BLE001
        logger.debug("provider stack unavailable: %s", e)
        return None

    model = get_model(provider_name, model_id)
    if model is None:
        logger.debug("model %r/%r not found", provider_name, model_id)
        return None

    def _call(system_prompt: str, user_text: str) -> str:
        from openprogram.models.providers import stream_simple as _stream_simple
        from openprogram.models.providers.types import (
            Context, SimpleStreamOptions, UserMessage,
        )
        from openprogram.models.usage import usage_scope
        import time
        ctx_system = system_prompt
        ctx_user = user_text
        ctx = Context(
            system_prompt=ctx_system,
            messages=[
                UserMessage(content=ctx_user, timestamp=int(time.time() * 1000)),
            ],
            tools=[],
        )
        opts = SimpleStreamOptions(temperature=0.2, max_tokens=max_tokens)

        async def _drive() -> str:
            chunks: list[str] = []
            with usage_scope(call_kind=call_kind):
                async for ev in _stream_simple(model, ctx, opts):
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
