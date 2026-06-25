"""StreamingRegistry + StreamingMsg.

Skeleton implementation — wire it into the actual write paths in
follow-up phases (see ``docs/design/runtime/streaming-resume.md``).

The registry is process-global, accessed via :func:`get_registry`.
Each running message gets a :class:`StreamingMsg` handle that knows
how to:

  * persist incremental updates (throttled, with a final hard-flush
    on terminal transitions);
  * broadcast ``msg_update`` events to WS subscribers;
  * abort / finalize cleanly when the producer exits.
"""
from __future__ import annotations

import contextlib
import enum
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional


# Persistence debounce: at most one save per msg per N seconds during
# active updates. A terminal transition (done / error / aborted)
# always forces a hard flush regardless of the timer.
SAVE_DEBOUNCE_SEC = 0.25

# Worker-startup sweep threshold: any running msg whose last update
# is older than this is considered orphaned and gets marked aborted.
STALE_RUNNING_AFTER_SEC = 300.0  # 5 min


class StreamStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    ABORTED = "aborted"

    @property
    def is_terminal(self) -> bool:
        return self in (
            StreamStatus.DONE,
            StreamStatus.ERROR,
            StreamStatus.ABORTED,
        )


@dataclass
class StreamingMsg:
    """Per-message handle for a long-running producer.

    Lifecycle::

        open_stream(...) -> StreamingMsg(status=PENDING)
        msg.start() -> status=RUNNING, persisted
        msg.update(content=..., tree=...) -> debounced persist + broadcast
        msg.finalize(status=DONE, content=...) -> hard persist + broadcast
        msg.abort(reason="...") -> hard persist + broadcast (status=ABORTED)

    Thread-safe — multiple update calls from different threads are
    serialized via an internal lock.
    """
    session_id: str
    msg_id: str
    kind: str                           # "llm_reply" | "tool_call" | "agentic_function" | ...
    function: Optional[str] = None      # function name for tool / agentic
    status: StreamStatus = StreamStatus.PENDING
    content: str = ""
    tree: Optional[dict] = None
    started_at: float = field(default_factory=time.time)
    last_update_at: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_saved_at: float = 0.0
    _save_timer: Optional[threading.Timer] = field(default=None, repr=False)

    # lifecycle

    def start(self) -> None:
        """Transition PENDING → RUNNING + persist initial placeholder."""
        with self._lock:
            if self.status != StreamStatus.PENDING:
                return
            self.status = StreamStatus.RUNNING
            self.started_at = time.time()
            self.last_update_at = self.started_at
        self._persist(force=True)
        self._broadcast()

    def update(
        self,
        content: Optional[str] = None,
        tree: Optional[dict] = None,
        append: bool = False,
    ) -> None:
        """Patch content / tree. Debounced persist.

        ``append=True`` concatenates to existing content (use for
        token-by-token streaming). ``append=False`` replaces.
        """
        with self._lock:
            if self.status.is_terminal:
                return
            if content is not None:
                self.content = (self.content + content) if append else content
            if tree is not None:
                self.tree = tree
            self.last_update_at = time.time()
        self._schedule_persist()
        self._broadcast()

    def finalize(
        self,
        status: StreamStatus = StreamStatus.DONE,
        content: Optional[str] = None,
        tree: Optional[dict] = None,
    ) -> None:
        """Terminal transition. Hard flush + broadcast + deregister."""
        if not status.is_terminal:
            raise ValueError(f"finalize requires terminal status, got {status}")
        with self._lock:
            if self.status.is_terminal:
                return
            if content is not None:
                self.content = content
            if tree is not None:
                self.tree = tree
            self.status = status
            self.last_update_at = time.time()
            self._cancel_timer()
        self._persist(force=True)
        self._broadcast()
        get_registry()._unregister(self)

    def abort(self, reason: str = "") -> None:
        """Convenience: finalize as ABORTED."""
        self.tree = self.tree or {}
        if isinstance(self.tree, dict):
            self.tree.setdefault("_aborted_reason", reason)
        self.finalize(status=StreamStatus.ABORTED)

    # persistence

    def _schedule_persist(self) -> None:
        """Debounced save — at most one persist per ``SAVE_DEBOUNCE_SEC``."""
        with self._lock:
            elapsed = time.time() - self._last_saved_at
            if elapsed >= SAVE_DEBOUNCE_SEC:
                self._cancel_timer()
                threading.Thread(target=self._persist, daemon=True).start()
                return
            if self._save_timer is None:
                delay = SAVE_DEBOUNCE_SEC - elapsed
                self._save_timer = threading.Timer(delay, self._persist)
                self._save_timer.daemon = True
                self._save_timer.start()

    def _cancel_timer(self) -> None:
        if self._save_timer is not None:
            try:
                self._save_timer.cancel()
            except Exception:
                pass
            self._save_timer = None

    def _persist(self, force: bool = False) -> None:
        """Write current snapshot to SessionDB.

        Wired in Phase 2 — currently a no-op stub that just notes the
        intent. The real implementation reads / writes the msg node's
        ``metadata.status`` / ``content`` / ``metadata.context_tree``
        via the SessionStore.
        """
        with self._lock:
            self._last_saved_at = time.time()
            self._cancel_timer()
        # TODO Phase 2: read session_store, update node metadata,
        # commit_turn().

    def _broadcast(self) -> None:
        """Push msg_update event to WS subscribers.

        Wired in Phase 5 — currently a no-op stub. The real
        implementation looks up subscribers via the ws server's
        ``_msg_subscribers`` map and sends a JSON envelope.
        """
        # TODO Phase 5: ws_server._broadcast_msg_update(
        #     session_id=self.session_id,
        #     msg_id=self.msg_id,
        #     payload=self.snapshot(),
        # )

    # read helpers

    def snapshot(self) -> dict:
        """Plain-dict view suitable for WS / persistence."""
        return {
            "msg_id": self.msg_id,
            "session_id": self.session_id,
            "kind": self.kind,
            "function": self.function,
            "status": self.status.value,
            "content": self.content,
            "tree": self.tree,
            "started_at": self.started_at,
            "last_update_at": self.last_update_at,
        }


