"""CallStreamState — single owner of per-llm-call stream merge + revision."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .protocol import (
    CHECKPOINT_DIRTY_BYTES,
    CHECKPOINT_INTERVAL_S,
    DELTA_MERGE_BYTES,
    DELTA_MERGE_MS,
    MAX_INLINE_PREVIEW_BYTES,
    SCHEMA_VERSION,
    TERMINAL_PHASES,
    build_chat_response_envelope,
    is_terminal_phase,
)
import logging

_log = logging.getLogger(__name__)

EmitFn = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class StreamIdentity:
    session_id: str
    execution_id: str
    node_id: str
    generation: int = 1
    display_msg_id: str = ""
    ephemeral: bool = False


@dataclass
class _BlockState:
    block_id: str
    message_id: str
    attempt_id: str
    block_index: int
    kind: str
    visibility: str = "visible"  # visible | opaque
    retention: str = "persist"  # persist | memory_only
    content: str = ""
    status: str = "running"  # running | finished
    finish_reason: str = ""
    # tool_ref / parallel_group metadata (empty for text/reasoning)
    tool_call_id: str = ""
    occurrence_id: str = ""
    ref_node_id: str = ""
    tool_name: str = ""
    group_id: str = ""


@dataclass
class _AttemptState:
    attempt_id: str
    attempt_index: int
    reason: str = "initial"
    provider: str = ""
    model: str = ""
    parent_attempt_id: str = ""
    phase: str = "running"
    status: str = "running"
    validation: str = "pending"  # pending | passed | failed
    message_id: str = ""
    usage: Optional[dict[str, Any]] = None
    blocks: dict[str, _BlockState] = field(default_factory=dict)
    block_order: list[str] = field(default_factory=list)


class CallStreamState:
    """Owner of attempts/blocks/revision for one logical LLM Call node.

    Emits ``execution_stream`` frames via ``emit``. Call.output is NOT
    written here — only ``metadata.stream`` preview / checkpoint dicts.
    """

    def __init__(
        self,
        identity: StreamIdentity,
        *,
        emit: Optional[EmitFn] = None,
        provider: str = "",
        model: str = "",
    ) -> None:
        self.identity = identity
        self._emit_fn = emit
        self._lock = threading.RLock()
        self.revision = 0
        self.checkpoint_revision = 0
        self.phase = "running"
        self.provider = provider
        self.model = model
        self.attempts: dict[str, _AttemptState] = {}
        self.attempt_order: list[str] = []
        self.current_attempt_id: Optional[str] = None
        self.selected_attempt_id: Optional[str] = None
        self.result: Any = None
        self.recovered_from_checkpoint = False
        self.source_checkpoint: Optional[dict[str, int]] = None
        self._closed = False
        self._accepting = True
        self._pending_delta: Optional[dict[str, Any]] = None
        self._pending_delta_started_at = 0.0
        self._dirty_bytes = 0
        self._last_checkpoint_at = 0.0
        self._protocol_errors: list[str] = []

    # ------------------------------------------------------------------ emit

    def set_emit(self, emit: Optional[EmitFn]) -> None:
        self._emit_fn = emit

    def _base_fields(self) -> dict[str, Any]:
        ident = self.identity
        return {
            "type": "execution_stream",
            "schema_version": SCHEMA_VERSION,
            "session_id": ident.session_id,
            "execution_id": ident.execution_id,
            "node_id": ident.node_id,
            "generation": ident.generation,
            "display_msg_id": ident.display_msg_id or "",
        }

    def _emit(self, payload: dict[str, Any]) -> None:
        fn = self._emit_fn
        if fn is None:
            return
        try:
            fn(build_chat_response_envelope(payload))
        except Exception:
            # Caller-supplied emitter: a failing consumer must not break the stream.
            _log.warning("stream frame not emitted for node %s", self.identity.node_id, exc_info=True)

    def _advance(self) -> tuple[int, int]:
        base = self.revision
        self.revision = base + 1
        return base, self.revision

    def _flush_pending_delta(self) -> None:
        pending = self._pending_delta
        if not pending:
            return
        self._pending_delta = None
        self._emit(pending)

    def _queue_or_emit_delta(self, payload: dict[str, Any], delta: str) -> None:
        """Merge consecutive same-block deltas within time/byte budgets."""
        now = time.monotonic()
        pending = self._pending_delta
        if (
            pending
            and pending.get("block_id") == payload.get("block_id")
            and pending.get("attempt_id") == payload.get("attempt_id")
            and pending.get("op") == "block_delta"
        ):
            elapsed_ms = (now - self._pending_delta_started_at) * 1000
            merged_text = str(pending.get("delta") or "") + delta
            if (
                elapsed_ms < DELTA_MERGE_MS
                and len(merged_text.encode("utf-8")) < DELTA_MERGE_BYTES
            ):
                pending["delta"] = merged_text
                pending["revision"] = payload["revision"]
                # Keep earliest base_revision.
                return
            self._flush_pending_delta()
        self._pending_delta = payload
        self._pending_delta_started_at = now

    # ---------------------------------------------------------------- attempts

    def start_attempt(
        self,
        *,
        reason: str = "initial",
        provider: str = "",
        model: str = "",
        parent_attempt_id: str = "",
        phase: str = "running",
        attempt_id: Optional[str] = None,
    ) -> str:
        with self._lock:
            if self._closed or not self._accepting:
                return ""
            self._flush_pending_delta()
            # Supersede previous running attempt in the same sequence.
            if self.current_attempt_id:
                prev = self.attempts.get(self.current_attempt_id)
                if prev and prev.status == "running":
                    prev.status = "superseded"
                    prev.phase = "superseded"
                    base, rev = self._advance()
                    self._emit(
                        {
                            **self._base_fields(),
                            "op": "attempt_finished",
                            "base_revision": base,
                            "revision": rev,
                            "attempt_id": prev.attempt_id,
                            "status": "superseded",
                            "validation": prev.validation,
                        }
                    )
            aid = attempt_id or f"attempt_{uuid.uuid4().hex[:12]}"
            index = len(self.attempt_order)
            message_id = f"message_{uuid.uuid4().hex[:12]}"
            attempt = _AttemptState(
                attempt_id=aid,
                attempt_index=index,
                reason=reason,
                provider=provider or self.provider,
                model=model or self.model,
                parent_attempt_id=parent_attempt_id,
                phase=phase,
                message_id=message_id,
            )
            self.attempts[aid] = attempt
            self.attempt_order.append(aid)
            self.current_attempt_id = aid
            self.phase = "running" if reason == "initial" else "retry_wait"
            if reason != "initial":
                self.phase = "running"
            base, rev = self._advance()
            self._emit(
                {
                    **self._base_fields(),
                    "op": "attempt_started",
                    "base_revision": base,
                    "revision": rev,
                    "attempt_id": aid,
                    "attempt_index": index,
                    "reason": reason,
                    "provider": attempt.provider,
                    "model": attempt.model,
                    "parent_attempt_id": parent_attempt_id or None,
                    "phase": phase,
                    "message_id": message_id,
                }
            )
            return aid

    def ensure_attempt(self, *, reason: str = "initial") -> str:
        with self._lock:
            if self.current_attempt_id and self.current_attempt_id in self.attempts:
                cur = self.attempts[self.current_attempt_id]
                if cur.status == "running":
                    return self.current_attempt_id
        return self.start_attempt(reason=reason)

    # ------------------------------------------------------------------ blocks

    def start_block(
        self,
        *,
        kind: str,
        visibility: str = "visible",
        retention: str = "persist",
        block_id: Optional[str] = None,
        attempt_id: Optional[str] = None,
    ) -> Optional[str]:
        with self._lock:
            if self._closed or not self._accepting:
                return None
            self._flush_pending_delta()
            aid = attempt_id or self.current_attempt_id
            if not aid or aid not in self.attempts:
                aid = self.start_attempt()
            attempt = self.attempts[aid]
            # Reuse open block of same kind when still running (legacy
            # text/thinking projection often streams without boundaries).
            for bid in reversed(attempt.block_order):
                blk = attempt.blocks[bid]
                if blk.kind == kind and blk.status == "running":
                    return blk.block_id
            bid = block_id or f"block_{uuid.uuid4().hex[:12]}"
            index = len(attempt.block_order)
            blk = _BlockState(
                block_id=bid,
                message_id=attempt.message_id,
                attempt_id=aid,
                block_index=index,
                kind=kind,
                visibility=visibility,
                retention=retention,
            )
            attempt.blocks[bid] = blk
            attempt.block_order.append(bid)
            base, rev = self._advance()
            self._emit(
                {
                    **self._base_fields(),
                    "op": "block_started",
                    "base_revision": base,
                    "revision": rev,
                    "attempt_id": aid,
                    "message_id": attempt.message_id,
                    "block_id": bid,
                    "block_index": index,
                    "kind": kind,
                    "visibility": visibility,
                    "retention": retention,
                }
            )
            return bid


    def add_tool_ref(
        self,
        *,
        tool_call_id: str,
        occurrence_id: str = "",
        tool_name: str = "",
        ref_node_id: str = "",
        group_id: str = "",
        status: str = "running",
    ) -> Optional[str]:
        """Insert a tool_ref block that points at a real DAG tool node.

        Does not embed tool args/results — those live on the referenced node.
        Reuses an existing block with the same qualified occurrence identity
        (or raw tool_call_id for legacy events).
        A parallel group is valid only when the caller supplies its explicit
        ``group_id``. The stream cannot infer parallelism from adjacency:
        sequential tool calls may also be adjacent, and assigning a group to
        an already-emitted block would require an out-of-order update.
        """
        with self._lock:
            if self._closed or not self._accepting:
                return None
            self._flush_pending_delta()
            aid = self.current_attempt_id
            if not aid or aid not in self.attempts:
                aid = self.start_attempt()
            attempt = self.attempts[aid]
            # Idempotent: same tool_call_id → update ref only
            for bid in attempt.block_order:
                blk = attempt.blocks[bid]
                if blk.kind == "tool_ref" and (
                    blk.occurrence_id == (occurrence_id or tool_call_id)
                    or (
                        not occurrence_id
                        and not blk.occurrence_id
                        and blk.tool_call_id == tool_call_id
                    )
                ):
                    changed = False
                    if ref_node_id and ref_node_id != blk.ref_node_id:
                        blk.ref_node_id = ref_node_id
                        changed = True
                    if tool_name and tool_name != blk.tool_name:
                        blk.tool_name = tool_name
                        changed = True
                    if group_id and group_id != blk.group_id:
                        blk.group_id = group_id
                        changed = True
                    if status and status != blk.status and not (
                        blk.status == "finished" and status == "running"
                    ):
                        blk.status = status
                        changed = True
                    if not changed:
                        return blk.block_id
                    base, rev = self._advance()
                    self._emit(
                        {
                            **self._base_fields(),
                            "op": "block_finished" if status == "finished" else "block_started",
                            "base_revision": base,
                            "revision": rev,
                            "attempt_id": aid,
                            "message_id": attempt.message_id,
                            "block_id": blk.block_id,
                            "block_index": blk.block_index,
                            "kind": "tool_ref",
                            "tool_call_id": tool_call_id,
                            "occurrence_id": blk.occurrence_id or None,
                            "ref_node_id": blk.ref_node_id,
                            "tool_name": blk.tool_name,
                            "group_id": blk.group_id or None,
                            "status": blk.status,
                        }
                    )
                    return blk.block_id
            # A tool call is a hard content boundary. Close an open text or
            # thinking block before inserting the tool_ref so continuation
            # text gets a new block after the tool row instead of appending to
            # the earlier Markdown block.
            for bid in reversed(attempt.block_order):
                previous = attempt.blocks[bid]
                if previous.kind not in {"text", "reasoning_summary", "refusal"}:
                    continue
                if previous.status == "running":
                    self.finish_block(
                        block_id=previous.block_id,
                        attempt_id=aid,
                        finish_reason="tool_call",
                    )
                break
            # Parallel membership comes from the provider/agent batch. Never
            # infer it from neighboring blocks: a sequential tool call can be
            # adjacent to another tool call.
            gid = group_id or ""
            bid = f"block_{uuid.uuid4().hex[:12]}"
            index = len(attempt.block_order)
            blk = _BlockState(
                block_id=bid,
                message_id=attempt.message_id,
                attempt_id=aid,
                block_index=index,
                kind="tool_ref",
                content="",
                status=status,
                tool_call_id=tool_call_id or "",
                occurrence_id=occurrence_id or tool_call_id or "",
                ref_node_id=ref_node_id or "",
                tool_name=tool_name or "",
                group_id=gid or "",
            )
            attempt.blocks[bid] = blk
            attempt.block_order.append(bid)
            base, rev = self._advance()
            self._emit(
                {
                    **self._base_fields(),
                    "op": "block_started",
                    "base_revision": base,
                    "revision": rev,
                    "attempt_id": aid,
                    "message_id": attempt.message_id,
                    "block_id": bid,
                    "block_index": index,
                    "kind": "tool_ref",
                    "tool_call_id": blk.tool_call_id,
                    "occurrence_id": blk.occurrence_id or None,
                    "ref_node_id": blk.ref_node_id,
                    "tool_name": blk.tool_name,
                    "group_id": blk.group_id or None,
                    "status": blk.status,
                    "visibility": "visible",
                    "retention": "persist",
                }
            )
            return bid

    def finish_tool_ref(
        self,
        tool_call_id: str,
        *,
        occurrence_id: str = "",
        ref_node_id: str = "",
    ) -> None:
        with self._lock:
            if self._closed:
                return
            aid = self.current_attempt_id
            attempt = self.attempts.get(aid) if aid else None
            if attempt is None:
                return
            for bid in attempt.block_order:
                blk = attempt.blocks[bid]
                if blk.kind == "tool_ref" and (
                    blk.occurrence_id == (occurrence_id or tool_call_id)
                    or (
                        not occurrence_id
                        and not blk.occurrence_id
                        and blk.tool_call_id == tool_call_id
                    )
                ):
                    changed = blk.status != "finished"
                    if ref_node_id and ref_node_id != blk.ref_node_id:
                        blk.ref_node_id = ref_node_id
                        changed = True
                    if not changed:
                        return
                    blk.status = "finished"
                    base, rev = self._advance()
                    self._emit(
                        {
                            **self._base_fields(),
                            "op": "block_finished",
                            "base_revision": base,
                            "revision": rev,
                            "attempt_id": aid,
                            "message_id": attempt.message_id,
                            "block_id": blk.block_id,
                            "block_index": blk.block_index,
                            "kind": "tool_ref",
                            "tool_call_id": tool_call_id,
                            "occurrence_id": blk.occurrence_id or None,
                            "ref_node_id": blk.ref_node_id,
                            "tool_name": blk.tool_name,
                            "group_id": blk.group_id or None,
                            "status": "finished",
                            "finish_reason": "tool_result",
                        }
                    )
                    return

    def append_delta(
        self,
        *,
        kind: str,
        delta: str,
        attempt_id: Optional[str] = None,
        block_id: Optional[str] = None,
    ) -> None:
        if not delta:
            return
        with self._lock:
            if self._closed or not self._accepting:
                return
            if is_terminal_phase(self.phase):
                return
            aid = attempt_id or self.current_attempt_id
            attempt = self.attempts.get(aid) if aid else None
            if attempt is None or attempt.status != "running":
                # Re-enter so start_attempt can take the lock (RLock).
                aid = self.start_attempt(reason="continue")
                attempt = self.attempts.get(aid)
                if attempt is None:
                    return
            bid = block_id
            if bid is None or bid not in attempt.blocks:
                bid = self.start_block(kind=kind, attempt_id=aid)
            if not bid:
                return
            blk = attempt.blocks[bid]
            if blk.status != "running":
                self._protocol_errors.append("delta_after_block_finished")
                return
            if blk.visibility == "opaque":
                # Never render or accumulate opaque signatures as text.
                return
            blk.content += delta
            self._dirty_bytes += len(delta.encode("utf-8"))
            base, rev = self._advance()
            payload = {
                **self._base_fields(),
                "op": "block_delta",
                "base_revision": base,
                "revision": rev,
                "attempt_id": aid,
                "message_id": blk.message_id,
                "block_id": bid,
                "kind": blk.kind,
                "delta": delta,
            }
            self._queue_or_emit_delta(payload, delta)
            self._maybe_checkpoint_unlocked()

    def finish_block(
        self,
        *,
        block_id: Optional[str] = None,
        kind: Optional[str] = None,
        finish_reason: str = "stop",
        attempt_id: Optional[str] = None,
    ) -> None:
        with self._lock:
            if self._closed:
                return
            self._flush_pending_delta()
            aid = attempt_id or self.current_attempt_id
            if not aid or aid not in self.attempts:
                return
            attempt = self.attempts[aid]
            bid = block_id
            if bid is None and kind:
                for candidate in reversed(attempt.block_order):
                    if attempt.blocks[candidate].kind == kind:
                        bid = candidate
                        break
            if not bid or bid not in attempt.blocks:
                return
            blk = attempt.blocks[bid]
            if blk.status != "running":
                return
            blk.status = "finished"
            blk.finish_reason = finish_reason
            base, rev = self._advance()
            self._emit(
                {
                    **self._base_fields(),
                    "op": "block_finished",
                    "base_revision": base,
                    "revision": rev,
                    "attempt_id": aid,
                    "message_id": blk.message_id,
                    "block_id": bid,
                    "finish_reason": finish_reason,
                }
            )

    def finish_attempt(
        self,
        *,
        status: str = "completed",
        validation: str = "pending",
        usage: Optional[dict[str, Any]] = None,
        attempt_id: Optional[str] = None,
        error_summary: str = "",
    ) -> None:
        with self._lock:
            if self._closed:
                return
            self._flush_pending_delta()
            aid = attempt_id or self.current_attempt_id
            if not aid or aid not in self.attempts:
                return
            attempt = self.attempts[aid]
            # Close open blocks.
            for bid in list(attempt.block_order):
                blk = attempt.blocks[bid]
                if blk.status == "running":
                    blk.status = "finished"
                    blk.finish_reason = "attempt_end"
                    base, rev = self._advance()
                    self._emit(
                        {
                            **self._base_fields(),
                            "op": "block_finished",
                            "base_revision": base,
                            "revision": rev,
                            "attempt_id": aid,
                            "message_id": blk.message_id,
                            "block_id": bid,
                            "finish_reason": "attempt_end",
                        }
                    )
            attempt.status = status
            attempt.validation = validation
            if usage is not None:
                attempt.usage = usage
            base, rev = self._advance()
            payload = {
                **self._base_fields(),
                "op": "attempt_finished",
                "base_revision": base,
                "revision": rev,
                "attempt_id": aid,
                "status": status,
                "validation": validation,
            }
            if usage is not None:
                payload["usage"] = usage
            if error_summary:
                payload["error_summary"] = error_summary
            self._emit(payload)

    # --------------------------------------------------------------- lifecycle

    def stop_accepting(self) -> None:
        """Cancel boundary: refuse further provider content."""
        with self._lock:
            self._accepting = False
            self._flush_pending_delta()
            if self.phase not in TERMINAL_PHASES:
                self.phase = "cancelling"

    def finish_node(
        self,
        *,
        status: str = "completed",
        result: Any = None,
        reason_code: str = "",
        validation: str = "passed",
    ) -> dict[str, Any]:
        """Terminal decision. Returns stream metadata for Call.metadata.stream."""
        with self._lock:
            self._flush_pending_delta()
            self._accepting = False
            if self.current_attempt_id:
                cur = self.attempts.get(self.current_attempt_id)
                if cur and cur.status == "running":
                    # Finish without emitting again if caller already did.
                    self.finish_attempt(
                        status=(
                            "cancelled"
                            if status == "cancelled"
                            else "failed"
                            if status == "failed"
                            else "completed"
                        ),
                        validation=validation,
                        attempt_id=self.current_attempt_id,
                    )
            if status == "completed":
                self.selected_attempt_id = self.current_attempt_id
            self.result = result
            self.phase = (
                "cancelled"
                if status == "cancelled"
                else "failed"
                if status == "failed"
                else "completed"
            )
            self.checkpoint_revision = self.revision
            snap = self._snapshot_dict_unlocked(durability="durable")
            terminal_revision = self.revision
            # node_finished carries full snapshot semantics (no base_revision).
            self._emit(
                {
                    **self._base_fields(),
                    "op": "node_finished",
                    "revision": terminal_revision,
                    "terminal_revision": terminal_revision,
                    "checkpoint_revision": self.checkpoint_revision,
                    "reason_code": reason_code or status,
                    "phase": self.phase,
                    "status": status,
                    "snapshot": snap,
                }
            )
            self._closed = True
            return self.stream_metadata_unlocked()

    def emit_snapshot(self, *, durability: str = "live") -> dict[str, Any]:
        with self._lock:
            self._flush_pending_delta()
            snap = self._snapshot_dict_unlocked(durability=durability)
            self._emit(
                {
                    **self._base_fields(),
                    "op": "snapshot",
                    "revision": self.revision,
                    "checkpoint_revision": self.checkpoint_revision,
                    "durability": durability,
                    "phase": self.phase,
                    "snapshot": snap,
                }
            )
            return snap

    def request_sync(self, reason: str = "gap") -> None:
        with self._lock:
            self._emit(
                {
                    **self._base_fields(),
                    "op": "sync_required",
                    "reason": reason,
                    "generation": self.identity.generation,
                    "revision": self.revision,
                }
            )

    # -------------------------------------------------------------- checkpoint

    def _maybe_checkpoint_unlocked(self) -> None:
        if self.identity.ephemeral:
            return
        now = time.monotonic()
        if (
            self._dirty_bytes < CHECKPOINT_DIRTY_BYTES
            and (now - self._last_checkpoint_at) < CHECKPOINT_INTERVAL_S
        ):
            return
        self._write_checkpoint_unlocked()

    def _write_checkpoint_unlocked(self) -> None:
        self.checkpoint_revision = self.revision
        self._dirty_bytes = 0
        self._last_checkpoint_at = time.monotonic()
        try:
            from openprogram.state.store import _store

            store = _store.get()
            if store is None:
                return
            meta = {"stream": self.stream_metadata_unlocked()}
            # Conditional-ish: only while node is non-terminal in DAG.
            store.update(self.identity.node_id, metadata=meta)
        except Exception:
            # The in-memory stream stays authoritative; a later checkpoint retries.
            _log.warning("stream checkpoint not saved for node %s", self.identity.node_id, exc_info=True)

    def flush_checkpoint(self) -> None:
        with self._lock:
            self._flush_pending_delta()
            if not self.identity.ephemeral:
                self._write_checkpoint_unlocked()

    # ----------------------------------------------------------------- views

    def text_preview(self, *, kind: str = "text", limit: int = 4000) -> str:
        with self._lock:
            return self._text_preview_unlocked(kind=kind, limit=limit)

    def _text_preview_unlocked(
        self,
        *,
        kind: str = "text",
        limit: int = 4000,
        include_memory_only: bool = True,
    ) -> str:
        aid = self.current_attempt_id or (
            self.attempt_order[-1] if self.attempt_order else None
        )
        if not aid:
            return ""
        attempt = self.attempts.get(aid)
        if not attempt:
            return ""
        parts: list[str] = []
        for bid in attempt.block_order:
            blk = attempt.blocks[bid]
            if (
                blk.kind == kind
                and blk.visibility == "visible"
                and (include_memory_only or blk.retention != "memory_only")
            ):
                parts.append(blk.content)
        text = "".join(parts)
        if len(text) > limit:
            return text[-limit:]
        return text

    def stream_metadata(self) -> dict[str, Any]:
        with self._lock:
            return self.stream_metadata_unlocked()

    def stream_metadata_unlocked(self) -> dict[str, Any]:
        """Preview / checkpoint payload for ``Call.metadata.stream``."""
        attempts_summary = []
        for aid in self.attempt_order:
            a = self.attempts[aid]
            attempts_summary.append(
                {
                    "attempt_id": a.attempt_id,
                    "attempt_index": a.attempt_index,
                    "reason": a.reason,
                    "status": a.status,
                    "validation": a.validation,
                    "provider": a.provider,
                    "model": a.model,
                }
            )
        preview = self._text_preview_unlocked(
            kind="text",
            limit=MAX_INLINE_PREVIEW_BYTES,
            include_memory_only=False,
        )
        reasoning = self._text_preview_unlocked(
            kind="reasoning_summary",
            limit=min(4000, MAX_INLINE_PREVIEW_BYTES),
            include_memory_only=False,
        )
        # Checkpoints must retain the complete ordered content needed by a
        # reconnect after the live owner is gone. Keep the summary fields for
        # legacy readers, and expose the durable snapshot for current readers.
        snapshot = self._snapshot_dict_unlocked(durability="durable")
        return {
            "schema_version": SCHEMA_VERSION,
            "generation": self.identity.generation,
            "revision": self.revision,
            "checkpoint_revision": self.checkpoint_revision,
            "phase": self.phase,
            "partial": self.phase not in TERMINAL_PHASES,
            "current_attempt_id": self.current_attempt_id,
            "selected_attempt_id": self.selected_attempt_id,
            "attempts": attempts_summary,
            "snapshot": snapshot,
            "preview_text": preview,
            "preview_reasoning": reasoning,
            "ephemeral": self.identity.ephemeral,
            "recovered_from_checkpoint": self.recovered_from_checkpoint,
            "protocol_errors": list(self._protocol_errors[-8:]),
        }

    def _snapshot_dict_unlocked(self, *, durability: str) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        for aid in self.attempt_order:
            a = self.attempts[aid]
            blocks = []
            for bid in a.block_order:
                b = a.blocks[bid]
                content = b.content
                if b.retention == "memory_only" and durability == "durable":
                    content = ""
                    omitted = True
                else:
                    omitted = False
                # Every snapshot is a reconnect source. Keep the complete
                # visible block for live refresh as well as durable recovery;
                # only the legacy preview fields remain bounded. Otherwise a
                # live snapshot would silently discard the beginning of a
                # Markdown reply when it replaces client state.
                truncated = False
                entry = {
                        "block_id": b.block_id,
                        "message_id": b.message_id,
                        "block_index": b.block_index,
                        "kind": b.kind,
                        "visibility": b.visibility,
                        "retention": b.retention,
                        "status": b.status,
                        "finish_reason": b.finish_reason,
                        "content": content if b.visibility == "visible" else "",
                        "omitted_by_policy": omitted
                        or (b.visibility == "opaque"),
                        "truncated": truncated,
                    }
                if b.kind == "tool_ref":
                    entry.update({
                        "tool_call_id": b.tool_call_id,
                        "occurrence_id": b.occurrence_id or None,
                        "ref_node_id": b.ref_node_id,
                        "tool_name": b.tool_name,
                        "group_id": b.group_id or None,
                    })
                blocks.append(entry)
            attempts.append(
                {
                    "attempt_id": a.attempt_id,
                    "attempt_index": a.attempt_index,
                    "reason": a.reason,
                    "status": a.status,
                    "validation": a.validation,
                    "provider": a.provider,
                    "model": a.model,
                    "parent_attempt_id": a.parent_attempt_id or None,
                    "message_id": a.message_id,
                    "usage": a.usage,
                    "blocks": blocks,
                }
            )
        return {
            "revision": self.revision,
            "checkpoint_revision": self.checkpoint_revision,
            "generation": self.identity.generation,
            "phase": self.phase,
            "durability": durability,
            "current_attempt_id": self.current_attempt_id,
            "selected_attempt_id": self.selected_attempt_id,
            "result": self.result if durability == "durable" else None,
            "attempts": attempts,
            "preview_text": self._text_preview_unlocked(
                include_memory_only=durability != "durable",
            ),
            "preview_reasoning": self._text_preview_unlocked(
                kind="reasoning_summary",
                limit=4000,
                include_memory_only=durability != "durable",
            ),
            "recovered_from_checkpoint": self.recovered_from_checkpoint,
            "source_checkpoint": self.source_checkpoint,
        }
