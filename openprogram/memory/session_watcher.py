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
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from openprogram import _compat as fcntl

from . import store
from .provider import WriteFailure

logger = logging.getLogger(__name__)

DEFAULT_IDLE_MINUTES = 30
DEFAULT_POLL_INTERVAL = 300  # seconds — 5 min


SESSION_PAGE = 500


class WatcherStateError(RuntimeError):
    """The persisted watcher state is unreadable and was not overwritten."""


def _processed_path() -> Path:
    return store.state_dir() / "session-end.json"


def _watcher_lock_path() -> Path:
    return store.state_dir() / "session-end.lock"


@contextmanager
def watcher_lock(*, timeout_s: float = 0.0):
    """Exclusive cross-process claim on one watcher pass.

    Two workers polling the same session DB would otherwise both call the
    writer for the same idle session, paying twice for one write and
    racing each other's processed state.
    """
    path = _watcher_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise WatcherStateError(
                        "another memory watcher holds the session-end lock"
                    ) from None
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        os.close(handle)


def _load_processed() -> dict[str, float]:
    """Terminal outcomes recorded so far.

    A corrupt file is reported rather than treated as an empty set: read as
    empty, every session in it would be handed to the model again, which is
    the expensive way to lose this file.
    """
    p = _processed_path()
    if not p.exists():
        return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WatcherStateError(f"watcher state is unreadable: {p}") from exc
    if not isinstance(payload, dict):
        raise WatcherStateError(f"watcher state is not an object: {p}")
    return {
        str(key): float(value)
        for key, value in payload.items()
        if isinstance(value, (int, float))
    }


def _save_processed(state: dict[str, float]) -> None:
    """Replace the state file whole: write, flush, fsync, rename.

    An interrupted write must not leave half a JSON document behind, so
    nothing is ever written into the real path directly.
    """
    path = _processed_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix="session-end-", suffix=".json.tmp", dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(state, indent=2, ensure_ascii=False))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _all_sessions(db: Any) -> list[dict[str, Any]]:
    """Every session, paged, so the list size cannot silently drop old ones.

    A single ``limit=500`` call returns the 500 most recently updated
    sessions — which are exactly the ones least likely to be idle.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    offset = 0
    while True:
        page = db.list_sessions(limit=SESSION_PAGE, offset=offset)
        if not page:
            return rows
        for row in page:
            session_id = str(row.get("id") or "")
            if session_id and session_id not in seen:
                seen.add(session_id)
                rows.append(row)
        if len(page) < SESSION_PAGE:
            return rows
        offset += SESSION_PAGE


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
    with watcher_lock():
        return _scan_locked(db, idle_minutes)


def _scan_locked(db: Any, idle_minutes: int) -> int:
    cutoff = time.time() - idle_minutes * 60
    processed = _load_processed()
    n_done = 0
    for s in _all_sessions(db):
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
            _save_processed(processed)
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
        elif left.retryable:
            # Nothing terminal happened, so nothing is recorded: the next
            # poll offers this session again.
            logger.info(
                "memory: write incomplete for %s (%s); will retry",
                sid, left.reason,
            )
            continue
        else:
            # Offering it again produces the same refusal, so stop
            # offering it. The event above is what says so out loud.
            logger.warning(
                "memory: giving up on %s (%s)", sid, left.reason
            )
        # A terminal outcome is persisted before the next session is
        # touched: a crash mid-pass must not replay a model call that
        # already happened.
        processed[sid] = updated_at
        _save_processed(processed)
    return n_done


def _process_session(
    session_id: str, messages: list[dict[str, Any]]
) -> WriteFailure | None:
    """Write an idle conversation into memory.

    Returns nothing once the session is written — the caller marks it
    processed. A ``WriteFailure`` carries why it is not, and whether
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
        left = get_provider().write(
            messages, session_id=session_id, force=True,
        )
    except Exception as exc:  # noqa: BLE001
        # Only an explicit transient verdict justifies another poll. An
        # unclassified exception may be a permanent config/auth failure.
        logger.info("memory: write deferred for %s (%s)", session_id, exc)
        verdict = getattr(exc, "retryable", None)
        left = WriteFailure(
            str(exc), retryable=False if verdict is None else bool(verdict),
            reason_code="WRITER_FAILURE_UNKNOWN",
        )
    if left is not None:
        from .scriptorium.runtime.writer_status import (
            record_active_workspace_failure,
        )

        record_active_workspace_failure(
            getattr(left, "reason_code", None),
            retryable=left.retryable,
        )
    return left


def run_now(*, idle_minutes: int = DEFAULT_IDLE_MINUTES) -> int:
    """Manual entry point — process every idle session right now."""
    return _scan(idle_minutes)
