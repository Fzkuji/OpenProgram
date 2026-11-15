"""A summary request that reuses the conversation's cached prefix.

Right after a turn's request, its prefix sits in the provider's prompt
cache. A compaction that sends that same request again, with the summary
prompt as one more user message, reads it at the cache price instead of
paying for the conversation a second time (OpenCode's compaction request,
Claude Code's forked summary agent, Codex's local compaction). Same system
prompt, same tools, same cache key; a tool call in the reply is never run,
it only counts as a reply without the summary.

The summary covers everything up to the tip; the kept tail still renders
raw after it, so a few thousand tokens appear twice and the summary request
hits the cache almost entirely.

A fork is used only when:

- the conversation's last request is known for this chain (a turn's own
  request, recorded by ``remember_request``; its message is on the chain
  being compacted);
- the summary is written with the model that request went to;
- that request, the prompt and the summary's output cap fit the window;
- the compaction is not recovering from an overflow (that request is what
  did not fit).

Otherwise the summary reads the conversation as text
(``Summarizer._llm_summary``).
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Iterable, Optional

#: Requests remembered per conversation (one per branch that ran lately).
_PER_SESSION = 4
#: Conversations remembered at once.
_SESSIONS = 64


@dataclass(frozen=True)
class ForkSource:
    """A turn's last request: what the summary request sends again."""

    anchor_id: str
    model_key: str
    context: Any
    session_id: Optional[str]
    recorded_at: float


_lock = threading.Lock()
_requests: "OrderedDict[str, OrderedDict[str, ForkSource]]" = OrderedDict()


def model_key(model: Any) -> str:
    return f"{getattr(model, 'provider', '')}/{getattr(model, 'id', '') or model}"


def remember_request(session_id: Optional[str], anchor_id: Optional[str], model: Any, context: Any) -> None:
    """Record the request a conversation turn (its message ``anchor_id``) just sent."""
    if not session_id or not anchor_id or context is None:
        return
    source = ForkSource(anchor_id, model_key(model), context, session_id, time.time())
    with _lock:
        per = _requests.pop(session_id, None) or OrderedDict()
        per.pop(anchor_id, None)
        per[anchor_id] = source
        while len(per) > _PER_SESSION:
            per.popitem(last=False)
        _requests[session_id] = per
        while len(_requests) > _SESSIONS:
            _requests.popitem(last=False)


def forget(session_id: Optional[str] = None) -> None:
    """Drop what is remembered (one conversation, or all)."""
    with _lock:
        if session_id is None:
            _requests.clear()
        else:
            _requests.pop(session_id, None)


def fork_source(session_id: str, chain_ids: Iterable[str], model: Any, *,
                prompt: str, output_tokens: int) -> Optional[ForkSource]:
    """The remembered request a summary of this chain may fork, or None."""
    from openprogram.runtime.context.engine import memory_readable
    if not memory_readable():
        # The forked request carries recalled memory; with memory off the
        # summary is built from a render that leaves it out.
        return None
    chain = set(chain_ids)
    with _lock:
        per = list((_requests.get(session_id) or {}).values())
    candidates = [s for s in per if s.anchor_id in chain and s.model_key == model_key(model)]
    if not candidates:
        return None
    source = max(candidates, key=lambda s: s.recorded_at)
    from openprogram.runtime.context.budget import BudgetAllocator
    from openprogram.runtime.context.tokens import _text_tokens, real_context_window
    window = real_context_window(model)
    used = BudgetAllocator().allocate(
        context_window=window,
        system_prompt=getattr(source.context, "system_prompt", None) or "",
        history=list(getattr(source.context, "messages", None) or []),
        tools=list(getattr(source.context, "tools", None) or []),
    ).input_used
    if used + _text_tokens(prompt) + output_tokens > window - max(256, window // 20):
        return None
    return source


async def summarize_by_fork(source: ForkSource, model: Any, *, prompt: str, nudge: str,
                            max_tokens: int, cancel_event: Optional[threading.Event]) -> str:
    """The summary written as one more turn of ``source``'s request.

    A reply without every section (a tool call counts as one) is asked once
    more with ``nudge``; still without them, it fails. A reply that reaches
    its cap fails (``SummaryTruncated``).
    """
    import asyncio

    from openprogram.models.providers.types import SimpleStreamOptions, TextContent, ToolCall, UserMessage
    from openprogram.models.usage import usage_scope
    from openprogram.runtime.context.compaction.bounded_summary import SummaryTruncated, summary_reply
    from openprogram.runtime.context.compaction.prompts import missing_sections
    from openprogram.runtime.context.compaction.summarize import SummaryIncomplete

    output = min(max_tokens, model.max_tokens)
    asks = [prompt, prompt + "\n\n" + nudge]
    text = ""
    for ask in asks:
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError()
        context = source.context.model_copy(update={"messages": [
            *source.context.messages,
            UserMessage(content=[TextContent(text=ask)], timestamp=int(time.time() * 1000)),
        ]})
        options = SimpleStreamOptions(max_tokens=output, signal=cancel_event,
                                      session_id=source.session_id)
        with usage_scope(call_kind="summarize"):
            response = await summary_reply(model, context, options)
        if response.stop_reason == "aborted" or (cancel_event is not None and cancel_event.is_set()):
            raise asyncio.CancelledError()
        if response.stop_reason == "length":
            raise SummaryTruncated("Summary reached its output cap before it ended")
        if response.stop_reason == "error":
            raise RuntimeError(response.error_message or "summary request failed")
        called = any(isinstance(block, ToolCall) for block in response.content)
        text = "\n".join(b.text for b in response.content if isinstance(b, TextContent)).strip()
        if text and not called and not missing_sections(text):
            return text
    raise SummaryIncomplete("Summary is missing sections after a reminder")


__all__ = ["ForkSource", "remember_request", "forget", "fork_source", "summarize_by_fork", "model_key"]