class StreamingRegistry:
    """Process-global registry of active streaming messages.

    Singleton — access via :func:`get_registry`. Threading: every
    public method is safe to call from any thread.
    """

    def __init__(self) -> None:
        self._streams: dict[tuple[str, str], StreamingMsg] = {}
        self._lock = threading.Lock()

    def open(
        self,
        *,
        session_id: str,
        msg_id: str,
        kind: str,
        function: Optional[str] = None,
    ) -> StreamingMsg:
        """Create + register a new StreamingMsg."""
        msg = StreamingMsg(
            session_id=session_id,
            msg_id=msg_id,
            kind=kind,
            function=function,
        )
        with self._lock:
            self._streams[(session_id, msg_id)] = msg
        msg.start()
        return msg

    def get(self, session_id: str, msg_id: str) -> Optional[StreamingMsg]:
        with self._lock:
            return self._streams.get((session_id, msg_id))

    def active(self) -> list[StreamingMsg]:
        with self._lock:
            return [m for m in self._streams.values() if not m.status.is_terminal]

    def _unregister(self, msg: StreamingMsg) -> None:
        with self._lock:
            self._streams.pop((msg.session_id, msg.msg_id), None)


_registry_singleton: Optional[StreamingRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> StreamingRegistry:
    """Process-global StreamingRegistry."""
    global _registry_singleton
    if _registry_singleton is None:
        with _registry_lock:
            if _registry_singleton is None:
                _registry_singleton = StreamingRegistry()
    return _registry_singleton


@contextlib.contextmanager
def open_stream(
    session_id: str,
    msg_id: str,
    *,
    kind: str,
    function: Optional[str] = None,
) -> Iterator[StreamingMsg]:
    """Context manager wrapping :meth:`StreamingRegistry.open`.

    On clean exit, finalizes as DONE. On exception, finalizes as ERROR
    (the producer can still call ``finalize`` / ``abort`` explicitly
    to override).
    """
    msg = get_registry().open(
        session_id=session_id,
        msg_id=msg_id,
        kind=kind,
        function=function,
    )
    try:
        yield msg
        if not msg.status.is_terminal:
            msg.finalize(status=StreamStatus.DONE)
    except BaseException:
        if not msg.status.is_terminal:
            msg.finalize(status=StreamStatus.ERROR)
        raise


def sweep_stale_running(
    *,
    older_than_sec: float = STALE_RUNNING_AFTER_SEC,
) -> int:
    """Worker startup hook — scan all sessions for ``status=running``
    msgs whose ``last_update_at`` predates ``older_than_sec``, and
    mark them ``aborted``.

    Returns the number of msgs swept.

    Called from worker startup so a crashed previous run doesn't
    leave the chat showing a frozen ``running`` spinner forever.
    """
    try:
        from openprogram.agent.session_db import default_db
        from openprogram.store import GraphStoreShim
    except Exception:
        return 0

    cutoff = time.time() - older_than_sec
    db = default_db()
    swept = 0
    try:
        sessions = db.list_sessions(limit=10**9)
    except Exception:
        return 0

    for sess in sessions:
        sid = sess.get("id") or sess.get("session_id")
        if not sid:
            continue
        try:
            msgs = db.get_messages(sid)
        except Exception:
            continue
        shim = None
        for m in msgs or []:
            if (m.get("status") or "done") != "running":
                continue
            last = m.get("last_update_at") or m.get("timestamp") or 0
            if last >= cutoff:
                continue
            if shim is None:
                shim = GraphStoreShim(db, sid)
            try:
                shim.update(
                    m["id"],
                    metadata={
                        "status": "aborted",
                        "last_update_at": time.time(),
                        "_aborted_reason": "worker_restart_stale_sweep",
                    },
                )
                swept += 1
            except Exception:
                pass
    return swept
