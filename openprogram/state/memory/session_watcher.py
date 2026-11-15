"""Session watcher — forces a memory write when a conversation goes idle.

Polls the session DB every ``poll_interval`` seconds. For any session
whose ``updated_at`` exceeds ``idle_minutes`` and which we haven't
already handled, hands the message list to the memory backend's
``write(..., force=True)``, which runs the writer over whatever the
per-turn path left behind.

Everything the watcher keeps lives in one file, ``ingest-state.json`` in
the personal memory's runtime directory: conversations may move between
projects, so their bookkeeping cannot live in any one project's memory.
The runtime directory is outside the workspace revision, so a file that
changes on every poll never looks like a concurrent write.

A failed write is recorded at the scope of its cause:

- the conversation, keyed by its watermark (``updated_at``): a failure
  that comes back for the same input is offered at most
  ``SESSION_MAX_ATTEMPTS`` times, ``SESSION_RETRY_SPACING`` apart, and
  again once the conversation moves on;
- the memory it writes to: recorded once, and every conversation writing
  there is passed over until that memory is edited, the configuration
  changes or the process restarts;
- the writer's model sign-in: recorded once, and every conversation is
  passed over until a sign-in or the configuration changes;
- nothing, for a cause that ends by itself: the wait doubles from the
  poll interval up to six hours and never gives up.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from openprogram.host._platform import locking

from . import store
from .backend import MemoryWriteFailureCode, WriteFailure
from openprogram.host._platform.filesystem import replace_file

logger = logging.getLogger(__name__)

DEFAULT_IDLE_MINUTES = 30
DEFAULT_POLL_INTERVAL = 300  # seconds — 5 min

STATE_FILE = "ingest-state.json"
STATE_VERSION = 1
# What this file replaced; read once, then removed.
LEGACY_PROCESSED_FILE = "session-end.json"
LEGACY_RETRIES_FILE = "session-end-retries.json"

# A cause that ends by itself: the wait doubles up to RETRY_MAX_DELAY.
RETRY_BASE_DELAY = DEFAULT_POLL_INTERVAL
RETRY_MAX_DELAY = 6 * 3600
# A cause in the conversation: the same input fails the same way, so it
# gets a few tries an hour apart, then waits for the conversation to move.
SESSION_MAX_ATTEMPTS = 3
SESSION_RETRY_SPACING = 3600

# Causes that end by themselves: a provider that is down, slow or rate
# limited, a lock another writer holds. An idle conversation never changes
# again, so giving up on one of these would lose it for good once the
# outage is over; they keep the longest wait instead.
TRANSIENT_REASON_CODES = frozenset({
    MemoryWriteFailureCode.MODEL_TRANSPORT,
    MemoryWriteFailureCode.MODEL_RATE_LIMIT,
    MemoryWriteFailureCode.MODEL_PROVIDER_INTERNAL,
    MemoryWriteFailureCode.MODEL_TIMEOUT,
    MemoryWriteFailureCode.CONCURRENT_UPDATE,
    MemoryWriteFailureCode.EMBEDDING_UNAVAILABLE,
})
# Causes in the writer's sign-in, the same for every conversation.
CREDENTIAL_REASON_CODES = frozenset({
    MemoryWriteFailureCode.MODEL_AUTHENTICATION,
    MemoryWriteFailureCode.MODEL_AUTHORIZATION,
})
# Causes in the memory being written to, the same for every conversation
# that writes there: committed files the writer cannot start from, a Git
# history that refuses commits.
WORKSPACE_REASON_CODES = frozenset({
    MemoryWriteFailureCode.WRITER_PRECONDITION_FAILED,
    MemoryWriteFailureCode.GIT_COMMIT_FAILED,
})
# One per process: a memory stopped in an earlier process gets one more
# try in this one.
_BOOT = uuid.uuid4().hex


SESSION_PAGE = 500


class WatcherStateError(RuntimeError):
    """The persisted watcher state is unreadable and was not overwritten."""


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
                locking.flock(handle, locking.LOCK_EX | locking.LOCK_NB)
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
                locking.flock(handle, locking.LOCK_UN)
            except OSError:
                pass
    finally:
        os.close(handle)


def _state_path() -> Path:
    # The personal memory's runtime directory, named directly: the
    # status of any memory, project ones included, reads it from here.
    from openprogram.state.paths import get_memory_dir

    from .workspace_layout import ensure_runtime_dir

    return ensure_runtime_dir(get_memory_dir()) / STATE_FILE


def _empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION, "processed": {}, "sessions": {},
        "workspaces": {}, "credentials": {},
    }


def _valid_session(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("updated_at"), (int, float))
        and isinstance(value.get("attempts"), int)
        and isinstance(value.get("next_at"), (int, float))
    )


def _legacy_state(directory: Path) -> dict[str, Any] | None:
    """The two files this one replaced, or None when neither is there."""
    processed_path = directory / LEGACY_PROCESSED_FILE
    retries_path = directory / LEGACY_RETRIES_FILE
    if not processed_path.exists() and not retries_path.exists():
        return None
    state = _empty_state()
    if processed_path.exists():
        try:
            payload = json.loads(processed_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WatcherStateError(
                f"watcher state is unreadable: {processed_path}"
            ) from exc
        if not isinstance(payload, dict):
            raise WatcherStateError(
                f"watcher state is not an object: {processed_path}"
            )
        state["processed"] = {
            str(key): float(value) for key, value in payload.items()
            if isinstance(value, (int, float))
        }
    try:
        retries = json.loads(retries_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        retries = {}
    for session_id, entry in (retries if isinstance(retries, dict) else {}).items():
        if not _valid_session(entry):
            continue
        # The old files stopped by another rule, and on causes that now
        # belong to the sign-in or the memory: nothing comes over stopped.
        # A sign-in or memory cause is dropped, so the session is offered
        # once more and a still-present cause lands in its own scope; any
        # other keeps its wait with one try left.
        code = entry.get("reason_code")
        if code in CREDENTIAL_REASON_CODES or code in WORKSPACE_REASON_CODES:
            continue
        transient = code in TRANSIENT_REASON_CODES
        attempts = int(entry["attempts"])
        state["sessions"][str(session_id)] = {
            **entry,
            "failures": 0 if transient else min(attempts, SESSION_MAX_ATTEMPTS - 1),
            "backoff": attempts if transient else 0,
            "stopped": False,
        }
    return state


def load_state() -> dict[str, Any]:
    """Everything the watcher has recorded.

    A corrupt file is reported rather than treated as empty: read as
    empty, every session it names would be handed to the model again,
    which is the expensive way to lose this file. The files it replaced
    are read once, on the first load, and removed once it is written.
    """
    path = _state_path()
    if not path.exists():
        legacy = _legacy_state(path.parent)
        if legacy is None:
            return _empty_state()
        _save_state(legacy)
        for name in (LEGACY_PROCESSED_FILE, LEGACY_RETRIES_FILE):
            (path.parent / name).unlink(missing_ok=True)
        return legacy
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WatcherStateError(f"watcher state is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise WatcherStateError(f"watcher state is not an object: {path}")
    state = _empty_state()
    processed = payload.get("processed")
    if isinstance(processed, dict):
        state["processed"] = {
            str(key): float(value) for key, value in processed.items()
            if isinstance(value, (int, float))
        }
    for key in ("sessions", "workspaces", "credentials"):
        value = payload.get(key)
        if isinstance(value, dict):
            state[key] = {
                str(name): entry for name, entry in value.items()
                if isinstance(entry, dict)
                and (key != "sessions" or _valid_session(entry))
            }
    return state


def _save_state(state: dict[str, Any]) -> None:
    """Replace the state file whole: write, flush, fsync, rename, fsync.

    An interrupted write must not leave half a JSON document behind, so
    nothing is ever written into the real path directly.

    The second fsync is of the parent directory, and it is what makes the
    rename durable. Flushing the temp file's contents alone leaves the
    rename in the directory's own unwritten cache, so a power loss
    restores the previous state and hands every session it named to the
    model a second time — one model call per session, for nothing.
    """
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix="ingest-state-", suffix=".json.tmp", dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(
                {**state, "version": STATE_VERSION},
                indent=2, ensure_ascii=False,
            ))
            handle.flush()
            os.fsync(handle.fileno())
        # Records which conversations were fed to the model; owner-only
        # like the rest of the profile.
        os.chmod(temporary, 0o600)
        replace_file(temporary, path)
        from openprogram.state.store.session.atomic_file import _fsync_directory

        _fsync_directory(path.parent)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _save_state_safely(state: dict[str, Any]) -> None:
    try:
        _save_state(state)
    except Exception as exc:  # noqa: BLE001 - a lost backoff costs one retry
        logger.debug("memory: watcher state not saved: %s", exc)


def _load_processed() -> dict[str, float]:
    """Conversations written as far as they went, by their ``updated_at``."""
    return load_state()["processed"]


def _save_processed(processed: dict[str, float]) -> None:
    state = load_state()
    state["processed"] = dict(processed)
    _save_state(state)


def load_retries() -> dict[str, dict[str, Any]]:
    """Conversations waiting to be offered again, and the ones stopped.

    Unreadable reads as empty: this is what a status shows, and a status
    must answer.
    """
    try:
        return load_state()["sessions"]
    except WatcherStateError:
        return {}


def _same_dir(a: Path | str, b: Path | str) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def retries_for(memory_dir: Path | str) -> dict[str, dict[str, Any]]:
    """The entries of conversations that write to ``memory_dir``."""
    from openprogram.state.paths import get_memory_dir

    personal = get_memory_dir()
    return {
        session_id: entry for session_id, entry in load_retries().items()
        if _same_dir(entry.get("workspace") or personal, memory_dir)
    }


def _target_root(session_id: str) -> Path:
    """The memory this session's background writing lands in."""
    try:
        from .project_layer import write_root

        target = write_root(session_id)
    except Exception:  # noqa: BLE001 - the personal memory is the default
        target = None
    return Path(target) if target is not None else store.root()


