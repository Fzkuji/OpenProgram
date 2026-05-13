"""ContextEngine — the per-turn orchestrator.

Lifecycle, per turn:

    1. ``prepare(agent, session, history, model)`` runs *before* the LLM
       call. Applies tool-result aging in memory, computes a token
       budget, builds the final ``messages`` list + system prompt.
       Returns ``TurnPrep``.

    2. Caller inspects ``TurnPrep.budget_pct`` and calls
       ``engine.should_auto_compact(prep)`` to decide whether to
       summarise BEFORE issuing the LLM request. Auto-compact is
       transparent: it writes a synthetic summary into SessionDB so
       the next prepare() picks up the shorter branch.

    3. After the turn completes, ``record_turn(prep, usage)`` lets the
       engine update telemetry (recommended compaction events,
       running token estimates).

Plugin contract — anyone can subclass ``ContextEngine`` and register a
custom policy. The default impl encodes our standard three-tier
strategy (aging → auto-compact → manual). To swap policies per agent,
set ``agent.context_engine = "<engine_name>"`` and add the impl to
``CONTEXT_ENGINE_REGISTRY``.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from openprogram.context.aging import age_tool_results
from openprogram.context.tokens import (
    estimate_history_tokens,
    estimate_message_tokens,
    real_context_window,
)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TurnPrep:
    """What ``prepare()`` returns — the LLM-ready context for one turn.

    Fields:
        system_prompt:    Final system-prompt string after layered build.
        agent_messages:   ``UserMessage``/``AssistantMessage`` list, the
                          structured shape AgentContext expects.
        history_dicts:    The same content as ``agent_messages`` but in
                          raw SessionDB dict shape — kept around so the
                          caller can persist mutations (e.g. aged tool
                          results) back to disk if desired.
        estimated_tokens: Conservative estimate of tokens we're sending.
        context_window:   Model's true max input window (NOT max_tokens).
        budget_pct:       ``estimated_tokens / context_window`` clamped 0-1.
        tool_results_redacted: How many tool-result blocks got aged out.
        tokens_freed_by_aging: How many tokens that saved.
        summary_id:       SessionDB id of the summary node, if one is in
                          play for this branch.
    """
    system_prompt: str
    agent_messages: list[Any] = field(default_factory=list)
    history_dicts: list[dict] = field(default_factory=list)
    estimated_tokens: int = 0
    context_window: int = 0
    budget_pct: float = 0.0
    tool_results_redacted: int = 0
    tokens_freed_by_aging: int = 0
    summary_id: Optional[str] = None


@dataclass
class CompactResult:
    """Outcome of an LLM summarisation pass."""
    summary_text: str
    summary_id: Optional[str]      # SessionDB row id, when persisted
    summarised_count: int          # how many old messages folded
    summarised_tokens: int         # token weight that got freed
    previous_summary_used: bool


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

EventCallback = Callable[[dict], None]


class ContextEngine:
    """Override these four methods to implement a custom policy."""

    name: str = "abstract"

    def prepare(self, *, agent, session, history, model) -> TurnPrep:
        raise NotImplementedError

    def should_auto_compact(self, prep: TurnPrep) -> bool:
        raise NotImplementedError

    def should_recommend(self, prep: TurnPrep) -> bool:
        """Cheaper threshold — UI shows the user a 'context filling up'
        toast but we don't actually summarise yet."""
        raise NotImplementedError

    async def compact(self, *, agent, session_id, model,
                      on_event: Optional[EventCallback] = None,
                      previous_summary: Optional[str] = None,
                      user_initiated: bool = False) -> CompactResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Default implementation — three-tier compaction
# ---------------------------------------------------------------------------

