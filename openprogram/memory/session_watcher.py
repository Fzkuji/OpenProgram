"""Session watcher — fires on_session_end when a conversation goes idle.

Polls the session DB every ``poll_interval`` seconds. For any session
whose ``updated_at`` exceeds ``idle_minutes`` and which we haven't
already processed, hands the message list to the builtin provider's
``on_session_end`` (which runs the LLM summarizer and appends short-term
notes).

State (already-processed session IDs and their last update timestamp)
lives at ``<state>/memory/.state/session-end.json`` so a worker
restart doesn't re-process every session.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from . import store

logger = logging.getLogger(__name__)

DEFAULT_IDLE_MINUTES = 30
DEFAULT_POLL_INTERVAL = 300  # seconds — 5 min


def _processed_path() -> Path:
    return store.state_dir() / "session-end.json"


def _load_processed() -> dict[str, float]:
    p = _processed_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_processed(state: dict[str, float]) -> None:
    _processed_path().write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def start_in_worker(
    *,
    idle_minutes: int = DEFAULT_IDLE_MINUTES,
    poll_interval: int = DEFAULT_POLL_INTERVAL,
) -> threading.Thread | None:
    """Spawn the watcher thread. Returns the thread or None if disabled."""
    import os
    if os.environ.get("OPENPROGRAM_NO_SESSION_END", "").strip() in ("1", "true", "yes"):
        logger.info("memory session-end watcher disabled by env")
        return None

    def _loop() -> None:
        # Initial wait so we don't process freshly-resumed sessions.
        time.sleep(poll_interval)
        while True:
            try:
                _scan(idle_minutes)
            except Exception as e:  # noqa: BLE001
                logger.debug("session-end scan failed: %s", e)
            time.sleep(poll_interval)

    t = threading.Thread(target=_loop, name="memory-session-end", daemon=True)
    t.start()
    return t


def _scan(idle_minutes: int) -> int:
    """One pass. Returns number of sessions processed."""
    try:
        from openprogram.agent.session_db import default_db
    except Exception:
        return 0
    db = default_db()
    cutoff = time.time() - idle_minutes * 60
    processed = _load_processed()
    sessions = db.list_sessions(limit=500)
    n_done = 0
    for s in sessions:
        sid = s.get("id")
        updated_at = float(s.get("updated_at", 0))
        if not sid or updated_at == 0:
            continue
        if updated_at > cutoff:
            # Still active; skip.
            continue
        if processed.get(sid) == updated_at:
            # Already processed at this exact updated_at.
            continue
        try:
            messages = db.get_branch(sid)
        except Exception:
            continue
        if not messages:
            processed[sid] = updated_at
            continue
        if _process_session(sid, messages):
            n_done += 1
            processed[sid] = updated_at
    _save_processed(processed)
    return n_done


def _process_session(session_id: str, messages: list[dict[str, Any]]) -> bool:
    """Hand messages to the builtin provider's on_session_end.

    Returns True on success — caller marks the session as processed.
    Returns False on retryable failure (LLM call failed, network error,
    etc.) so the next poll tries again.
    """
    try:
        from .builtin import BuiltinMemoryProvider
        from .llm_bridge import build_default_llm
    except Exception:
        return False
    llm = build_default_llm()
    if llm is None:
        # No LLM configured at all — safe to mark processed; we'd just
        # loop on this same session forever otherwise.
        return True
    provider = BuiltinMemoryProvider()
    provider.initialize(session_id=session_id, summarize_model=llm)
    # Wrap the LLM call so we can distinguish "transient API failure"
    # (don't mark processed — retry next poll) from "no extractable
    # facts" (do mark processed).
    last_error: Exception | None = None

    def _wrapped_llm(system_prompt: str, user_text: str) -> str:
        nonlocal last_error
        try:
            return llm(system_prompt, user_text)
        except Exception as e:  # noqa: BLE001
            last_error = e
            raise

    provider._summarize_model = _wrapped_llm
    try:
        provider.on_session_end(messages)
    except Exception as e:  # noqa: BLE001
        logger.warning("on_session_end failed for %s: %s", session_id, e)
        return False
    if last_error is not None:
        # The provider swallowed the error inside on_session_end (it
        # logs and returns); re-surface it so we don't mark processed.
        logger.info("memory: %s session %s (will retry next poll)", last_error, session_id)
        return False
    logger.info("memory: extracted facts from idle session %s (msgs=%d)", session_id, len(messages))
    return True


def run_now(*, idle_minutes: int = DEFAULT_IDLE_MINUTES) -> int:
    """Manual entry point — process every idle session right now."""
    return _scan(idle_minutes)
