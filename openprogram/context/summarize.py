"""Summarizer — LLM-driven prefix-summary pipeline with recovery.

Three improvements over the old ``compact_context``:

1. **Cancellable**: takes a ``cancel_event`` so a user ctrl-C aborts
   the LLM call cleanly instead of hanging.

2. **Recoverable**: when the summariser LLM throws / times out, the
   Summarizer falls back to a deterministic *structural* summary
   ("dropped N messages totalling M tokens") so the calling
   ``compact()`` can still cut history. Way better than leaving the
   agent stuck with full history during an outage.

3. **Cut-point picker is pluggable**: default picks at user-turn
   boundaries by walking from the newest end with a token budget
   (Hermes-style ``protect_last_n``). Subclasses can override
   ``find_cut_index`` for different strategies.
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
DEFAULT_KEEP_MIN_MESSAGES = 5           # ≥ N messages with text content in tail
DEFAULT_PROTECT_FIRST_N = 3             # initial task description always kept
DEFAULT_PROTECT_LAST_N = 20             # final N messages always kept
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

        prefix = messages[self.protect_first_n:cut]
        prefix_tokens = estimate_history_tokens(prefix)

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
            text = self._structural_summary(prefix)
            fell_back = True
            err = f"{type(e).__name__}: {e}"

        return Summary(
            summary_text=text,
            cut_idx=cut,
            summarised_count=cut - self.protect_first_n,
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
        """Pick the cut point — everything before goes into the summary,
        everything at or after is preserved verbatim as the kept tail.

        Algorithm (synthesised from Claude Code, Hermes, OpenClaw — see
        openprogram/context/README.md §4):

          1. Compute the *desired* tail size:
                desired = clamp(window × keep_ratio, keep_min_tokens, keep_max_tokens)
             ``keep_recent_tokens`` overrides this when the caller forces
             a specific budget (legacy /compact path).
          2. Apply small-window cap so the head still has room to breathe:
                min_prompt = min(min_prompt_budget, window × min_prompt_ratio)
                effective = min(desired, window - min_prompt)
          3. Walk from newest backward. Accept the cut when BOTH
             tail_tokens ≥ effective AND tail_text_block_messages ≥
             keep_min_messages. The double gate prevents a tail full of
             one giant tool_result with no real conversation.
          4. Apply protect_last_n: cut must not eat into the last N msgs.
          5. Apply protect_first_n: cut must not be inside the protected
             head.
          6. Snap forward to the next user-message boundary so the kept
             tail starts with a user turn (otherwise the model sees an
             orphan assistant reply).
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

        # 3. Walk backward accumulating tokens + text-block messages
        tail_tokens = 0
        tail_text_msgs = 0
        cut = n
        lower = self.protect_first_n
        for i in range(n - 1, lower - 1, -1):
            tail_tokens += estimate_message_tokens(messages[i])
            if _has_text_block(messages[i]):
                tail_text_msgs += 1
            if (tail_tokens >= effective_keep
                    and tail_text_msgs >= self.keep_min_messages):
                cut = i
                break
            cut = i

        # 4. protect_last_n — never fold the most recent N messages.
        last_n_floor = max(0, n - self.protect_last_n)
        cut = min(cut, last_n_floor)

        # 5. protect_first_n — cut sits at or after the protected head.
        cut = max(cut, self.protect_first_n)

        # 6. Snap forward to a user-message boundary.
        while cut < n and messages[cut].get("role") != "user":
            cut += 1
        if cut >= n:
            # Couldn't find a user boundary in the kept tail. Fall back
            # to the latest sensible cut so the call still makes progress.
            cut = max(self.protect_first_n + 1, n - 2)
        return cut

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

    @staticmethod
    def _structural_summary(prefix: list[dict]) -> str:
        """Deterministic fallback when the LLM call fails.

        Lists per-message role + first 60 chars so the model has SOME
        idea what got dropped, even if the LLM couldn't summarise it
        properly. Better than ``[N messages elided]`` which the prior
        impl produced.
        """
        n = len(prefix)
        tokens = estimate_history_tokens(prefix)
        lines = [
            f"[Context summary unavailable — LLM summariser failed. "
            f"The following {n} message(s) were dropped to free "
            f"≈{tokens} tokens. Each line shows role + opening of the "
            f"original content; the agent can ask the user for missing "
            f"details if a particular item turns out to be relevant.]",
        ]
        for m in prefix:
            role = (m.get("role") or "?").capitalize()
            head = (m.get("content") or "").strip().replace("\n", " ")
            if len(head) > 60:
                head = head[:57] + "…"
            lines.append(f"  · {role}: {head or '(empty)'}")
        return "\n".join(lines)


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
