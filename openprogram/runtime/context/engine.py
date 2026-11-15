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

import logging
import threading
from typing import Any, Callable, Optional

from openprogram.runtime.context.budget import BudgetAllocator, default_allocator
from openprogram.runtime.context.persistence import Persister, default_persister
from openprogram.runtime.context.compaction.summarize import Summarizer, default_summarizer
from openprogram.runtime.context.tokens import real_context_window
from openprogram.runtime.context.types import (
    CompactResult,
    TurnPrep,
    UsageState,
)
from openprogram.runtime.context.usage import UsageTracker, default_tracker as _usage_tracker


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
                tail_tokens: int | None = None,
                clean_history: bool = False,
                head_id: Optional[str] = None,
                audience: str = "own_prompt",
                ) -> TurnPrep:
        raise NotImplementedError

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
                      head_id: Optional[str] = None,
                      trigger: Optional[str] = None,
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

_NO_REDUCTION = "Summary does not reduce context; history unchanged"
_CANCELLED = "Compaction cancelled; history unchanged"


def _turn_branch(session_id: str) -> Optional[str]:
    """The branch of the turn running this compaction, when it is one of
    this conversation's turns."""
    try:
        from openprogram.state import execution_facts
        return execution_facts.turn_branch(session_id)
    except Exception:
        return None


def _model_name(model: Any) -> str:
    """``provider/id`` of the model a summary was written with."""
    model_id = getattr(model, "id", None) or (str(model) if model else "")
    provider = getattr(model, "provider", None)
    return f"{provider}/{model_id}" if provider and model_id else model_id


def _compaction_audience() -> str:
    """Whom a compaction's input is rendered for: the own-prompt audience,
    without recalled memory while memory is off, so the summary does not
    carry it on (``turn_context.without_recall``). Earlier turns keep what
    they were sent until then."""
    from openprogram.runtime.context.turn_context import OWN_PROMPT, without_recall
    return OWN_PROMPT if memory_readable() else without_recall(OWN_PROMPT)


def memory_readable() -> bool:
    try:
        from openprogram.state.memory.policy import allowed, current
        return current() is None or allowed()
    except Exception:
        return False


