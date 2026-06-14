"""Session titling + user-initiated compaction.

Extracted from dispatcher/__init__.py (dispatcher-split step 2):

  _default_title       first-line title for a brand-new session row
  _maybe_auto_title    idempotent title backfill on the first text turn
  trigger_compaction   user-clicks-/compact path (public; webui imports it)

Leaf module: depends only on the stdlib, ``types`` (TurnRequest /
EventCallback / _noop), and ``_model_tools`` (profile + model resolution,
the same source ``__init__`` uses). The package ``__init__`` re-exports
these so existing callers — ``from openprogram.agent.dispatcher import
trigger_compaction`` (webui/ws_actions/chat.py) and the dispatcher tests'
``D.trigger_compaction`` — resolve unchanged.

See docs/design/runtime/dispatcher-split.md.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from openprogram.agent.dispatcher.types import EventCallback, TurnRequest, _noop
from openprogram.agent.internals._model_tools import (
    load_agent_profile as _load_agent_profile,
    resolve_model as _resolve_model,
)


def _default_title(req: TurnRequest) -> str:
    text = req.user_text.strip().splitlines()[0] if req.user_text else ""
    return text[:50] + ("…" if len(text) > 50 else "") or "New chat"


def _maybe_auto_title(db, session_id: str, session: dict,
                      user_text: str) -> None:
    """Stamp a readable session title once, on the first turn that
    has a non-empty user message. Idempotent — once
    ``extra_meta._titled`` is True we never touch the title again so
    user-set titles via /rename win.

    Skips when:
      - The session was never created (missing row)
      - User already explicitly titled it
      - This wasn't a real text turn (e.g. tool-only follow-up)
    """
    extra = (session.get("extra_meta") or {})
    if extra.get("_titled"):
        return
    stripped = (user_text or "").strip()
    if not stripped:
        return
    text = stripped.splitlines()[0] if stripped.splitlines() else stripped
    if not text:
        return
    title = text[:50] + ("…" if len(text) > 50 else "")
    try:
        db.update_session(session_id, title=title, _titled=True)
    except Exception:
        pass


def trigger_compaction(session_id: str, agent_id: str = "main",
                        on_event: Optional[EventCallback] = None,
                        *,
                        keep_recent_tokens: Optional[int] = None) -> dict:
    """User-initiated compaction. Synchronous — the caller is responsible
    for running this off the request thread if it cares about latency
    (compaction calls the LLM to generate a summary).

    Pipeline:
      1. Load active branch from SessionDB.
      2. Run compact_context to get summary text + recent kept tail.
      3. Persist a synthetic ``compactionSummary`` row chained off
         the current head's parent (so it sits at the same fork
         point as the original first kept message).
      4. set_head to the new summary row.
      5. Re-link the kept tail: each kept message gets a new id and
         parent_id pointing back through the new chain.

    Mirrors Claude Code's compaction model (a real "summary" message
    in the transcript) but stays SQL-native — no JSONL fork needed.
    Old pre-summary messages remain in SessionDB but are off the
    active branch (you can still get_descendants from them for
    audit).

    Returns ``{"summary": str, "kept_count": int, "summary_id": str}``.
    """
    on_event = on_event or _noop
    from openprogram.agent.session_db import default_db
    from openprogram.context import resolve_engine_for

    db = default_db()
    sess = db.get_session(session_id)
    if sess is None:
        raise ValueError(f"Unknown conversation {session_id!r}")
    history = db.get_branch(session_id) or []
    if len(history) < 4:
        return {"summary": "", "kept_count": len(history), "summary_id": ""}

    profile = _load_agent_profile(agent_id)
    model = _resolve_model(profile, None)
    engine = resolve_engine_for(profile)

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            engine.compact(
                agent=profile,
                session_id=session_id,
                model=model,
                on_event=on_event,
                user_initiated=True,
                keep_recent_tokens=keep_recent_tokens,
            )
        )
    finally:
        loop.close()

    return {
        "summary": result.summary_text or "",
        "kept_count": result.summarised_count,
        "summary_id": result.summary_id or "",
    }
