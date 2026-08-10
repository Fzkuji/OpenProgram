"""Session watcher — forces a memory write when a conversation goes idle.

Polls the session DB every ``poll_interval`` seconds. For any session
whose ``updated_at`` exceeds ``idle_minutes`` and which we haven't
already handled, hands the message list to the memory provider's
``write(..., force=True)``, which runs the writer over whatever the
per-turn path left behind.

State (already-processed session IDs and their last update timestamp)
lives at ``<state>/memory/.scriptorium/session-end.json`` so a worker
restart doesn't re-process every session. It goes in the runtime
directory rather than beside the memory: it changes on every poll, and
anything holding a workspace revision would read that as a concurrent
write.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from . import store
from .provider import WriteIncomplete

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


def start_idle_session_watcher(
    *,
    idle_minutes: int = DEFAULT_IDLE_MINUTES,
    poll_interval: int = DEFAULT_POLL_INTERVAL,
) -> threading.Thread | None:
    """Spawn the watcher thread. Returns the thread or None if disabled."""
    import os
    from . import is_enabled

    if not is_enabled():
        logger.info("memory session-end watcher disabled by memory.backend=none")
        return None
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
        left = _process_session(sid, messages)
        # 事件层 tap：空闲会话写入记忆的起止（B 类）。懒 import 防循环。
        try:
            from openprogram.events import emit_safe
            emit_safe("memory.ingest_ended", "system", {
                "ok": left is None,
                "retryable": left is not None and left.retryable,
                "reason": left.reason if left else "",
            }, {"session": sid})
        except Exception:
            pass
        if left is None:
            n_done += 1
            processed[sid] = updated_at
        elif not left.retryable:
            # Offering it again produces the same refusal, so stop
            # offering it. The event above is what says so out loud.
            logger.warning(
                "memory: giving up on %s (%s)", sid, left.reason
            )
            processed[sid] = updated_at
        else:
            logger.info(
                "memory: write incomplete for %s (%s); will retry",
                sid, left.reason,
            )
    _save_processed(processed)
    return n_done


def _process_session(
    session_id: str, messages: list[dict[str, Any]]
) -> WriteIncomplete | None:
    """Write an idle conversation into memory.

    Returns nothing once the session is written — the caller marks it
    processed. A ``WriteIncomplete`` carries why it is not, and whether
    a later poll could finish it.

    Per-turn writing already handles a live session once enough has
    gathered; this catches the remainder, which would otherwise sit
    unwritten because the conversation ended below the threshold. The
    cursor makes the overlap harmless: whatever the live path already
    wrote is skipped here.
    """
    try:
        from openprogram.events import emit_safe
        emit_safe("memory.ingest_started", "system",
                  {"messages": len(messages)}, {"session": session_id})
    except Exception:
        pass

    try:
        from . import get_provider
        return get_provider().write(
            messages, session_id=session_id, force=True,
        )
    except Exception as exc:  # noqa: BLE001
        # A provider that lets an exception out says nothing about
        # whether the next poll would fare better, and the conversation
        # is safe in the session store either way — so retry.
        logger.info("memory: write deferred for %s (%s)", session_id, exc)
        return WriteIncomplete(str(exc))


def run_now(*, idle_minutes: int = DEFAULT_IDLE_MINUTES) -> int:
    """Manual entry point — process every idle session right now."""
    return _scan(idle_minutes)