def _scope(code: str) -> str:
    if code in TRANSIENT_REASON_CODES:
        return "transient"
    if code in CREDENTIAL_REASON_CODES:
        return "credential"
    if code in WORKSPACE_REASON_CODES:
        return "workspace"
    return "session"


def _config_digest() -> str:
    """The user configuration, as a fingerprint: any change to it may be
    the fix for a stopped cause."""
    try:
        from openprogram.state.config import read_config

        text = json.dumps(read_config(), sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 - unreadable reads as one value
        text = ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _logins_digest() -> str:
    """The profile's stored sign-ins, by name, size and time — never by
    content: signing in again, or out, changes it."""
    from openprogram.state.paths import get_state_dir

    digest = hashlib.sha256()
    auth = get_state_dir() / "auth"
    try:
        for path in sorted(auth.rglob("*")):
            if path.is_file():
                stat = path.stat()
                digest.update(
                    f"{path.relative_to(auth).as_posix()}\0{stat.st_size}"
                    f"\0{stat.st_mtime_ns}\0".encode()
                )
    except OSError:
        pass
    return digest.hexdigest()[:16]


def _workspace_digest(memory_dir: Path | str) -> str:
    from .management.transaction import workspace_revision

    try:
        return workspace_revision(Path(memory_dir))
    except OSError:
        return ""


def _expire_stops(state: dict[str, Any]) -> bool:
    """Drop the stops whose cause may be gone. True when any went."""
    config = _config_digest()
    changed = False
    for key, stop in list(state["workspaces"].items()):
        if (
            stop.get("boot") != _BOOT
            or stop.get("config") != config
            or stop.get("revision") != _workspace_digest(key)
        ):
            del state["workspaces"][key]
            changed = True
    if state["credentials"]:
        logins = _logins_digest()
        for key, stop in list(state["credentials"].items()):
            if stop.get("config") != config or stop.get("logins") != logins:
                del state["credentials"][key]
                changed = True
    return changed


def _stop(left: WriteFailure, now: float, **fingerprints: str) -> dict[str, Any]:
    return {
        "reason": str(left.reason or ""),
        "reason_code": str(getattr(left, "reason_code", "") or ""),
        "at": now,
        "config": _config_digest(),
        **fingerprints,
    }


def _retry_after(
    entry: dict[str, Any] | None,
    left: WriteFailure,
    updated_at: float,
    now: float,
    workspace: Path | str | None = None,
) -> dict[str, Any]:
    """The next retry for a session whose write failed.

    Everything counts per watermark, which only moving on resets.
    ``attempts`` is every try; ``failures`` the tries that failed in the
    conversation itself, which stop it at ``SESSION_MAX_ATTEMPTS``;
    ``backoff`` the transient failures in a row, which set the wait. A
    transient failure neither spends nor resets ``failures``.
    """
    reason_code = str(getattr(left, "reason_code", "") or "")
    transient = reason_code in TRANSIENT_REASON_CODES
    same = entry is not None and entry.get("updated_at") == updated_at
    previous = entry if same else {}
    attempts = int(previous.get("attempts", 0)) + 1
    failures = int(previous.get("failures", 0)) + (0 if transient else 1)
    backoff = int(previous.get("backoff", 0)) + 1 if transient else 0
    delay = (
        min(RETRY_BASE_DELAY * 2 ** min(backoff - 1, 32), RETRY_MAX_DELAY)
        if transient else SESSION_RETRY_SPACING
    )
    return {
        "updated_at": updated_at,
        "reason": str(left.reason or ""),
        "reason_code": reason_code,
        "workspace": str(workspace) if workspace is not None else None,
        "attempts": attempts,
        "failures": failures,
        "backoff": backoff,
        "first_at": previous.get("first_at", now),
        "last_at": now,
        "next_at": now + delay,
        "stopped": failures >= SESSION_MAX_ATTEMPTS,
    }


def _due(entry: dict[str, Any] | None, updated_at: float, now: float) -> bool:
    """Whether a session with this retry entry is offered on this poll.

    A stopped session waits for its conversation to move on; nothing
    another conversation does, a successful write included, counts.
    """
    if entry is None or entry.get("updated_at") != updated_at:
        return True
    if entry.get("stopped"):
        return False
    return now >= float(entry["next_at"])


def _all_sessions(db: Any) -> list[dict[str, Any]]:
    """Every session, paged, so the list size cannot silently drop old ones.

    A single ``limit=500`` call returns the 500 most recently updated
    sessions — which are exactly the ones least likely to be idle.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    offset = 0
    while True:
        page = db.list_sessions(limit=SESSION_PAGE, offset=offset,
                               include_archived=True)
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
        startup_pass()
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


# The record of Topic files a repair agent could not fix. There is no
# repair any more (a file another tool wrote is read as it is), so a record
# left by an earlier version is deleted where it lies.
_RETIRED_RECORDS = ("unrepaired-topics.json",)


def _memory_roots() -> list[Path]:
    """The personal memory and every project memory on this profile."""
    roots = [store.root()]
    try:
        from .project_layer import project_roots

        roots.extend(project_roots())
    except Exception:  # noqa: BLE001 - the personal memory still counts
        pass
    return roots


def startup_pass(roots: list[Path] | None = None) -> None:
    """What the Worker does once per memory when it starts: drop retired
    runtime records, and redraw the map's list of files written outside
    OpenProgram (they may have arrived while it was down). Never raises."""
    from .runtime import memory_guide
    from .workspace_layout import runtime_dir

    for root in roots if roots is not None else _memory_roots():
        try:
            root = Path(root)
            if not (root / "topics").is_dir():
                continue
            for name in _RETIRED_RECORDS:
                (runtime_dir(root) / name).unlink(missing_ok=True)
            memory_guide.refresh_outside_map(root)
        except Exception as exc:  # noqa: BLE001 - one memory never stops the rest
            logger.debug("memory: startup pass skipped %s: %s", root, exc)


def _scan(idle_minutes: int) -> int:
    """One pass. Returns number of sessions processed."""
    try:
        from openprogram.state.store.current import current_store
    except Exception:
        return 0
    db = current_store()
    with watcher_lock():
        return _scan_locked(db, idle_minutes)


def _scan_locked(db: Any, idle_minutes: int) -> int:
    now = time.time()
    cutoff = now - idle_minutes * 60
    state = load_state()
    processed = state["processed"]
    retries = state["sessions"]
    n_done = 0
    sessions = _all_sessions(db)
    dirty = _expire_stops(state)
    # A deleted conversation has nothing left to retry.
    listed = {str(s.get("id") or "") for s in sessions}
    for session_id in [k for k in retries if k not in listed]:
        del retries[session_id]
        dirty = True
    if dirty:
        _save_state_safely(state)
    for s in sessions:
        if state["credentials"]:
            # The writer cannot sign in: no conversation can be written
            # until that changes.
            break
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
        entry = retries.get(sid)
        if not _due(entry, updated_at, now):
            # Waiting out its backoff, or stopped until it changes.
            continue
        target = _target_root(sid)
        if str(target) in state["workspaces"]:
            # Its memory is stopped; so is every conversation writing there.
            continue
        try:
            messages = db.get_branch(sid)
        except Exception:
            continue
        if not messages:
            processed[sid] = updated_at
            _save_state(state)
            continue
        left = _process_session(sid, messages, target=target)
        code = str(getattr(left, "reason_code", "") or "") if left else ""
        scope = _scope(code) if left is not None else ""
        retry = None
        stopped_scope = None
        if left is None:
            retries.pop(sid, None)
        elif scope == "credential":
            state["credentials"]["writer"] = _stop(
                left, time.time(), logins=_logins_digest(),
            )
            stopped_scope = scope
        elif scope == "workspace":
            state["workspaces"][str(target)] = _stop(
                left, time.time(), boot=_BOOT, revision=_workspace_digest(target),
            )
            stopped_scope = scope
        elif left.retryable:
            retry = _retry_after(entry, left, updated_at, time.time(), target)
            retries[sid] = retry
        else:
            retries.pop(sid, None)
        # 事件层 tap：空闲会话写入记忆的起止（B 类）。懒 import 防循环。
        from openprogram.state.event_sink import emit_safe
        emit_safe("memory.ingest_ended", "system", {
            "ok": left is None,
            "retryable": retry is not None and not retry["stopped"],
            "reason": left.reason if left else "",
            **({
                "attempt": retry["attempts"],
                "next_attempt_at": None if retry["stopped"] else retry["next_at"],
            } if retry is not None else {}),
        }, {"session": sid})
        if stopped_scope is not None or (retry is not None and retry["stopped"]):
            # Another poll would pay for the same failure. The writer
            # status of that memory says it stopped.
            logger.warning(
                "memory: writing stopped for %s (%s: %s)",
                sid if stopped_scope is None else f"every conversation ({stopped_scope})",
                code, left.reason,
            )
            from .runtime.writer_status import record_failure

            record_failure(target, code or None, retryable=False)
            _save_state_safely(state)
            continue
        if retry is not None:
            # Nothing terminal happened, so nothing is recorded as
            # processed: a later poll offers this session again.
            logger.info(
                "memory: write incomplete for %s (%s); retry %d in %ds",
                sid, left.reason, retry["attempts"],
                int(retry["next_at"] - retry["last_at"]),
            )
            _save_state_safely(state)
            continue
        if left is None:
            n_done += 1
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
        _save_state(state)
    return n_done


def _process_session(
    session_id: str,
    messages: list[dict[str, Any]],
    *,
    target: Path | None = None,
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

    A failure is recorded in ``target``, the memory the session writes to,
    where a success is recorded too.
    """
    from openprogram.state.event_sink import emit_safe
    emit_safe("memory.ingest_started", "system",
              {"messages": len(messages)}, {"session": session_id})

    try:
        from . import get_backend
        backend = get_backend()
        from .policy import node_policy
        if any(node_policy(row) is not None for row in messages) and not getattr(backend, "supports_execution_policy", False):
            left = WriteFailure("Memory backend cannot enforce execution policy", reason_code=MemoryWriteFailureCode.MEMORY_PROVIDER_RESOLUTION_FAILED)
        else:
            left = backend.write(
                messages, session_id=session_id, force=True,
            )
    except Exception as exc:  # noqa: BLE001
        # Only an explicit transient verdict justifies another poll. An
        # unclassified exception may be a permanent config/auth failure.
        logger.info("memory: write deferred for %s (%s)", session_id, exc)
        verdict = getattr(exc, "retryable", None)
        left = WriteFailure(
            str(exc), retryable=False if verdict is None else bool(verdict),
            reason_code=MemoryWriteFailureCode.WRITER_FAILURE_UNKNOWN,
        )
    if left is not None:
        from .runtime.writer_status import record_failure

        try:
            root = target if target is not None else store.root()
        except Exception as exc:  # noqa: BLE001 - status cannot fail a scan
            logger.debug("memory writer status root was unavailable: %s", exc)
        else:
            record_failure(
                root, getattr(left, "reason_code", None),
                retryable=left.retryable,
            )
    return left
