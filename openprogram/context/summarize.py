"""Whole-turn context summarization with token budgeting and safe failure.

The caller supplies rendered DAG turn groups. Only a complete prefix is
summarized; an unavailable summarizer leaves the original history intact.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from openprogram.context.prompts import (
    FRESH_PROMPT,
    SYSTEM_PROMPT,
    UPDATE_PROMPT,
)
from openprogram.context.tokens import (
    estimate_history_tokens,
    estimate_message_tokens,
)


# Defaults synthesised from Claude Code (min/max range + text-block gate),
# Hermes (ratio-of-window + protect_last_n), and OpenClaw (small-window cap).
# See openprogram/context/README.md §4 for the rationale per number.
DEFAULT_KEEP_RECENT_TOKENS = 20_000     # legacy override; new path uses range
DEFAULT_KEEP_MIN_TOKENS = 8_000         # tail floor: enough for ~5-6 turns
DEFAULT_KEEP_MAX_TOKENS = 40_000        # tail ceiling: more is wasteful
DEFAULT_KEEP_RATIO = 0.10               # tail = window × ratio, clamped
DEFAULT_KEEP_MIN_MESSAGES = 4           # minimum recent messages with text
DEFAULT_PROTECT_FIRST_N = 0             # minimum prefix size; all covered input is summarized
DEFAULT_PROTECT_LAST_N = 4             # keep the latest two complete user turns
DEFAULT_MIN_PROMPT_BUDGET = 8_000       # head must have ≥ this many tokens free
DEFAULT_MIN_PROMPT_RATIO = 0.25         # OR ≥ 25% of window — whichever smaller


@dataclass
class Summary:
    summary_text: str
    cut_idx: int
    summarised_count: int
    summarised_tokens: int
    previous_summary_used: bool
    duration_ms: int
    fell_back_to_structural: bool = False
    error: Optional[str] = None


class Summarizer:
    """Top-level summarisation entry point used by ContextEngine.compact."""

    def __init__(self,
                 *,
                 keep_recent_tokens: int = DEFAULT_KEEP_RECENT_TOKENS,
                 keep_min_tokens: int = DEFAULT_KEEP_MIN_TOKENS,
                 keep_max_tokens: int = DEFAULT_KEEP_MAX_TOKENS,
                 keep_ratio: float = DEFAULT_KEEP_RATIO,
                 keep_min_messages: int = DEFAULT_KEEP_MIN_MESSAGES,
                 protect_first_n: int = DEFAULT_PROTECT_FIRST_N,
                 protect_last_n: int = DEFAULT_PROTECT_LAST_N,
                 min_prompt_budget: int = DEFAULT_MIN_PROMPT_BUDGET,
                 min_prompt_ratio: float = DEFAULT_MIN_PROMPT_RATIO,
                 max_summary_tokens: int = 4000):
        # Legacy single-budget knob — still honoured when caller passes
        # ``keep_recent_tokens=N`` to summarise() for back-compat with
        # ``trigger_compaction(keep_recent_tokens=…)``.
        self.keep_recent_tokens = keep_recent_tokens
        self.keep_min_tokens = keep_min_tokens
        self.keep_max_tokens = keep_max_tokens
        self.keep_ratio = keep_ratio
        self.keep_min_messages = keep_min_messages
        self.protect_first_n = protect_first_n
        self.protect_last_n = protect_last_n
        self.min_prompt_budget = min_prompt_budget
        self.min_prompt_ratio = min_prompt_ratio
        self.max_summary_tokens = max_summary_tokens

    # ---- Public API ----------------------------------------------------

    async def summarise(self,
                        *,
                        messages: list[dict],
                        model: Any,
                        previous_summary: str | None = None,
                        cancel_event: threading.Event | None = None,
                        keep_recent_tokens: int | None = None,
                        context_window: int | None = None,
                        ) -> Summary:
        started = time.time()
        # context_window lets find_cut_index scale the kept tail to the
        # model's actual window. Without it we fall back to the legacy
        # fixed budget. Caller (engine.compact) resolves real_context_window
        # from the model and passes it through.
        if context_window is None:
            try:
                from openprogram.context.tokens import real_context_window
                context_window = real_context_window(model)
            except Exception:
                context_window = 0
        cut = self.find_cut_index(
            messages,
            keep_recent_tokens=keep_recent_tokens,
            context_window=context_window,
        )
        if cut <= self.protect_first_n:
            return Summary(
                summary_text="",
                cut_idx=0,
                summarised_count=0,
                summarised_tokens=0,
                previous_summary_used=False,
                duration_ms=int((time.time() - started) * 1000),
            )

        prefix = messages[:cut]
        prefix_tokens = sum(_message_tokens(m) for m in prefix)

        try:
            text = await self._llm_summary(
                prefix=prefix,
                model=model,
                previous_summary=previous_summary,
                cancel_event=cancel_event,
            )
            fell_back = False
            err: Optional[str] = None
        except Exception as e:  # noqa: BLE001
            text = ""
            fell_back = True
            err = f"{type(e).__name__}: {e}"

        return Summary(
            summary_text=text,
            cut_idx=cut,
            summarised_count=cut,
            summarised_tokens=prefix_tokens,
            previous_summary_used=bool(previous_summary),
            duration_ms=int((time.time() - started) * 1000),
            fell_back_to_structural=fell_back,
            error=err,
        )

    # ---- Cut-point picker (overridable) -------------------------------

    def find_cut_index(self, messages: list[dict],
                       *,
                       keep_recent_tokens: int | None = None,
                       context_window: int = 0,
                       ) -> int:
        """Select a prefix using rendered tokens and complete user turns.

        The bounded token target controls the retained suffix, with a minimum
        recent-message count. If that suffix alone exceeds the target, retain
        it intact. A history already under the target needs no summary.
        """
        n = len(messages)
        if n < 4:
            return 0

        # 1. Desired tail size
        if keep_recent_tokens is not None:
            effective_keep = max(0, int(keep_recent_tokens))
        else:
            if context_window > 0:
                desired = int(context_window * self.keep_ratio)
            else:
                desired = self.keep_recent_tokens
            effective_keep = max(self.keep_min_tokens,
                                  min(self.keep_max_tokens, desired))

        # 2. Small-window cap — the head must always retain at least
        #    min_prompt tokens of room for system prompt + new turn.
        if context_window > 0:
            min_prompt = min(self.min_prompt_budget,
                             int(context_window * self.min_prompt_ratio))
            max_safe_keep = max(0, context_window - min_prompt)
            effective_keep = min(effective_keep, max_safe_keep)

        # Select only complete user-turn boundaries. The latest two user
        # turns are the default minimum; a giant recent turn may exceed the
        # target, but must never be split from its calls/results.
        tokens = [_message_tokens(m) for m in messages]
        if sum(tokens) <= effective_keep:
            return 0
        user_indices = [i for i, msg in enumerate(messages)
                        if msg.get("role") == "user"]
        if len(user_indices) < 3:
            return 0
        latest_allowed = user_indices[-2]
        boundaries = [i for i in range(1, latest_allowed + 1)
                      if messages[i].get("role") == "user"
                      and i >= self.protect_first_n
                      and n - i >= self.protect_last_n
                      and sum(_has_text_block(m) for m in messages[i:])
                      >= self.keep_min_messages]
        if not boundaries:
            return 0
        for cut in boundaries:
            if sum(tokens[cut:]) <= effective_keep:
                return cut
        return boundaries[-1]

    # ---- LLM call ------------------------------------------------------

    async def _llm_summary(self,
                           *,
                           prefix: list[dict],
                           model: Any,
                           previous_summary: str | None,
                           cancel_event: threading.Event | None,
                           ) -> str:
        from openprogram.providers import complete_simple
        from openprogram.providers.types import (
            Context, SimpleStreamOptions, TextContent, UserMessage,
        )

        conv = self._serialize(prefix)
        prompt = f"<conversation>\n{conv}\n</conversation>\n\n"
        if previous_summary:
            prompt += (
                f"<previous-summary>\n{previous_summary}\n"
                f"</previous-summary>\n\n{UPDATE_PROMPT}"
            )
        else:
            prompt += FRESH_PROMPT

        opts_kwargs: dict[str, Any] = {"max_tokens": self.max_summary_tokens}
        if getattr(model, "reasoning", False):
            opts_kwargs["reasoning"] = "high"
        # Cancellation: the provider layer reads ``signal`` if supplied.
        if cancel_event is not None:
            opts_kwargs["signal"] = cancel_event
        opts = SimpleStreamOptions(**opts_kwargs)

        ctx = Context(
            system_prompt=SYSTEM_PROMPT,
            messages=[UserMessage(
                role="user",
                content=[TextContent(type="text", text=prompt)],
                timestamp=0,
            )],
        )
        from openprogram.usage import usage_scope
        with usage_scope(call_kind="summarize"):
            response = await complete_simple(model, ctx, opts)
        if getattr(response, "stop_reason", None) == "error":
            raise RuntimeError(
                f"Summariser provider error: "
                f"{getattr(response, 'error_message', 'unknown')}"
            )
        return " ".join(
            b.text for b in response.content
            if isinstance(b, TextContent)
        )

    # ---- Helpers -------------------------------------------------------

    @staticmethod
    def _serialize(messages: list[dict]) -> str:
        out: list[str] = []
        for m in messages:
            role = (m.get("role") or "user").capitalize()
            text = (m.get("content") or "").strip()
            if text:
                out.append(f"{role}: {text}")
        return "\n\n".join(out)


def _has_text_block(msg: dict) -> bool:
    """A 'text-block message' is one whose content carries some real
    text — i.e. a user message, a textual assistant reply, or any
    message with non-empty content. Pure tool-result wrappers with an
    empty content string don't count. Used by find_cut_index to keep
    enough conversational turns in the tail even when one giant
    tool_result block would otherwise satisfy the token budget alone.
    """
    role = msg.get("role")
    if role in ("user", "assistant", "system"):
        content = msg.get("content")
        if content and str(content).strip():
            return True
    return False


default_summarizer = Summarizer()


def _message_tokens(msg: dict) -> int:
    """DAG groups are priced from provider messages, including tool results."""
    if "_context_tokens" in msg:
        return int(msg["_context_tokens"])
    return estimate_message_tokens(msg)
