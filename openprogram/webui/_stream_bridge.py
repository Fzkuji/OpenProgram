"""
Bridge between runtime's ``on_stream(event: dict)`` callback and the v2
``MessageStore``.

The runtime emits opaque event dicts (``{"type": "text", "text": ...}``
etc). The store needs structured mutations (``append_text(msg_id, block_id,
...)``). This module owns the mapping from one to the other, keeping
server.py free of streaming bookkeeping.

One ``StreamBridge`` per in-flight message. Create it with a pre-created
assistant ``Message`` id, hand its ``on_stream`` to the runtime, and call
``commit`` / ``fail`` / ``cancel`` when the exec finishes.
"""
from __future__ import annotations

from typing import Optional

from .messages import Block, MessageStore


class StreamBridge:
    """Owns the lifecycle of one assistant message's streaming state.

    The bridge lazily creates ``text`` and ``thinking`` blocks the first
    time the runtime emits a delta of each kind — starting with zero blocks
    is the common case for pure tool-only responses, and emitting empty
    blocks upfront would add clutter to the rendered bubble.

    Tool call / result pairs are tracked by ``tool_call_id`` so an arbitrary
    number of interleaved tools can stream in parallel.
    """

    def __init__(self, store: MessageStore, message_id: str) -> None:
        self._store = store
        self._msg_id = message_id
        self._text_block_id: Optional[str] = None
        self._thinking_block_id: Optional[str] = None
        # tool_call_id → block_id (for the tool_use block; the result is
        # stored as .tool_result on the same block, not a separate block.
        # One block per tool call keeps the rendered order deterministic.)
        self._tool_blocks: dict[str, str] = {}

    # ----- forwarded from runtime ------------------------------------------

    def on_stream(self, event: dict) -> None:
        """Translate one runtime event into MessageStore mutations.

        Unknown event types are ignored silently — the goal is forward
        compatibility with future pi-ai event kinds we haven't wired yet.
        A missing translation should degrade to "that part doesn't render",
        not "the whole message breaks".
        """
        etype = event.get("type")
        if etype == "text":
            text = event.get("text") or ""
            if not text:
                return
            if self._text_block_id is None:
                blk = Block(type="text")
                self._store.add_block(self._msg_id, blk)
                self._text_block_id = blk.id
            self._store.append_text(self._msg_id, self._text_block_id, text)

        elif etype == "thinking":
            text = event.get("text") or ""
            if not text:
                return
            if self._thinking_block_id is None:
                blk = Block(type="thinking")
                self._store.add_block(self._msg_id, blk)
                self._thinking_block_id = blk.id
            self._store.append_text(self._msg_id, self._thinking_block_id, text)
            # Piggyback elapsed on every thinking delta. The store treats
            # this as an idempotent field overwrite; the client renders
            # the most recent value next to the fold label.
            elapsed = event.get("elapsed")
            if elapsed:
                try:
                    self._store.update_block(
                        self._msg_id,
                        self._thinking_block_id,
                        elapsed_ms=int(float(elapsed) * 1000),
                    )
                except KeyError:
                    pass

        elif etype == "tool_use":
            call_id = event.get("tool_call_id") or ""
            if not call_id or call_id in self._tool_blocks:
                return
            blk = Block(
                type="tool_use",
                tool_call_id=call_id,
                tool_name=event.get("tool") or "?",
                tool_arguments=_coerce_args(event.get("input")),
            )
            self._store.add_block(self._msg_id, blk)
            self._tool_blocks[call_id] = blk.id

        elif etype == "tool_result":
            call_id = event.get("tool_call_id") or ""
            blk_id = self._tool_blocks.get(call_id)
            if blk_id is None:
                # Result arrived before a known tool_use — synthesize a
                # minimal tool block so the UI has somewhere to hang it.
                blk = Block(
                    type="tool_use",
                    tool_call_id=call_id,
                    tool_name=event.get("tool") or "?",
                )
                self._store.add_block(self._msg_id, blk)
                blk_id = blk.id
                self._tool_blocks[call_id] = blk_id
            self._store.update_block(
                self._msg_id,
                blk_id,
                tool_result=event.get("result") or "",
                tool_is_error=bool(event.get("is_error")),
            )

    # ----- terminal --------------------------------------------------------

    def commit(self, *, usage: Optional[dict] = None, stop_reason: str = "stop") -> None:
        self._store.commit(
            self._msg_id, status="complete", usage=usage, stop_reason=stop_reason
        )

    def fail(self, error: str) -> None:
        self._store.set_status(self._msg_id, "error", error=error)
        self._store.commit(self._msg_id, status="error")

    def cancel(self) -> None:
        self._store.commit(self._msg_id, status="cancelled")


def _coerce_args(raw) -> dict:
    """Normalize ``input`` from a stream event into a dict.

    Providers serialize tool arguments in wildly different shapes: JSON
    strings, already-parsed dicts, bare strings for legacy tools. The
    store's ``tool_arguments`` field is typed ``dict`` so callers get one
    shape to render. Unrecognized payloads wrap into ``{"raw": ...}`` so
    nothing is silently dropped.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            import json as _json
            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {"raw": raw}
    return {}
