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
``_build_messages_from_dag``, ``_build_system_prompt`` — that
subclasses commonly want to override on its own.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable, Optional

from openprogram.context.budget import BudgetAllocator, default_allocator
from openprogram.context.persistence import Persister, default_persister
from openprogram.context.references import ReferenceTracker, default_tracker as _ref_tracker
from openprogram.context.summarize import Summarizer, default_summarizer
from openprogram.context.tokens import real_context_window
from openprogram.context.types import (
    BudgetAllocation,
    CompactResult,
    TurnPrep,
    UsageState,
)
from openprogram.context.usage import UsageTracker, default_tracker as _usage_tracker


_log = logging.getLogger(__name__)

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
                system_prompt: Optional[str] = None,
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
        AUTO_COMPACT_PCT = 0.80 inline compaction before next LLM call
    """

    name = "default"

    # Two-tier triggers (see README §4):
    #   RECOMMEND_PCT       surface compaction_recommended event
    #   AUTO_COMPACT_PCT    proactive compact — still have summary budget
    RECOMMEND_PCT = 0.70
    AUTO_COMPACT_PCT = 0.80

    def __init__(self,
                 *,
                 usage_tracker: UsageTracker | None = None,
                 budget_allocator: BudgetAllocator | None = None,
                 summarizer: Summarizer | None = None,
                 persister: Persister | None = None,
                 references: ReferenceTracker | None = None,
                 recommend_pct: float | None = None,
                 auto_compact_pct: float | None = None,
                 ):
        self.usage = usage_tracker or _usage_tracker
        self.budgets = budget_allocator or default_allocator
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
                system_prompt: Optional[str] = None,
                ) -> TurnPrep:
        decision: list[str] = []
        session_id = (session or {}).get("id") or ""

        # 1. Reference scan — surfaces cited tool_use ids in TurnPrep.
        #    ContextCommit rules don't yet consume this (they use locked= flag
        #    on items instead), but the TurnPrep caller still expects
        #    n_redacted / ref counts in its log line — keep computing it.
        ref_map = self.references.build(history)
        if ref_map.cited_tool_use_ids:
            decision.append(
                f"references:protected={len(ref_map.cited_tool_use_ids)}"
            )

        # 2-3. Build the LLM input from the DAG — render_context +
        #    render_dag_messages, the same pipeline runtime.exec uses.
        #    ONE pipeline: there is no fallback. A render failure is a
        #    real failure and must surface as a failed turn rather than
        #    silently degrading to a differently-shaped prompt, which is
        #    invisible in production and impossible to debug after the
        #    fact (dag/overview.md §8).
        compacted_history = history
        n_redacted = 0
        tokens_freed = 0
        agent_messages = self._build_messages_from_dag(
            session_id=session_id,
            history=history,
            model=model,
        )
        decision.append("input:dag render")

        # dag/overview.md §7: the caller (dispatcher) assembled the wire
        # prompt already — budget THAT string, never a second assembly.
        # Standalone callers that pass nothing get one built here.
        if system_prompt is None:
            system_prompt = self._build_system_prompt(agent, tools=tools)

        # 4. Allocate budget.
        from openprogram.programs import split_tools_for_dispatch
        provider_tools, _deferred_catalog = split_tools_for_dispatch(tools or [])
        budget = self.budgets.allocate(
            context_window=real_context_window(model),
            system_prompt=system_prompt,
            history=compacted_history,
            tools=provider_tools,
        )

        # 5. Hybridise with provider-reported usage if we have it.
        usage = self.usage.get(session_id) if session_id else UsageState()
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
        # ``session`` may be None or carry a non-dict extra_meta from an
        # older record; both are handled without a guard.
        extra_meta = (session or {}).get("extra_meta")
        summary_id = (
            extra_meta.get("_last_summary_id")
            if isinstance(extra_meta, dict) else None
        )

        return TurnPrep(
            system_prompt=system_prompt,
            agent_messages=agent_messages,
            history_dicts=compacted_history,
            budget=budget,
            usage=usage,
            tool_results_redacted=n_redacted,
            tokens_freed_by_microcompact=tokens_freed,
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
        from openprogram.context.persistence import rendered_history

        db = default_db()
        sess = db.get_session(session_id) or {}
        # §4 step 1: compaction consumes the RENDERED view — active
        # summary first, then the kept turns — so a re-compaction eats
        # "previous summary + more turns" instead of re-summarising raw
        # turns the previous summary already covers.
        history = rendered_history(db, session_id)
        tokens_before = self._occupancy_tokens(session_id, history)
        reason = "auto" if not user_initiated else "manual"

        def _no_op(extra: str | None = None) -> CompactResult:
            return CompactResult(
                ok=True,
                no_op=True,
                tokens_before=tokens_before,
                tokens_after=tokens_before,
                duration_ms=int((time.time() - started) * 1000),
                reason=reason,
                error=extra,
            )

        if len(history) < 4:
            result = _no_op()
            self._emit_compaction_finished(
                on_event, session_id=session_id,
                user_initiated=user_initiated, result=result,
            )
            return result

        window = real_context_window(model) or 0
        cut = self.summarizer.find_cut_index(
            history,
            keep_recent_tokens=keep_recent_tokens,
            context_window=window,
        )
        if cut <= self.summarizer.protect_first_n:
            result = _no_op()
            self._emit_compaction_finished(
                on_event, session_id=session_id,
                user_initiated=user_initiated, result=result,
            )
            return result

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
            context_window=window,
        )

        if (summary.cut_idx <= 0 or summary.summarised_count == 0
                or not summary.summary_text):
            result = _no_op(summary.error)
            self._emit_compaction_finished(
                on_event, session_id=session_id,
                user_initiated=user_initiated, result=result,
            )
            return result

        summary_id = self.persister.insert_summary_node(
            session_id,
            summary_text=summary.summary_text,
            cut_idx=summary.cut_idx,
            history=history,
        )
        if not summary_id:
            result = _no_op("insert_summary_node returned None")
            self._emit_compaction_finished(
                on_event, session_id=session_id,
                user_initiated=user_initiated, result=result,
            )
            return result

        try:
            db.update_session(
                session_id,
                _last_summary_id=summary_id,
                _last_summary_text=summary.summary_text,
                _last_compacted_at=time.time(),
            )
        except Exception:
            # Losing this silently makes the next turn recompact the
            # same range, so it is worth a log line.
            _log.warning(
                "failed to persist compaction summary state for session %s",
                session_id, exc_info=True,
            )
        self.usage.record_compaction(session_id)

        new_history = rendered_history(db, session_id) or history
        tokens_after = self._occupancy_tokens(session_id, new_history)
        try:
            db.merge_node_metadata(session_id, summary_id, {
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "summarised_count": summary.summarised_count,
                "compacted_at": time.time(),
            })
        except Exception:
            _log.warning(
                "failed to stamp compaction stats on %s",
                summary_id, exc_info=True,
            )

        result = CompactResult(
            ok=True,
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
        self._emit_compaction_finished(
            on_event, session_id=session_id,
            user_initiated=user_initiated, result=result,
        )

        # 事件层 tap（懒 import 防循环）。emit_safe 自己吞异常，无需再包一层。
        from openprogram.events import emit_safe
        emit_safe("context.compacted", "system",
                  {"ok": result.ok,
                   "tokens_before": result.tokens_before,
                   "tokens_after": result.tokens_after,
                   "reason": result.reason},
                  {"session": session_id})

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
        commit = self.usage.record_turn(session_id, usage=usage)
        # Emit recommend if this turn pushed us over.
        if prep is None or not on_event:
            return
        # Re-derive a fresh budget_pct from the post-turn numbers.
        if prep.context_window > 0:
            pct = commit.last_prompt_tokens / prep.context_window
        else:
            pct = 0.0
        if pct >= self.RECOMMEND_PCT:
            on_event({"type": "chat_response", "data": {
                "type": "compaction_recommended",
                "session_id": session_id,
                "input_tokens": commit.last_prompt_tokens,
                "context_window": prep.context_window,
                "budget_pct": pct,
                "source": commit.source,
            }})
            # 事件层 tap（懒 import 防循环）。emit_safe 自己吞异常。
            from openprogram.events import emit_safe
            emit_safe("context.compaction_recommended", "system",
                      {"budget_pct": round(pct, 3)},
                      {"session": session_id})

    # ---- Internals -----------------------------------------------------

    def _build_system_prompt(self, agent: Any, *, tools: Any = None) -> str:
        from openprogram.context.components import build_system_prompt
        return build_system_prompt(agent, tools=tools)

    def _build_messages_from_dag(
        self,
        *,
        session_id: str,
        history: list[dict],
        model: Any,
    ) -> list:
        """Build provider Message[] via render_context + render_dag_messages —
        the SAME context pipeline runtime.exec uses (dag/overview.md
        step 4). Chat thus reads context from the one DAG, frame=-1
        (top-level: all in-frame → full accumulation).

        The engine does exactly what §6 leaves it: resolve the head,
        call render_context, hand the result to render_dag_messages.
        Branch isolation is render_context's own walk — no set
        intersection, no caller-based re-admission patch here.

        The one thing still resolved locally is WHICH head to render
        from. agent_loop re-adds the just-persisted user message as the
        live prompt, so rendering from the literal branch tip would feed
        the model that message twice. The tip at prepare() time is the
        in-flight assistant PLACEHOLDER (dispatcher step 3b writes it and
        moves head before the loop runs), giving the shape
        ``[..., userN, placeholderN]``; walk back past the placeholder
        and past userN, and render from what precedes them. See
        test_retry_branch_isolation.py.

        Raises on failure → prepare() catches and falls back to _assemble.
        """
        if not session_id:
            raise RuntimeError("dag render path requires session_id")
        from openprogram.context.nodes import render_context
        from openprogram.context.render import render_dag_messages
        from openprogram.store.session.session_node_writer import SessionNodeWriter
        from openprogram.agent.session_db import default_db

        db = default_db()
        shim = SessionNodeWriter(db, session_id)
        graph = shim.load()

        branch = db.get_branch(session_id) or history or []
        branch_ids = [b.get("id") for b in branch if b.get("id")]

        head_id = None
        for nid in reversed(branch_ids):
            n = graph.nodes.get(nid)
            if n is None:
                continue
            if n.is_llm() and not (n.output or "").strip():
                continue          # in-flight assistant placeholder
            if n.is_user():
                head_id = n.predecessor   # render from before the live prompt
                break
            head_id = nid         # completed reply — nothing to exclude
            break

        read_ids = (
            render_context(graph, head_id=head_id, frame_entry_seq=-1)
            if head_id and head_id in graph.nodes else []
        )

        # Drop abandoned assistant turns. A stream that died mid-flight
        # (crashed worker, dropped connection) leaves an llm node with
        # status "running" and empty output sitting IN the branch, and
        # rendering it feeds the model a contentless assistant message
        # between two user messages — some providers reject that outright,
        # and it teaches the model that empty replies are acceptable.
        # A node that owns tool calls is kept regardless: its ToolCall
        # entries have to hang off an assistant message or the following
        # tool_result is orphaned.
        owns_tool_call = {
            (graph.nodes[nid].caller or "")
            for nid in read_ids
            if nid in graph.nodes and graph.nodes[nid].is_code()
        }

        def _is_abandoned(nid: str) -> bool:
            n = graph.nodes.get(nid)
            if n is None or not n.is_llm():
                return False
            if (n.output or "").strip() or nid in owns_tool_call:
                return False
            return ((n.metadata or {}).get("status") or "") == "running"

        read_ids = [nid for nid in read_ids if not _is_abandoned(nid)]

        history_dir = None
        try:
            _sdir_fn = getattr(db, "_session_dir", None)
            if _sdir_fn:
                history_dir = str(_sdir_fn(session_id) / "history")
        except Exception:
            history_dir = None

        return render_dag_messages(graph, read_ids, history_dir)

    def _estimate(self, history: list[dict]) -> int:
        from openprogram.context.tokens import estimate_history_tokens
        return estimate_history_tokens(history)

    def _occupancy_tokens(self, session_id: str, history: list[dict]) -> int:
        """Same total the ring and /context panel use (rendered view)."""
        try:
            from openprogram.context.session_stats import estimate_total_used
            total, _ = estimate_total_used(session_id)
            if total > 0:
                return int(total)
        except Exception:
            _log.debug(
                "occupancy estimate fell back to history tokens",
                exc_info=True,
            )
        return self._estimate(history)

    def _emit_compaction_finished(
        self,
        on_event: Optional[EventCallback],
        *,
        session_id: str,
        user_initiated: bool,
        result: CompactResult,
    ) -> None:
        if not on_event:
            return
        on_event({"type": "chat_response", "data": {
            "type": "compaction_finished",
            "session_id": session_id,
            "user_initiated": user_initiated,
            "no_op": result.no_op,
            "summary_id": result.summary_id,
            "summarised_count": result.summarised_count,
            "summarised_tokens": result.summarised_tokens,
            "tokens_before": result.tokens_before,
            "tokens_after": result.tokens_after,
            "duration_ms": result.duration_ms,
            "fell_back_to_structural": result.fell_back_to_structural,
            "used_previous_summary": result.used_previous_summary,
        }})


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
