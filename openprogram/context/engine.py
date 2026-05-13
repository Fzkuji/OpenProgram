"""ContextEngine — lifecycle orchestrator.

Composes the single-responsibility components into one production
pipeline. Replaces the old monolithic engine.

Lifecycle (every method optional in the ABC, default-impl supplies all):

    on_session_start(session_id)
        Called when a session is first loaded or created. Engines that
        keep per-session state (UsageTracker) hydrate from DB here.

    ingest(session_id, message)
        Called when a message lands in the DB. Default-impl no-ops —
        SessionDB is the canonical store. Custom engines can use this
        to maintain incremental indexes (e.g. a vector store for
        retrieval-augmented context).

    prepare(agent, session, history, model)
        Called BEFORE every LLM exec. Returns TurnPrep with the
        ready-to-send messages, system prompt, and a budget breakdown.

    should_auto_compact(prep) -> bool
        Cheap check the dispatcher uses to decide whether to fire
        compact() before the LLM call.

    compact(agent, session_id, model, ...)
        Either auto (inline) or manual (/compact). Persists the
        summary as a DAG node.

    after_turn(session_id, usage)
        Called AFTER each LLM exec with the provider's real usage
        dict. UsageTracker swaps in the real numbers; the engine can
        emit a recommend event if budget is rising fast.

    on_session_end(session_id)
        Called when a session is closed (CLI exit, /reset, gateway
        ttl). Frees in-memory state.

Subclassing: override the method whose behaviour you want different.
The default impl is structured so each step calls one helper —
``_age``, ``_assemble_messages``, ``_build_system_prompt`` — that
subclasses commonly want to override on its own.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Optional

from openprogram.context.aging import TurnAger, default_ager
from openprogram.context.budget import BudgetAllocator, default_allocator
from openprogram.context.persistence import Persister, default_persister
from openprogram.context.references import ReferenceTracker, default_tracker as _ref_tracker
from openprogram.context.summarize import Summarizer, default_summarizer
from openprogram.context.tokens import real_context_window
from openprogram.context.types import (
    BudgetAllocation,
    CompactResult,
    TurnPrep,
    UsageSnapshot,
)
from openprogram.context.usage import UsageTracker, default_tracker as _usage_tracker


EventCallback = Callable[[dict], None]


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class ContextEngine:
    """The pluggable contract. Subclasses override what they need; the
    default impl satisfies every method."""

    name: str = "abstract"

    # ---- Session lifecycle --------------------------------------------

    def on_session_start(self, session_id: str) -> None:
        pass

    def on_session_end(self, session_id: str) -> None:
        pass

    # ---- Per-message ingest -------------------------------------------

    def ingest(self, session_id: str, message: dict) -> None:
        pass

    # ---- Per-turn prepare ---------------------------------------------

    def prepare(self, *,
                agent: Any,
                session: dict,
                history: list[dict],
                model: Any,
                tools: list[Any] | None = None,
                ) -> TurnPrep:
        raise NotImplementedError

    def should_recommend(self, prep: TurnPrep) -> bool:
        return False

    def should_auto_compact(self, prep: TurnPrep) -> bool:
        return False

    # ---- Compaction ----------------------------------------------------

    async def compact(self, *,
                      agent: Any,
                      session_id: str,
                      model: Any,
                      on_event: Optional[EventCallback] = None,
                      previous_summary: Optional[str] = None,
                      user_initiated: bool = False,
                      cancel_event: Optional[threading.Event] = None,
                      keep_recent_tokens: Optional[int] = None,
                      ) -> CompactResult:
        raise NotImplementedError

    # ---- Post-turn -----------------------------------------------------

    def after_turn(self,
                   session_id: str,
                   *,
                   usage: dict | None,
                   prep: Optional[TurnPrep] = None,
                   on_event: Optional[EventCallback] = None,
                   ) -> None:
        pass


# ---------------------------------------------------------------------------
# Default implementation — composes the components
# ---------------------------------------------------------------------------

class DefaultContextEngine(ContextEngine):
    """Production engine. Three-tier policy with full lifecycle.

    Thresholds (overridable via constructor):
        RECOMMEND_PCT  = 0.70   surface "context filling up" event
        AUTO_COMPACT_PCT = 0.85 inline compaction before next LLM call
    """

    name = "default"

    # Two-tier triggers (see README §4):
    #   RECOMMEND_PCT       surface compaction_recommended event
    #   AUTO_COMPACT_PCT    proactive compact — still have summary budget
    #   EMERGENCY_PCT       last-resort compact before the next call dies
    RECOMMEND_PCT = 0.70
    AUTO_COMPACT_PCT = 0.80
    EMERGENCY_PCT = 0.95

    def __init__(self,
                 *,
                 usage_tracker: UsageTracker | None = None,
                 budget_allocator: BudgetAllocator | None = None,
                 ager: TurnAger | None = None,
                 summarizer: Summarizer | None = None,
                 persister: Persister | None = None,
                 references: ReferenceTracker | None = None,
                 recommend_pct: float | None = None,
                 auto_compact_pct: float | None = None,
                 ):
        self.usage = usage_tracker or _usage_tracker
        self.budgets = budget_allocator or default_allocator
        self.ager = ager or default_ager
        self.summarizer = summarizer or default_summarizer
        self.persister = persister or default_persister
        self.references = references or _ref_tracker
        if recommend_pct is not None:
            self.RECOMMEND_PCT = recommend_pct
        if auto_compact_pct is not None:
            self.AUTO_COMPACT_PCT = auto_compact_pct

    # ---- Lifecycle -----------------------------------------------------

    def on_session_start(self, session_id: str) -> None:
        # Pre-warm the usage cache so the first prepare() doesn't pay
        # the DB-read cost.
        self.usage.get(session_id)

    def on_session_end(self, session_id: str) -> None:
        self.usage.on_session_end(session_id)

    def ingest(self, session_id: str, message: dict) -> None:
        # Default no-op. Future engines could update inverted indexes here.
        return None

    # ---- Prepare -------------------------------------------------------

    def prepare(self, *,
                agent: Any,
                session: dict,
                history: list[dict],
                model: Any,
                tools: list[Any] | None = None,
                ) -> TurnPrep:
        decision: list[str] = []
        session_id = (session or {}).get("id") or ""

        # 1. Reference scan — once per prepare, shared with ager.
        ref_map = self.references.build(history)
        if ref_map.cited_tool_use_ids:
            decision.append(
                f"references:protected={len(ref_map.cited_tool_use_ids)}"
            )

        # 2. Apply aging.
        aged_history, n_redacted, tokens_freed = self.ager.age(
            history, ref_map=ref_map,
        )
        if n_redacted:
            decision.append(
                f"aged:n={n_redacted},freed≈{tokens_freed}tok"
            )

        # 3. Build messages + system prompt.
        agent_messages = self._assemble_messages(aged_history)
        system_prompt = self._build_system_prompt(agent)

        # 4. Allocate budget.
        budget = self.budgets.allocate(
            context_window=real_context_window(model),
            system_prompt=system_prompt,
            history=aged_history,
            tools=tools,
        )

        # 5. Hybridise with provider-reported usage if we have it.
        usage = self.usage.get(session_id) if session_id else UsageSnapshot()
        if usage.source == "provider" and usage.last_prompt_tokens > 0:
            # Trust the provider on the prefix; add our estimated delta
            # for anything added since.
            blended, src = self.usage.estimated_input(
                session_id, budget.history,
            )
            # Replace history with the blended number.
            budget.history = blended
            decision.append(f"usage:source={src}")
        else:
            decision.append(f"usage:source={usage.source}")

        # 6. Note any active summary id stamped on session.extra_meta.
        summary_id = None
        try:
            extra_meta = (session or {}).get("extra_meta") or {}
            summary_id = extra_meta.get("_last_summary_id")
        except Exception:
            pass

        return TurnPrep(
            system_prompt=system_prompt,
            agent_messages=agent_messages,
            history_dicts=aged_history,
            budget=budget,
            usage=usage,
            tool_results_redacted=n_redacted,
            tokens_freed_by_aging=tokens_freed,
            references_protected=len(ref_map.cited_tool_use_ids),
            summary_id=summary_id,
            decision_path=decision,
        )

    def should_recommend(self, prep: TurnPrep) -> bool:
        return prep.budget_pct >= self.RECOMMEND_PCT

    def should_auto_compact(self, prep: TurnPrep) -> bool:
        return prep.budget_pct >= self.AUTO_COMPACT_PCT

    # ---- Compaction ----------------------------------------------------

    async def compact(self, *,
                      agent: Any,
                      session_id: str,
                      model: Any,
                      on_event: Optional[EventCallback] = None,
                      previous_summary: Optional[str] = None,
                      user_initiated: bool = False,
                      cancel_event: Optional[threading.Event] = None,
                      keep_recent_tokens: Optional[int] = None,
                      ) -> CompactResult:
        import time
        from openprogram.agent.session_db import default_db

        started = time.time()
        db = default_db()
        sess = db.get_session(session_id) or {}
        history = db.get_branch(session_id) or []
        tokens_before = self._estimate(history)

        if len(history) < 4:
            return CompactResult(
                ok=True,
                tokens_before=tokens_before,
                tokens_after=tokens_before,
                reason="auto" if not user_initiated else "manual",
            )

        # Chain on previous summary if not supplied.
        if previous_summary is None:
            extra_meta = sess.get("extra_meta") or {}
            previous_summary = extra_meta.get("_last_summary_text")

        if on_event:
            on_event({"type": "chat_response", "data": {
                "type": "compaction_started",
                "session_id": session_id,
                "user_initiated": user_initiated,
                "tokens_before": tokens_before,
            }})

        summary = await self.summarizer.summarise(
            messages=history,
            model=model,
            previous_summary=previous_summary,
            cancel_event=cancel_event,
            keep_recent_tokens=keep_recent_tokens,
            context_window=real_context_window(model),
        )

        # Persist + re-parent.
        summary_id: Optional[str] = None
        if summary.summary_text:
            summary_id = self.persister.insert_summary_node(
                session_id,
                summary_text=summary.summary_text,
                cut_idx=summary.cut_idx,
                history=history,
            )

        # Update session meta for incremental chain + usage counters.
        if summary_id:
            try:
                db.update_session(
                    session_id,
                    _last_summary_id=summary_id,
                    _last_summary_text=summary.summary_text,
                    _last_compacted_at=time.time(),
                )
            except Exception:
                pass
            self.usage.record_compaction(session_id)

        new_history = db.get_branch(session_id) or history
        tokens_after = self._estimate(new_history)

        result = CompactResult(
            ok=bool(summary_id),
            summary_text=summary.summary_text,
            summary_id=summary_id,
            summarised_count=summary.summarised_count,
            summarised_tokens=summary.summarised_tokens,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            duration_ms=int((time.time() - started) * 1000),
            used_previous_summary=summary.previous_summary_used,
            reason=("manual" if user_initiated
                    else ("recovered" if summary.fell_back_to_structural
                          else "auto")),
            error=summary.error,
            fell_back_to_structural=summary.fell_back_to_structural,
        )

        if on_event:
            on_event({"type": "chat_response", "data": {
                "type": "compaction_finished",
                "session_id": session_id,
                "user_initiated": user_initiated,
                "summary_id": summary_id,
                "summarised_count": result.summarised_count,
                "summarised_tokens": result.summarised_tokens,
                "tokens_before": result.tokens_before,
                "tokens_after": result.tokens_after,
                "duration_ms": result.duration_ms,
                "fell_back_to_structural": result.fell_back_to_structural,
                "used_previous_summary": result.used_previous_summary,
            }})

        return result

    # ---- Post-turn -----------------------------------------------------

    def after_turn(self,
                   session_id: str,
                   *,
                   usage: dict | None,
                   prep: Optional[TurnPrep] = None,
                   on_event: Optional[EventCallback] = None,
                   ) -> None:
        # Feed real numbers back into the tracker.
        snap = self.usage.record_turn(session_id, usage=usage)
        # Emit recommend if this turn pushed us over.
        if prep is None or not on_event:
            return
        # Re-derive a fresh budget_pct from the post-turn numbers.
        if prep.context_window > 0:
            pct = snap.last_prompt_tokens / prep.context_window
        else:
            pct = 0.0
        if pct >= self.RECOMMEND_PCT:
            on_event({"type": "chat_response", "data": {
                "type": "compaction_recommended",
                "session_id": session_id,
                "input_tokens": snap.last_prompt_tokens,
                "context_window": prep.context_window,
                "budget_pct": pct,
                "source": snap.source,
            }})

    # ---- Internals -----------------------------------------------------

    def _assemble_messages(self, history: list[dict]) -> list:
        import time
        from openprogram.providers.types import (
            AssistantMessage, TextContent, UserMessage,
        )
        out: list = []
        for m in history:
            role = m.get("role")
            content = m.get("content") or ""
            ts = int((m.get("timestamp") or time.time()) * 1000)
            if role == "user":
                out.append(UserMessage(
                    content=[TextContent(text=content)],
                    timestamp=ts,
                ))
            elif role == "assistant":
                try:
                    out.append(AssistantMessage(
                        content=[TextContent(text=content)],
                        api="completion",
                        provider="openai",
                        model="gpt-5",
                        timestamp=ts,
                    ))
                except Exception:
                    pass
        return out

    def _build_system_prompt(self, agent: Any) -> str:
        from openprogram.context.system_prompt import build_system_prompt
        return build_system_prompt(agent)

    def _estimate(self, history: list[dict]) -> int:
        from openprogram.context.tokens import estimate_history_tokens
        return estimate_history_tokens(history)


# ---------------------------------------------------------------------------
# Plugin registry — config-driven engine selection (Hermes-style)
# ---------------------------------------------------------------------------

CONTEXT_ENGINE_REGISTRY: dict[str, ContextEngine] = {}


def register_engine(engine: ContextEngine) -> ContextEngine:
    CONTEXT_ENGINE_REGISTRY[engine.name] = engine
    return engine


def get_engine(name: str | None = None) -> ContextEngine:
    if name and name in CONTEXT_ENGINE_REGISTRY:
        return CONTEXT_ENGINE_REGISTRY[name]
    return default_engine


def resolve_engine_for(agent: Any) -> ContextEngine:
    """Pick the engine for ``agent``, honouring config order:

    1. ``agent.context_engine`` field (per-agent override)
    2. ``config.context.engine`` (global setting, future)
    3. ``default_engine``
    """
    requested = getattr(agent, "context_engine", None)
    if not requested:
        try:
            from openprogram.setup import _read_config
            requested = (_read_config().get("context") or {}).get("engine")
        except Exception:
            requested = None
    return get_engine(requested)


# Module-level singleton + register
default_engine: ContextEngine = DefaultContextEngine()
register_engine(default_engine)


__all__ = [
    "ContextEngine",
    "DefaultContextEngine",
    "default_engine",
    "register_engine",
    "get_engine",
    "resolve_engine_for",
    "CONTEXT_ENGINE_REGISTRY",
]