class DefaultContextEngine(ContextEngine):
    """Standard policy:

    Tier 1 — tool-result aging (cheap, every turn). Stale tool outputs
        get replaced with a short stub. Preserves turn structure so
        prompt cache only invalidates around the redaction boundary.

    Tier 2 — auto-compact threshold. Once estimated input crosses
        ``AUTO_COMPACT_PCT`` of the context window, we invoke LLM
        summarisation INLINE before issuing the next request. The
        summary persists as a SessionDB row so subsequent prepare()
        calls just see the shorter branch.

    Tier 3 — manual ``/compact``. User-initiated full compaction, same
        underlying summariser but bypasses the threshold check.
    """

    name = "default"

    # Recommend at 70% (UI shows a toast); auto-compact at 85% (we act).
    # Both compare estimated input tokens against the real context window.
    RECOMMEND_PCT = 0.70
    AUTO_COMPACT_PCT = 0.85

    # Tail to keep verbatim when the summariser runs.
    KEEP_RECENT_TOKENS = 20_000

    # ---- prepare ----------------------------------------------------------

    def prepare(self, *, agent, session, history, model) -> TurnPrep:
        # 1. Apply tool-result aging in memory.
        aged_history, n_redacted, tokens_freed = age_tool_results(history)

        # 2. Build the structured messages list.
        agent_messages = _to_agent_messages(aged_history)

        # 3. System prompt — uses the existing builder if available.
        system_prompt = _build_system_prompt(agent)

        # 4. Token estimate + budget pct.
        tokens = estimate_history_tokens(aged_history)
        # System prompt is sent every turn too — count it toward the budget.
        tokens += len(system_prompt) // 4  # rough: 4 chars/token
        window = real_context_window(model)
        pct = (tokens / window) if window else 0.0

        # 5. Look up the active summary id, if one's stamped on the
        #    session (the dispatcher trigger_compaction writes one).
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
            estimated_tokens=tokens,
            context_window=window,
            budget_pct=pct,
            tool_results_redacted=n_redacted,
            tokens_freed_by_aging=tokens_freed,
            summary_id=summary_id,
        )

    # ---- thresholds -------------------------------------------------------

    def should_recommend(self, prep: TurnPrep) -> bool:
        return prep.budget_pct >= self.RECOMMEND_PCT

    def should_auto_compact(self, prep: TurnPrep) -> bool:
        return prep.budget_pct >= self.AUTO_COMPACT_PCT

    # ---- compact ----------------------------------------------------------

    async def compact(self, *, agent, session_id, model,
                      on_event: Optional[EventCallback] = None,
                      previous_summary: Optional[str] = None,
                      user_initiated: bool = False) -> CompactResult:
        """Run summarisation and persist the result.

        Returns the summary text + the new SessionDB row id.

        Auto-compact path: caller passes ``user_initiated=False``. We
        still emit a ``compaction_started`` / ``compaction_finished``
        envelope so any UI tailing the session can show a status line
        while the summariser runs.
        """
        from openprogram.agent.session_db import default_db
        from openprogram.context.summarize import summarise_prefix

        db = default_db()
        sess = db.get_session(session_id) or {}
        history = db.get_branch(session_id) or []
        if len(history) < 4:
            return CompactResult(
                summary_text="",
                summary_id=None,
                summarised_count=0,
                summarised_tokens=0,
                previous_summary_used=False,
            )

        if on_event:
            on_event({
                "type": "chat_response",
                "data": {
                    "type": "compaction_started",
                    "session_id": session_id,
                    "user_initiated": user_initiated,
                },
            })

        # Inherit previous summary if not supplied — incremental
        # summary chain.
        if previous_summary is None:
            extra_meta = sess.get("extra_meta") or {}
            previous_summary = extra_meta.get("_last_summary_text")

        summary = await summarise_prefix(
            messages=history,
            model=model,
            keep_recent_tokens=self.KEEP_RECENT_TOKENS,
            previous_summary=previous_summary,
        )

        summary_id = None
        if summary.summary_text:
            summary_id = _persist_summary_node(
                db, session_id, summary.summary_text,
                cut_idx=summary.cut_idx, history=history,
            )

        # Stamp session meta so the next prepare() knows we have an
        # active summary AND so the next compaction can chain off it.
        if summary_id:
            try:
                db.update_session(
                    session_id,
                    _last_summary_id=summary_id,
                    _last_summary_text=summary.summary_text,
                    _last_compacted_at=__import__("time").time(),
                )
            except Exception:
                pass

        if on_event:
            on_event({
                "type": "chat_response",
                "data": {
                    "type": "compaction_finished",
                    "session_id": session_id,
                    "summary_id": summary_id,
                    "summarised_count": summary.summarised_count,
                    "summarised_tokens": summary.summarised_tokens,
                    "previous_summary_used": summary.previous_summary_used,
                    "user_initiated": user_initiated,
                },
            })

        return CompactResult(
            summary_text=summary.summary_text,
            summary_id=summary_id,
            summarised_count=summary.summarised_count,
            summarised_tokens=summary.summarised_tokens,
            previous_summary_used=summary.previous_summary_used,
        )


