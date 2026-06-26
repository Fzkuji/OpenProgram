"""
Tier 5 Reactive compaction — emergency recovery when the LLM returns
a context overflow error (413 / prompt_too_long).

Attempts snip + auto-compact, then signals the caller to retry the
LLM call with the shortened context. At most one reactive attempt
per turn.
"""
from __future__ import annotations

import asyncio
import logging

from openprogram.providers.utils.overflow import is_context_overflow

log = logging.getLogger(__name__)


def is_overflow_error(exc: Exception) -> bool:
    """Check if an exception wraps a context overflow error."""
    msg_text = str(exc)
    from openprogram.providers.utils.overflow import OVERFLOW_PATTERNS, _STATUS_CODE_RE
    for p in OVERFLOW_PATTERNS:
        if p.search(msg_text):
            return True
    if _STATUS_CODE_RE.match(msg_text):
        return True
    return False


def reactive_compact(
    *,
    agent_profile: dict,
    session_id: str,
    model: str,
    history: list[dict],
    on_event,
    context_window: int | None = None,
) -> list[dict] | None:
    """Attempt emergency compaction after a context overflow error.

    Returns the compacted history if successful, None if compaction
    failed or didn't free enough space.
    """
    log.warning("Tier 5 Reactive: context overflow detected, attempting emergency compact")

    on_event({"type": "chat_response",
              "data": {"type": "reactive_compact_started",
                       "session_id": session_id}})

    # Step 1: Try snip first (free, no LLM)
    try:
        from openprogram.context.snip import snip
        from openprogram.context.tokens import count_tokens
        snipped, n_snipped = snip(
            history,
            token_counter=lambda msgs: count_tokens(msgs, model),
            context_window=context_window or 200_000,
        )
        if n_snipped > 0:
            log.info("Reactive snip removed %d turns", n_snipped)
            on_event({"type": "chat_response",
                      "data": {"type": "reactive_snip",
                               "session_id": session_id,
                               "turns_removed": n_snipped}})
            return snipped
    except Exception as e:
        log.warning("Reactive snip failed: %s", e)

    # Step 2: Try auto-compact (calls LLM)
    try:
        from openprogram.context import resolve_engine_for
        engine = resolve_engine_for(agent_profile)
        loop = asyncio.new_event_loop()
        try:
            compact_res = loop.run_until_complete(
                engine.compact(
                    agent=agent_profile,
                    session_id=session_id,
                    model=model,
                    on_event=on_event,
                    user_initiated=False,
                )
            )
        finally:
            loop.close()

        if compact_res.summary_id:
            log.info("Reactive auto-compact succeeded: %s", compact_res.summary_id)
            on_event({"type": "chat_response",
                      "data": {"type": "reactive_compact_done",
                               "session_id": session_id,
                               "summary_id": compact_res.summary_id}})
            from openprogram.agent.session_db import default_db
            db = default_db()
            new_history = db.get_branch(session_id)
            if new_history:
                return new_history
    except Exception as e:
        log.warning("Reactive auto-compact failed: %s", e)

    on_event({"type": "chat_response",
              "data": {"type": "reactive_compact_failed",
                       "session_id": session_id}})
    return None
