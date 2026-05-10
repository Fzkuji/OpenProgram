"""
v2 message model + authoritative in-memory store.

Replaces the old "broadcast raw stream_event dicts + buffer last 200" pattern
with a single source of truth: for every assistant message we maintain one
``Message`` object in memory, every LLM delta mutates it, every subscriber
sees ``message.snapshot`` / ``message.delta`` / ``message.commit`` frames
off that single state.

Clients recovering from a disconnect send ``{"type": "sync", "known_seqs":
{msg_id: seq}}``. The server replies with a snapshot or a catch-up batch of
deltas so both sides converge on the same state. No raw-event replay.

This module is deliberately framework-agnostic — it does not import
FastAPI / WebSocket / pi-ai. Wiring into those layers happens in
``server.py`` and ``runtime.py``. That separation keeps ``MessageStore``
usable from tests and from alternate transports (SSE, gRPC) in the future.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Literal, Optional

# Schema version. Bump when the on-disk or wire format changes in a way that
# old clients can't read. v2 is the initial block-oriented design.
SCHEMA_VERSION = 2

# How many delta frames the store will replay on reconnect before falling
# back to a full snapshot. Tunable; the tradeoff is "many small frames over
# the wire" vs "one possibly-large JSON blob". 64 covers typical token
# streaming hiccups without blowing up reconnect payload size.
MAX_DELTA_CATCHUP = 64


# ---------------------------------------------------------------------------
# Block types — structured content, not a flat text field
# ---------------------------------------------------------------------------

BlockType = Literal[
    "text",          # rendered Markdown reply
    "thinking",      # reasoning / chain-of-thought
    "tool_use",      # LLM invoked a tool
    "tool_result",   # output from that tool (paired via tool_call_id)
    "image",         # inline image (uri or base64)
    "citation",      # reserved for future (web_search / retrieval)
    "error",         # inline block-level error (rare; whole-message errors
                     # use Message.status="error" instead)
]


@dataclass
class Block:
    """One content block inside a message.

    Every block has a stable ``id`` so deltas can target it by id without
    needing to know its position. Position ordering is preserved by the
    order blocks are added to ``Message.content``.

    The union-like shape (optional fields per type) is deliberate — a
    formal tagged-union would require either a discriminator library or a
    class hierarchy, both of which make JSON round-trips noisier than a
    few ignored-because-None fields.
    """

    type: BlockType
    id: str = field(default_factory=lambda: f"blk_{uuid.uuid4().hex[:12]}")

    # text / thinking
    text: str = ""
    # thinking-only metadata
    elapsed_ms: Optional[int] = None
    # tool_use / tool_result
    tool_call_id: str = ""
    tool_name: str = ""
    tool_arguments: dict = field(default_factory=dict)
    tool_result: str = ""
    tool_is_error: bool = False
    # image
    image_uri: str = ""
    image_mime: str = ""
    # citation (reserved — empty shape, filled when we wire web_search)
    citation_source: str = ""
    citation_title: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # Trim unused-per-type fields so wire payloads stay small. Callers
        # needing a full round-trip (persistence) can use asdict directly.
        if self.type in ("text", "thinking"):
            for k in ("tool_call_id", "tool_name", "tool_arguments",
                     "tool_result", "tool_is_error", "image_uri",
                     "image_mime", "citation_source", "citation_title"):
                d.pop(k, None)
            if self.type == "text":
                d.pop("elapsed_ms", None)
        elif self.type in ("tool_use", "tool_result"):
            for k in ("text", "elapsed_ms", "image_uri", "image_mime",
                     "citation_source", "citation_title"):
                d.pop(k, None)
        elif self.type == "image":
            for k in ("text", "elapsed_ms", "tool_call_id", "tool_name",
                     "tool_arguments", "tool_result", "tool_is_error",
                     "citation_source", "citation_title"):
                d.pop(k, None)
        elif self.type == "citation":
            for k in ("text", "elapsed_ms", "tool_call_id", "tool_name",
                     "tool_arguments", "tool_result", "tool_is_error",
                     "image_uri", "image_mime"):
                d.pop(k, None)
        elif self.type == "error":
            for k in ("elapsed_ms", "tool_call_id", "tool_name",
                     "tool_arguments", "tool_result", "tool_is_error",
                     "image_uri", "image_mime", "citation_source",
                     "citation_title"):
                d.pop(k, None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


MessageStatus = Literal[
    "pending",     # created client-side, not yet acknowledged
    "streaming",   # server actively generating deltas
    "complete",    # finished normally (stop / end_turn)
    "error",       # LLM or transport error — see .error
    "cancelled",   # user stopped mid-stream
    "interrupted", # connection dropped before commit; may be recoverable
]


@dataclass
class Message:
    """One message in a conversation.

    This is the *authoritative state*. Deltas mutate it; the wire frames
    (`message.snapshot`, `message.delta`, `message.commit`) describe those
    mutations to subscribers. JSON on disk is a straight serialization.
    """

    id: str
    session_id: str
    role: Literal["user", "assistant", "system"]
    status: MessageStatus = "pending"
    content: list[Block] = field(default_factory=list)
    seq: int = 0                        # monotonic per-message version
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    updated_at: int = field(default_factory=lambda: int(time.time() * 1000))
    # Optional metadata — present on assistant messages that finished normally.
    usage: Optional[dict] = None
    stop_reason: Optional[str] = None
    error: Optional[str] = None
    # Hooks for future features. None today.
    parent_message_id: Optional[str] = None       # branch / edit lineage
    regenerated_from: Optional[str] = None        # regenerate button
    function: Optional[str] = None                # program this msg ran (if any)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "status": self.status,
            "content": [b.to_dict() for b in self.content],
            "seq": self.seq,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "usage": self.usage,
            "stop_reason": self.stop_reason,
            "error": self.error,
            "parent_message_id": self.parent_message_id,
            "regenerated_from": self.regenerated_from,
            "function": self.function,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        blocks = [Block.from_dict(b) for b in (d.get("content") or [])]
        return cls(
            id=d["id"],
            session_id=d["session_id"],
            role=d["role"],
            status=d.get("status", "complete"),
            content=blocks,
            seq=d.get("seq", 0),
            created_at=d.get("created_at", 0),
            updated_at=d.get("updated_at", 0),
            usage=d.get("usage"),
            stop_reason=d.get("stop_reason"),
            error=d.get("error"),
            parent_message_id=d.get("parent_message_id"),
            regenerated_from=d.get("regenerated_from"),
            function=d.get("function"),
        )

    def find_block(self, block_id: str) -> Optional[Block]:
        for b in self.content:
            if b.id == block_id:
                return b
        return None


# ---------------------------------------------------------------------------
# Delta operations
# ---------------------------------------------------------------------------
#
# Delta ops are intentionally few. Every mutation the store supports maps
# to exactly one op. Clients apply them in seq order; any unknown op is a
# bug on the producer side — log + snapshot-recover rather than silent skip.

DeltaOp = Literal[
    "add_block",      # {"op": "add_block", "block": {...}}
    "append_text",    # {"op": "append_text", "block_id": ..., "text": "..."}
    "update_block",   # {"op": "update_block", "block_id": ..., "fields": {...}}
    "set_status",     # {"op": "set_status", "status": "streaming", ...}
]


# ---------------------------------------------------------------------------
# Store — authoritative, in-memory, with per-conversation persistence
# ---------------------------------------------------------------------------

_Listener = Callable[[str, dict], None]
# Signature: (session_id, frame_dict) → None. Frame is already shaped for the
# wire. Listeners are typically per-websocket "send this to my client".


class MessageStore:
    """Per-process authoritative state for all conversations' messages.

    Thread-safe. Every mutation bumps ``message.seq`` and broadcasts a
    frame to that conversation's listeners.

    Persistence is opt-in via ``persist_dir``. When set, ``commit()`` writes
    the message as one line into ``{session_id}/messages.jsonl`` (append-only,
    one record per message terminal state). Re-commits overwrite by id when
    the store is reloaded from disk.

    Deltas are buffered per-message in a short ring (``MAX_DELTA_CATCHUP``).
    On ``sync``, the store either replays from the ring (small gap) or
    sends a snapshot (large gap / ring evicted).
    """

    def __init__(self, persist_dir: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self._messages: dict[str, Message] = {}
        # message_id → deque-like list of (seq, delta) pairs
        self._delta_ring: dict[str, list[tuple[int, dict]]] = {}
        # session_id → list of listeners
        self._listeners: dict[str, list[_Listener]] = {}
        # Wildcard listeners — see subscribe_all. Used by transports that
        # want one fan-out point for every conversation (e.g. a single WS
        # broadcaster). These always get invoked on top of per-conv
        # listeners.
        self._global_listeners: list[_Listener] = []
        self._persist_dir = persist_dir

    # -- registration --------------------------------------------------------

    def subscribe(self, session_id: str, listener: _Listener) -> Callable[[], None]:
        with self._lock:
            self._listeners.setdefault(session_id, []).append(listener)

        def _unsub():
            with self._lock:
                lst = self._listeners.get(session_id)
                if lst and listener in lst:
                    lst.remove(listener)

        return _unsub

    def subscribe_all(self, listener: _Listener) -> Callable[[], None]:
        """Register a listener that fires for frames in *every* conversation.

        Typical use: a single WS broadcaster process-wide. Listeners are
        invoked in addition to (not instead of) any per-conv subscribers.
        """
        with self._lock:
            self._global_listeners.append(listener)

        def _unsub():
            with self._lock:
                if listener in self._global_listeners:
                    self._global_listeners.remove(listener)

        return _unsub

    # -- reads ---------------------------------------------------------------

    def get(self, message_id: str) -> Optional[Message]:
        with self._lock:
            return self._messages.get(message_id)

    def list_for_conv(self, session_id: str) -> list[Message]:
        with self._lock:
            return [m for m in self._messages.values() if m.session_id == session_id]

    # -- writes --------------------------------------------------------------

    def create(
        self,
        session_id: str,
        role: str,
        *,
        message_id: Optional[str] = None,
        status: MessageStatus = "pending",
        content: Optional[list[Block]] = None,
        function: Optional[str] = None,
    ) -> Message:
        mid = message_id or f"msg_{uuid.uuid4().hex[:12]}"
        msg = Message(
            id=mid,
            session_id=session_id,
            role=role,
            status=status,
            content=list(content or []),
            function=function,
        )
        with self._lock:
            self._messages[mid] = msg
            self._delta_ring[mid] = []
            # New messages go out as snapshots — there's no prior state for
            # delta apply to stitch onto.
            self._emit(session_id, {"type": "message.snapshot", "message": msg.to_dict()})
        return msg

    def add_block(self, message_id: str, block: Block) -> None:
        with self._lock:
            msg = self._messages[message_id]
            msg.content.append(block)
            self._bump(msg)
            self._record_delta(msg, {"op": "add_block", "block": block.to_dict()})

    def append_text(self, message_id: str, block_id: str, text: str) -> None:
        if not text:
            return
        with self._lock:
            msg = self._messages[message_id]
            blk = msg.find_block(block_id)
            if blk is None:
                raise KeyError(f"Block {block_id} not in message {message_id}")
            blk.text += text
            self._bump(msg)
            self._record_delta(msg, {
                "op": "append_text",
                "block_id": block_id,
                "text": text,
            })

    def update_block(self, message_id: str, block_id: str, **fields: Any) -> None:
        with self._lock:
            msg = self._messages[message_id]
            blk = msg.find_block(block_id)
            if blk is None:
                raise KeyError(f"Block {block_id} not in message {message_id}")
            for k, v in fields.items():
                if hasattr(blk, k):
                    setattr(blk, k, v)
            self._bump(msg)
            self._record_delta(msg, {
                "op": "update_block",
                "block_id": block_id,
                "fields": fields,
            })

    def set_status(
        self,
        message_id: str,
        status: MessageStatus,
        *,
        usage: Optional[dict] = None,
        stop_reason: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            msg = self._messages[message_id]
            msg.status = status
            if usage is not None:
                msg.usage = usage
            if stop_reason is not None:
                msg.stop_reason = stop_reason
            if error is not None:
                msg.error = error
            self._bump(msg)
            delta = {"op": "set_status", "status": status}
            if usage is not None:
                delta["usage"] = usage
            if stop_reason is not None:
                delta["stop_reason"] = stop_reason
            if error is not None:
                delta["error"] = error
            self._record_delta(msg, delta)

    def commit(
        self,
        message_id: str,
        *,
        status: MessageStatus = "complete",
        usage: Optional[dict] = None,
        stop_reason: Optional[str] = None,
    ) -> None:
        """Mark message terminal + persist.

        Internally this is a ``set_status`` delta (so reconnect catch-up
        includes it) plus a dedicated ``message.commit`` frame emitted on
        top. Clients that only care about "finished now" subscribe to
        ``commit``; clients that only care about monotonic state changes
        watch the ``delta`` stream. Both paths carry the same usage/
        stop_reason payload.
        """
        with self._lock:
            msg = self._messages[message_id]
            msg.status = status
            if usage is not None:
                msg.usage = usage
            if stop_reason is not None:
                msg.stop_reason = stop_reason
            self._bump(msg)
            # Record as a delta so sync/catch-up can replay it.
            delta = {"op": "set_status", "status": status}
            if usage is not None:
                delta["usage"] = usage
            if stop_reason is not None:
                delta["stop_reason"] = stop_reason
            ring = self._delta_ring.setdefault(msg.id, [])
            ring.append((msg.seq, delta))
            if len(ring) > MAX_DELTA_CATCHUP:
                del ring[: len(ring) - MAX_DELTA_CATCHUP]
            # Fan out the dedicated commit frame in addition to the delta,
            # so clients have one call site for "message is done now".
            self._emit(msg.session_id, {
                "type": "message.delta",
                "message_id": msg.id,
                "seq": msg.seq,
                "patch": delta,
            })
            self._emit(msg.session_id, {
                "type": "message.commit",
                "message_id": message_id,
                "seq": msg.seq,
                "status": msg.status,
                "usage": msg.usage,
                "stop_reason": msg.stop_reason,
            })
            self._persist(msg)

    # -- sync / reconnect ----------------------------------------------------

    def sync(self, session_id: str, known_seqs: dict[str, int]) -> list[dict]:
        """Compute the frames a reconnecting client needs to catch up.

        Client sends ``{msg_id: seq}``; server returns a list of frames.
        Rules:

          * message absent from known_seqs → full snapshot
          * known_seq == current seq → nothing
          * gap small enough to replay from ring → delta frames
          * gap too big / ring evicted → snapshot
          * unknown message ids (client knows about a message we don't) →
            ignored; client eventually prunes via its own GC
        """
        with self._lock:
            frames: list[dict] = []
            for msg in self.list_for_conv(session_id):
                client_seq = known_seqs.get(msg.id, -1)
                if client_seq >= msg.seq:
                    continue
                ring = self._delta_ring.get(msg.id, [])
                # Can we replay from the ring? Only if every seq > client_seq
                # is still in the ring.
                missing = [(s, d) for (s, d) in ring if s > client_seq]
                if missing and missing[0][0] == client_seq + 1:
                    for s, d in missing:
                        frames.append({
                            "type": "message.delta",
                            "message_id": msg.id,
                            "seq": s,
                            "patch": d,
                        })
                else:
                    frames.append({"type": "message.snapshot", "message": msg.to_dict()})
            return frames

    # -- persistence ---------------------------------------------------------

    def load_conv(self, session_id: str) -> None:
        """Rehydrate all messages for ``session_id`` from JSONL. Idempotent."""
        if self._persist_dir is None:
            return
        path = self._persist_dir / session_id / "messages.jsonl"
        if not path.exists():
            return
        with self._lock, path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("v") != SCHEMA_VERSION:
                    # v1 records (the old `messages.json` format) aren't
                    # handled here — the HTTP history endpoint migrates them
                    # on first read and rewrites as v2. See persistence.py.
                    continue
                msg = Message.from_dict(rec["message"])
                self._messages[msg.id] = msg
                self._delta_ring.setdefault(msg.id, [])

    def load_all(self) -> list[str]:
        """Scan the persist dir and rehydrate every conversation found.

        Returns the list of ``session_id`` values successfully loaded. Safe
        to call multiple times (``load_conv`` is idempotent) — useful on
        server startup to get v2 JSONL messages back into memory without
        depending on users clicking each conversation.

        Skips entries that don't have a ``messages.jsonl`` file so the
        old ``persistence.py`` layout (``messages.json`` + ``trees/``)
        doesn't produce spurious conv ids here.
        """
        if self._persist_dir is None:
            return []
        if not self._persist_dir.is_dir():
            return []
        loaded: list[str] = []
        for entry in sorted(self._persist_dir.iterdir()):
            if not entry.is_dir():
                continue
            if not (entry / "messages.jsonl").is_file():
                continue
            try:
                self.load_conv(entry.name)
                loaded.append(entry.name)
            except (OSError, json.JSONDecodeError):
                # One bad file shouldn't abort the whole restore — log
                # via stdout (the webui logger isn't reachable here) and
                # move on. The remaining conversations still rehydrate.
                import sys
                print(
                    f"[MessageStore.load_all] failed to load {entry.name}, skipping",
                    file=sys.stderr,
                )
        return loaded

    def _persist(self, msg: Message) -> None:
        if self._persist_dir is None:
            return
        # Append-only JSONL. Each line is the message's state at commit
        # time, so crash recovery picks up the latest record per id.
        # Size stays bounded because we only persist on terminal states,
        # not per-delta.
        d = self._persist_dir / msg.session_id
        d.mkdir(parents=True, exist_ok=True)
        rec = {"v": SCHEMA_VERSION, "message": msg.to_dict()}
        with (d / "messages.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # -- internals -----------------------------------------------------------

    def _bump(self, msg: Message) -> None:
        msg.seq += 1
        msg.updated_at = int(time.time() * 1000)

    def _record_delta(self, msg: Message, patch: dict) -> None:
        ring = self._delta_ring.setdefault(msg.id, [])
        ring.append((msg.seq, patch))
        if len(ring) > MAX_DELTA_CATCHUP:
            # Drop oldest; reconnects past this window fall back to snapshot.
            # That's fine — the snapshot always exists, it's just a bigger
            # payload. This is the tradeoff the outer constant controls.
            del ring[: len(ring) - MAX_DELTA_CATCHUP]
        self._emit(msg.session_id, {
            "type": "message.delta",
            "message_id": msg.id,
            "seq": msg.seq,
            "patch": patch,
        })

    def _emit(self, session_id: str, frame: dict) -> None:
        for lst in (self._listeners.get(session_id) or []):
            try:
                lst(session_id, frame)
            except Exception:
                # A broken listener must not take out the whole broadcast.
                # Listener lifecycle is owned by the transport layer; it
                # will notice disconnects on the next real send.
                pass
        for lst in list(self._global_listeners):
            try:
                lst(session_id, frame)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Process-wide singleton. Conservative default: ~/.agentic/sessions.
# Tests override via ``set_store_for_testing``.
# ---------------------------------------------------------------------------

_DEFAULT_DIR = Path.home() / ".agentic" / "sessions"
_store: Optional[MessageStore] = None


def get_store() -> MessageStore:
    global _store
    if _store is None:
        _store = MessageStore(persist_dir=_DEFAULT_DIR)
    return _store


def set_store_for_testing(store: Optional[MessageStore]) -> None:
    global _store
    _store = store