# ---------------------------------------------------------------------------
# Helpers used by DefaultContextEngine
# ---------------------------------------------------------------------------

def _to_agent_messages(history: list[dict]) -> list:
    """Turn SessionDB dict rows into structured AgentMessage objects.

    Same shape as the dispatcher's legacy ``_history_to_agent_messages``
    but lives here so context decisions are made in one place.
    """
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


def _build_system_prompt(agent: Any) -> str:
    """Defer to the legacy engine's system-prompt builder."""
    try:
        from openprogram.agents.context_engine import ContextEngine as _Legacy
        return _Legacy().build_system_prompt(agent)
    except Exception:
        return getattr(agent, "system_prompt", "") or ""


def _persist_summary_node(db, session_id: str, summary_text: str,
                          *, cut_idx: int, history: list[dict]) -> str:
    """Insert a synthetic ``compactionSummary`` row into SessionDB.

    The summary becomes a new message anchored at the same parent the
    pre-cut prefix was hanging off, and the kept tail gets re-parented
    onto it. Mirrors the original ``trigger_compaction`` flow but
    centralised here so all compaction paths produce the same DB shape.
    """
    import time
    import uuid
    summary_id = "summary_" + uuid.uuid4().hex[:10]

    # Place the summary just before ``history[cut_idx]``. Parent it to
    # whatever was the parent of history[cut_idx] (or None if cut_idx==0).
    parent_id = None
    if cut_idx > 0 and cut_idx < len(history):
        parent_id = history[cut_idx].get("parent_id")
    elif history:
        parent_id = history[-1].get("parent_id")

    row = {
        "id": summary_id,
        "role": "system",
        "content": f"[Previous conversation summary]\n{summary_text}",
        "parent_id": parent_id,
        "timestamp": time.time(),
        "type": "compactionSummary",
        "extra": '{"compaction": true}',
    }
    try:
        db.append_message(session_id, row)
        # Re-parent the kept tail so the active branch flows
        # parent → summary → first-kept → ... → leaf
        prev = summary_id
        for tail_msg in history[cut_idx:]:
            new_id = "k_" + uuid.uuid4().hex[:10]
            tail_copy = dict(tail_msg)
            tail_copy["id"] = new_id
            tail_copy["parent_id"] = prev
            db.append_message(session_id, tail_copy)
            prev = new_id
        # Advance head to the new leaf.
        db.set_head(session_id, prev)
    except Exception:
        # Best-effort: if DB write fails, callers still get the summary
        # text in the in-memory result.
        return ""
    return summary_id


# ---------------------------------------------------------------------------
# Plugin registry + module singleton
# ---------------------------------------------------------------------------

CONTEXT_ENGINE_REGISTRY: dict[str, ContextEngine] = {}


def register_engine(engine: ContextEngine) -> ContextEngine:
    CONTEXT_ENGINE_REGISTRY[engine.name] = engine
    return engine


def get_engine(name: str | None = None) -> ContextEngine:
    """Look up an engine by name, falling back to the default."""
    if name and name in CONTEXT_ENGINE_REGISTRY:
        return CONTEXT_ENGINE_REGISTRY[name]
    return default_engine


# Module-level default — register self.
default_engine: ContextEngine = DefaultContextEngine()
register_engine(default_engine)