class DefaultContextEngine(ContextEngine):
    """Production engine. Auto-compact and manual compact with full lifecycle.

    Automatic compaction runs before the next LLM call once the prompt and
    the reply's reserve reach ``context.auto_compact_percent`` of the
    window (80 by default), read at each decision so a change applies
    live; a constructor ``auto_compact_pct`` overrides it.
    """

    name = "default"

    def __init__(self,
                 *,
                 usage_tracker: UsageTracker | None = None,
                 budget_allocator: BudgetAllocator | None = None,
                 summarizer: Summarizer | None = None,
                 persister: Persister | None = None,
                 auto_compact_pct: float | None = None,
                 ):
        self.usage = usage_tracker or _usage_tracker
        self.budgets = budget_allocator or default_allocator
        self.summarizer = summarizer or default_summarizer
        self.persister = persister or default_persister
        self._auto_compact_pct = auto_compact_pct

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
                tail_tokens: int | None = None,
                clean_history: bool = False,
                head_id: Optional[str] = None,
                audience: str = "own_prompt",
                ) -> TurnPrep:
        decision: list[str] = []
        session_id = (session or {}).get("id") or ""

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
        shown: dict = {}
        agent_messages = self._build_messages_from_dag(
            session_id=session_id,
            history=history,
            model=model,
            tail_tokens=tail_tokens,
            clean_history=clean_history,
            head_id=head_id,
            shown=shown,
            audience=audience,
        )
        decision.append("input:dag render")

        # dag/overview.md §7: the caller (dispatcher) assembled the wire
        # prompt already — budget THAT string, never a second assembly.
        # Standalone callers that pass nothing get one built here.
        if system_prompt is None:
            system_prompt = self._build_system_prompt(agent, tools=tools)

        # 4. Allocate budget.
        from openprogram.programs import loaded_tools, split_tools_for_dispatch
        provider_tools, _deferred_catalog = split_tools_for_dispatch(
            tools or [], loaded_tools(agent_messages))
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

        return TurnPrep(
            system_prompt=system_prompt,
            agent_messages=agent_messages,
            history_dicts=compacted_history,
            budget=budget,
            usage=usage,
            tool_results_redacted=n_redacted,
            tokens_freed_by_microcompact=tokens_freed,
            section_notes=dict(shown.get("section_notes") or {}),
            turn_view=shown.get("turn_view"),
            decision_path=decision,
        )

    @property
    def auto_compact_pct(self) -> float:
        """The share of the window automatic compaction runs at."""
        if self._auto_compact_pct is not None:
            return self._auto_compact_pct
        from openprogram.state.settings import setting_value
        return setting_value("context.auto_compact_percent") / 100

    def should_auto_compact(self, prep: TurnPrep) -> bool:
        return prep.budget_pct >= self.auto_compact_pct

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
                      head_id: Optional[str] = None,
                      trigger: Optional[str] = None,
                      ) -> CompactResult:
        """Summarise the older part of one chain into a summary node.

        ``head_id`` is the tip of the chain to compact; the caller names it,
        so a compaction never lands on a branch it did not read (``None``
        reads the session's head once, when the view is loaded).
        ``cancel_event`` stops the summary request mid-stream; nothing is
        written then. ``trigger`` names why it ran (``manual``, ``auto``,
        ``reactive``) on the node; it defaults from ``user_initiated``.
        """
        import asyncio
        import time

        started = time.time()
        from openprogram.runtime.context.model import Context
        db = Context.turn_store(session_id)
        # §4 step 1: compaction consumes the RENDERED view — active
        # summary first, then the kept turns — so a re-compaction eats
        # "previous summary + more turns" instead of re-summarising raw
        # turns the previous summary already covers.
        from openprogram.runtime.context.compaction.view import load_compaction_view
        from openprogram.runtime.context.tokens import estimate_history_tokens
        view = load_compaction_view(db, session_id, head_id, audience=_compaction_audience())
        history = view.history
        tokens_before = self._occupancy_tokens(session_id, history, head_id=view.head_id)
        reason = "auto" if not user_initiated else "manual"
        trigger = trigger or reason

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

        def _finish(result: CompactResult) -> CompactResult:
            self._emit_compaction_finished(
                on_event, session_id=session_id,
                user_initiated=user_initiated, result=result, trigger=trigger,
            )
            return result

        if (len(history) < 4
                or (cancel_event is not None and cancel_event.is_set())):
            return _finish(_no_op())

        window = real_context_window(model) or 0
        cut = self.summarizer.find_cut_index(
            history,
            keep_recent_tokens=keep_recent_tokens,
            context_window=window,
        )
        if cut <= self.summarizer.protect_first_n:
            return _finish(_no_op())

        from openprogram.runtime.context.compaction.hold import default_hold, hold_key
        held_as = hold_key(session_id, model, _turn_branch(session_id))
        held = None if user_initiated else default_hold.held(held_as)
        if held is not None:
            return _finish(_no_op(held))

        # The active branch's previous summary is already in the covered
        # input. Never inject the session-global cache from another branch.
        previous_summary = None

        if on_event:
            on_event({"type": "chat_response", "data": {
                "type": "compaction_started",
                "session_id": session_id,
                "user_initiated": user_initiated,
                # manual, auto, mid_turn, reactive or model_downshift.
                "trigger": trigger,
                "tokens_before": tokens_before,
            }})

        # The conversation's last request on this chain, sent again with the
        # summary prompt, reads its cached prefix (compaction/fork.py). Not
        # when recovering from an overflow: that request is what did not fit.
        fork = None
        if trigger != "reactive":
            from openprogram.runtime.context.compaction.fork import fork_source
            from openprogram.runtime.context.compaction.prompts import FORK_SUMMARY_PROMPT
            cap = int(getattr(self.summarizer, "max_summary_tokens", 0) or 20_000)
            fork = fork_source(
                session_id, getattr(view, "rendered_ids", None) or (), model, prompt=FORK_SUMMARY_PROMPT,
                output_tokens=min(cap, int(getattr(model, "max_tokens", 0) or cap)))

        try:
            summary = await self.summarizer.summarise(
                messages=history,
                model=model,
                previous_summary=previous_summary,
                cancel_event=cancel_event,
                keep_recent_tokens=keep_recent_tokens,
                context_window=window,
                **({"fork": fork} if fork is not None else {}),
            )
        except asyncio.CancelledError:
            # A Stop closed the summary stream: the turn ends as stopped,
            # not as a failed compaction, and nothing counts toward the hold.
            if cancel_event is not None and cancel_event.is_set():
                return _finish(_no_op(_CANCELLED))
            raise
        except Exception as error:
            default_hold.failed(held_as, f"{type(error).__name__}: {error}")
            raise

        if cancel_event is not None and cancel_event.is_set():
            return _finish(_no_op(_CANCELLED))
        if (summary.cut_idx <= 0 or summary.summarised_count == 0
                or not summary.summary_text):
            if summary.error:
                default_hold.failed(held_as, summary.error)
            return _finish(_no_op(summary.error))

        # The summary is complete when it gets here: it has every section and
        # stopped before its output cap (the summarizer fails it otherwise).
        # What is left to check is that the chain is the one it read and
        # that the render gets strictly smaller.
        saved = 0
        if not view.unchanged(db, session_id):
            result = _no_op("Context changed during compaction; retry with current history")
        elif not 0 < summary.cut_idx < len(history):
            result = _no_op("Invalid compaction range")
        else:
            from openprogram.runtime.context.persistence import covered_chain_ids
            covers = covered_chain_ids(history[:summary.cut_idx])
            restored = self._restored_state(session_id, view, covers)
            candidate = view.candidate_messages(summary.summary_text, summary.cut_idx,
                                                restored.get("restored_state") or "")
            saved = estimate_history_tokens(view.messages) - estimate_history_tokens(candidate)
            result = _no_op(_NO_REDUCTION) if saved <= 0 else None
        if result is not None:
            if result.error == _NO_REDUCTION:
                default_hold.failed(held_as, result.error)
            return _finish(result)

        # Everything the node states is known before it is written, and goes
        # in with it: one write, no stamping afterwards. ``tokens_after`` is
        # the ring's own reading less what the summary saves on this chain.
        covered_count = len(covers)
        tokens_after = max(0, tokens_before - saved)
        compacted_at = time.time()
        summary_id = self.persister.insert_summary_node(
            session_id,
            summary_text=summary.summary_text,
            cut_idx=summary.cut_idx,
            history=history,
            facts={
                "trigger": trigger,
                "model": _model_name(model),
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "summarised_count": covered_count,
                "compacted_at": compacted_at,
                # Given to the model as context, not as its own words
                # (Codex, Pi, OpenCode, Claude Code all do).
                "render_role": "user",
            },
            context_window=window,
            **restored,
        )
        if not summary_id:
            return _finish(_no_op("insert_summary_node returned None"))
        default_hold.done(held_as)
        from openprogram.models.usage.prefix_fingerprint import expect_break
        expect_break(session_id, "compaction")

        self.usage.record_compaction(session_id)

        result = _finish(CompactResult(
            ok=True,
            summary_text=summary.summary_text,
            summary_id=summary_id,
            summarised_count=covered_count,
            summarised_tokens=summary.summarised_tokens,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            duration_ms=int((time.time() - started) * 1000),
            used_previous_summary=any(m.get("covers_ids") for m in history[:summary.cut_idx]),
            reason=("manual" if user_initiated
                    else ("recovered" if summary.fell_back_to_structural
                          else "auto")),
            error=summary.error,
            fell_back_to_structural=summary.fell_back_to_structural,
        ))

        # 事件层 tap（懒 import 防循环）。emit_safe 自己吞异常，无需再包一层。
        from openprogram.runtime.events import emit_safe
        emit_safe("context.compacted", "system",
                  {"ok": result.ok,
                   "tokens_before": result.tokens_before,
                   "tokens_after": result.tokens_after,
                   "reason": result.reason},
                  {"session": session_id})

        return result

    @staticmethod
    def _restored_state(session_id: str, view: Any, covers: list[str]) -> dict:
        """The working state written after the summary and the file lists
        kept with it (compaction/restore.py). A summary is worth keeping
        without them, so a failure leaves them out."""
        try:
            from openprogram.runtime.context.compaction.restore import restored
            text, files = restored(session_id, view.graph, view.head_id, covers)
            return {"restored_state": text, "restored_files": files}
        except Exception:
            _log.warning("restored state left out of the summary for %s", session_id, exc_info=True)
            return {}

    # ---- Post-turn -----------------------------------------------------

    def after_turn(self,
                   session_id: str,
                   *,
                   usage: dict | None,
                   prep: Optional[TurnPrep] = None,
                   on_event: Optional[EventCallback] = None,
                   ) -> None:
        # Feed real numbers back into the tracker.
        # prep/on_event stay on the dispatcher contract; they are unused here.
        _ = (prep, on_event)
        self.usage.record_turn(session_id, usage=usage)

    # ---- Internals -----------------------------------------------------

    def _build_system_prompt(self, agent: Any, *, tools: Any = None) -> str:
        from openprogram.runtime.context.components import build_system_prompt
        return build_system_prompt(agent, tools=tools)

    def _build_messages_from_dag(
        self,
        *,
        session_id: str,
        history: list[dict],
        model: Any,
        tail_tokens: int | None = None,
        clean_history: bool = False,
        head_id: Optional[str] = None,
        shown: Optional[dict] = None,
        audience: str = "own_prompt",
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

        ``tail_tokens`` bounds the rendered conversation to the summary and
        the latest steps (context/tail.py), for a turn that must stay small
        without compacting.

        ``head_id`` is the tip of the branch a turn runs on when that is not
        the selected branch (a turn beside it); otherwise the selected
        branch's tip is read.

        ``clean_history`` is a run that starts from nothing (a scheduled
        task, a clean Job or sub-agent): the conversation it sits in is not
        its context.

        ``shown``, when given, receives what the render shows that the
        caller decides on: ``section_notes``, the last note of each section
        in it, and ``turn_view``, what it shows of earlier dates, plan-mode
        and Goal notes and todo use (``context.turn_context``).

        ``audience`` is who the request is for (``context.turn_context``): a
        conversation turn's own request shows the section notes beside
        earlier messages; the default, a call with its own prompt, does not.

        Raises on failure → prepare() catches and falls back to _assemble.
        """
        if clean_history:
            return []
        if not session_id:
            raise RuntimeError("dag render path requires session_id")
        from openprogram.runtime.context.nodes import render_context
        from openprogram.runtime.context.render import render_dag_messages
        from openprogram.state.store.session.session_node_writer import SessionNodeWriter

        from openprogram.runtime.context.model import Context
        db = Context.turn_store(session_id)
        shim = SessionNodeWriter(db, session_id)
        graph = shim.load()

        branch = (db.get_branch(session_id, head_id) if head_id else db.get_branch(session_id)) or history or []
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

        active_context = Context.current()
        if active_context is not None:
            read_ids = active_context.select_history(graph, read_ids)

        # Drop abandoned assistant turns. A stream that died mid-flight
        # (crashed worker, dropped connection) leaves an llm node with
        # empty output sitting IN the branch, whether it still says
        # "running" or the owner stopped it afterwards, and rendering it
        # feeds the model a contentless assistant message between two user
        # messages — some providers reject that outright, and it teaches
        # the model that empty replies are acceptable.
        # A node that owns tool calls is kept regardless: its ToolCall
        # entries have to hang off an assistant message or the following
        # tool_result is orphaned. An attach node is kept too: it is a
        # sub-agent's report, which says so even with no text of its own.
        owns_tool_call = {
            (graph.nodes[nid].caller or "")
            for nid in read_ids
            if nid in graph.nodes and graph.nodes[nid].is_code()
        }

        def _is_abandoned(nid: str) -> bool:
            n = graph.nodes.get(nid)
            if n is None or not n.is_llm() or (n.metadata or {}).get("function") == "attach":
                return False
            return not (n.output or "").strip() and nid not in owns_tool_call

        read_ids = [nid for nid in read_ids if not _is_abandoned(nid)]

        omitted = False
        if tail_tokens is not None:
            from openprogram.runtime.context.tail import bounded_tail
            read_ids, omitted = bounded_tail(graph, read_ids, tail_tokens)

        session_directory = None
        try:
            _sdir_fn = getattr(db, "_session_dir", None)
            if _sdir_fn:
                session_directory = str(_sdir_fn(session_id))
        except Exception:
            session_directory = None

        messages = render_dag_messages(graph, read_ids, session_directory,
                                       reasoning_for=model, audience=audience)
        if shown is not None:
            from openprogram.runtime.context.turn_context import last_sections
            shown["section_notes"] = last_sections(graph, read_ids, audience)
            from openprogram.runtime.context.turn_context import turn_view
            shown["turn_view"] = turn_view(graph, read_ids)
        if omitted:
            messages = self._with_omitted_note(graph, read_ids, messages)
        return messages

    @staticmethod
    def _with_omitted_note(graph: Any, read_ids: list[str], messages: list) -> list:
        """Say that older messages were left out, right before the kept tail.

        A summary renders first as one assistant message, so the note goes
        after it. Being a user message, it also keeps a tail that starts on
        a reply from opening the request with an assistant message.
        """
        import time

        from openprogram.models.providers.types import TextContent, UserMessage
        from openprogram.runtime.context.nodes import summary_covers_ids
        from openprogram.runtime.context.tail import OMITTED_NOTE

        first = read_ids[0] if read_ids else None
        position = 1 if (first in graph.nodes
                         and summary_covers_ids(graph.nodes[first]) is not None) else 0
        note = UserMessage(
            role="user",
            content=[TextContent(type="text", text=OMITTED_NOTE)],
            timestamp=int(time.time() * 1000),
        )
        return [*messages[:position], note, *messages[position:]]

    def _estimate(self, history: list[dict]) -> int:
        from openprogram.runtime.context.tokens import estimate_history_tokens
        return estimate_history_tokens(history)

    def _occupancy_tokens(self, session_id: str, history: list[dict],
                          *, head_id: Optional[str] = None) -> int:
        """Same total the ring and /context panel use for the chain ending
        at ``head_id`` (rendered view)."""
        try:
            from openprogram.runtime.context.session_stats import estimate_total_used
            total, _ = estimate_total_used(session_id, head_id)
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
        trigger: Optional[str] = None,
    ) -> None:
        if not on_event:
            return
        on_event({"type": "chat_response", "data": {
            "type": "compaction_failed" if result.error else "compaction_finished",
            "error": result.error,
            "session_id": session_id,
            "user_initiated": user_initiated,
            "trigger": trigger,
            # A stop is not a failure; the surfaces word it differently.
            "cancelled": result.error == _CANCELLED,
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
            from openprogram.state.config import read_config
            requested = (read_config().get("context") or {}).get("engine")
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
