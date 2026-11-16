"""UsageTracker — running ledger of real provider-reported token usage.

Hermes' biggest win: feed every API response's usage dict back to the
engine so future budgets are based on **truth** rather than tiktoken
guesses (which are off by 10-30% on long Anthropic conversations and
~50% on heavily-toolcalling sessions).

Two storage layers:

* In-memory cache keyed by session_id — hot path. Reads on the prepare
  hot path stay sub-microsecond.
* SessionDB ``extra_meta`` JSON column — durable. Survives worker
  restarts so a long-running conversation's compaction state persists
  across crashes.

The ``last_*`` occupancy figures belong to a branch: each branch of a
conversation reads its own chain, so a turn on one branch never budgets
against another branch's request. The persisted record keeps every
branch's figures under ``branches`` and, at its top level, the selected
branch's (``branch_id``) as the projection the context ring reads; the
cumulative billing totals stay per conversation. The branch a call means
is the one it names, else the branch the current turn runs on (the usage
context's ``branch_id``), else the projection.

Tracker also handles prompt-cache observation: every Anthropic response
carries ``cache_read_input_tokens`` separately from ``input_tokens`` —
we track the split so an engine can tell "we're sending 100K tokens but
99K of it is cached" (don't compact) vs "100K all cold" (compact now).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from threading import Lock
from typing import Any, Optional

from openprogram.runtime.context.types import UsageState

_log = logging.getLogger(__name__)

_LAST_FIELDS = ("last_prompt_tokens", "last_completion_tokens",
                "last_cache_read_tokens", "last_cache_write_tokens")


class UsageTracker:
    """Thread-safe in-memory state store, backed by SessionDB.

    One instance per process. Get/set methods are cheap; the persist
    sidecar writes to ``session.extra_meta._usage`` opportunistically.

    The ``storage_key`` controls which extra_meta field the sidecar
    reads/writes. The default engine uses ``_usage``. Custom engines
    that want isolated state pass their own key (e.g. ``_usage_retrieval``)
    when constructing the tracker.
    """

    def __init__(self, *, storage_key: str = "_usage") -> None:
        # The conversation's state: its billing totals and the projected
        # branch's ``last_*`` figures.
        self._cache: dict[str, UsageState] = {}
        # Which branch the projection's ``last_*`` belong to (None: a record
        # written before branches were kept, read as the selected branch's).
        self._projection: dict[str, Optional[str]] = {}
        # Each branch's own ``last_*`` figures, by conversation.
        self._branches: dict[str, dict[str, dict[str, Any]]] = {}
        self._lock = Lock()
        self.storage_key = storage_key

    # ---- Read ----------------------------------------------------------

    def _session_state(self, session_id: str) -> UsageState:
        with self._lock:
            cached = self._cache.get(session_id)
            if cached is not None:
                return cached
        state = self._load_from_db(session_id)
        with self._lock:
            self._cache.setdefault(session_id, state)
            return self._cache[session_id]

    @staticmethod
    def _branch_for(session_id: str, branch_id: Optional[str]) -> Optional[str]:
        if branch_id is not None:
            return branch_id
        try:
            from openprogram.models.usage.context import current_usage_context
            context = current_usage_context()
        except Exception:
            return None
        if context.session_id not in (None, session_id):
            return None
        return context.branch_id

    def get(self, session_id: str, branch_id: Optional[str] = None) -> UsageState:
        """Return the latest state for ``session_id`` as ``branch_id`` reads
        it — load from DB on a cache miss.

        The billing totals are the conversation's; the ``last_*`` figures are
        the branch's own. A branch with no figures of its own reads none,
        unless it is the projected branch (or the record predates branches).
        """
        state = self._session_state(session_id)
        branch = self._branch_for(session_id, branch_id)
        if branch is None:
            return state
        with self._lock:
            own = (self._branches.get(session_id) or {}).get(branch)
            projected = self._projection.get(session_id)
        if own is not None:
            return replace(state, **{key: int(own.get(key) or 0) for key in _LAST_FIELDS},
                           last_updated_at=float(own.get("last_updated_at") or 0),
                           source=own.get("source") or state.source)
        if projected is None or projected == branch:
            return state
        return replace(state, **{key: 0 for key in _LAST_FIELDS}, source="estimate")

    # ---- Write ---------------------------------------------------------

    def record_turn(self, session_id: str, *,
                    usage: dict[str, Any] | None,
                    persist: bool = True,
                    branch_id: Optional[str] = None) -> UsageState:
        """Record one turn's provider-reported usage.

        ``usage`` shape mirrors what the dispatcher gets from
        ``_extract_usage`` — keys we honour: ``input_tokens``,
        ``output_tokens``, ``cache_read_tokens``, ``cache_write_tokens``.
        Missing keys default to 0.

        The ``last_*`` figures go to the turn's branch (``branch_id``, else
        the current turn's); the projection takes them too when that branch
        is the selected one. The billing totals always add up per
        conversation.

        Returns the updated state as the turn's branch reads it, for
        callers that want to immediately check thresholds.
        """
        prev = self._session_state(session_id)
        branch = self._branch_for(session_id, branch_id)
        u = usage or {}
        # Dispatcher totals carry an explicit last-request occupancy. Never
        # substitute their cumulative billing fields when that value is zero.
        aggregated = "context_tokens" in u
        prompt = int(u.get("context_tokens" if aggregated else "input_tokens", 0) or 0)
        new = UsageState(
            last_prompt_tokens=prompt,
            last_completion_tokens=int(u.get("output_tokens", 0) or 0),
            last_cache_read_tokens=0 if aggregated else int(u.get("cache_read_tokens", 0) or 0),
            last_cache_write_tokens=0 if aggregated else int(u.get("cache_write_tokens", 0) or 0),
            cumulative_prompt_tokens=prev.cumulative_prompt_tokens
                + int(u.get("input_tokens", 0) or 0),
            cumulative_completion_tokens=prev.cumulative_completion_tokens
                + int(u.get("output_tokens", 0) or 0),
            cumulative_cache_read_tokens=prev.cumulative_cache_read_tokens
                + int(u.get("cache_read_tokens", 0) or 0),
            turn_count=prev.turn_count + 1,
            compaction_count=prev.compaction_count,
            last_updated_at=time.time(),
            last_compacted_at=prev.last_compacted_at,
            source="provider" if prompt > 0 else "estimate",
        )
        projected = branch is None or branch == self._selected_branch(session_id)
        with self._lock:
            if branch is not None:
                self._branches.setdefault(session_id, {})[branch] = {
                    **{key: getattr(new, key) for key in _LAST_FIELDS},
                    "last_updated_at": new.last_updated_at, "source": new.source,
                }
            if projected:
                self._cache[session_id] = new
                self._projection[session_id] = branch
            else:
                # Another branch's turn: the projection keeps the selected
                # branch's figures; only the totals move.
                self._cache[session_id] = replace(
                    new, **{key: getattr(prev, key) for key in _LAST_FIELDS},
                    source=prev.source)
        if persist:
            self._persist(session_id, self._cache[session_id])
        return self.get(session_id, branch) if branch is not None else new

    @staticmethod
    def _selected_branch(session_id: str) -> Optional[str]:
        try:
            from openprogram.runtime.context.session_db import default_db
            return (default_db().get_session(session_id) or {}).get("active_branch_id")
        except Exception:
            return None

    def record_compaction(self, session_id: str) -> UsageState:
        state = self._session_state(session_id)
        state.compaction_count += 1
        state.last_compacted_at = time.time()
        with self._lock:
            self._cache[session_id] = state
        self._persist(session_id, state)
        return state

    # ---- Estimate fallback --------------------------------------------

    def estimated_input(self, session_id: str,
                        fresh_estimate: int) -> tuple[int, str]:
        """Decide whether to trust a fresh estimate or the last
        provider-reported number.

        Returns ``(best_value, source)``. ``source`` is one of
        ``provider`` / ``estimate`` so the engine can mark it on
        TurnPrep for the UI.

        Logic: if the cached state has a recent
        ``last_prompt_tokens`` (set within the last turn) and the
        fresh estimate isn't drastically larger, trust the provider's
        figure — it's authoritative. If the estimate is much bigger,
        new content was added since the last turn (the typical case
        between turns); blend.
        """
        state = self.get(session_id)
        if state.source != "provider" or state.last_prompt_tokens <= 0:
            return fresh_estimate, "estimate"
        # Heuristic blend: take the provider's number from last turn
        # PLUS the estimated growth between then and now.
        growth = max(0, fresh_estimate - state.last_prompt_tokens)
        return state.last_prompt_tokens + growth, "provider+delta"

    # ---- Lifecycle hooks ----------------------------------------------

    def on_session_end(self, session_id: str) -> None:
        """Flush + drop the in-memory cache slot for a closed session."""
        with self._lock:
            self._cache.pop(session_id, None)
            self._projection.pop(session_id, None)
            self._branches.pop(session_id, None)

    def reset(self, session_id: str) -> None:
        """Wipe everything for this session (used by /reset, clear_sessions)."""
        with self._lock:
            self._cache.pop(session_id, None)
            self._projection.pop(session_id, None)
            self._branches.pop(session_id, None)
        self._persist(session_id, UsageState())

    # ---- Persistence ---------------------------------------------------

    def _load_from_db(self, session_id: str) -> UsageState:
        try:
            from openprogram.runtime.context.session_db import default_db
            db = default_db()
            sess = db.get_session(session_id) or {}
            # Written as a session field (``_persist``); an older row kept
            # it under ``extra_meta``.
            raw = sess.get(self.storage_key) or (sess.get("extra_meta") or {}).get(self.storage_key)
        except Exception:
            raw = None
        if not raw:
            return UsageState()
        try:
            data = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            return UsageState()
        if data.get("context_usage_version") != 2:
            # Old last_* fields contain turn totals. Preserve billing history.
            for key in ("last_prompt_tokens", "last_cache_read_tokens", "last_cache_write_tokens"):
                data[key] = 0
        branches = data.get("branches")
        with self._lock:
            self._projection.setdefault(session_id, data.get("branch_id"))
            if isinstance(branches, dict):
                stored = self._branches.setdefault(session_id, {})
                for key, value in branches.items():
                    if isinstance(value, dict):
                        stored.setdefault(key, {**value, "source": "cached"})
        return UsageState(
            last_prompt_tokens=int(data.get("last_prompt_tokens", 0) or 0),
            last_completion_tokens=int(data.get("last_completion_tokens", 0) or 0),
            last_cache_read_tokens=int(data.get("last_cache_read_tokens", 0) or 0),
            last_cache_write_tokens=int(data.get("last_cache_write_tokens", 0) or 0),
            cumulative_prompt_tokens=int(data.get("cumulative_prompt_tokens", 0) or 0),
            cumulative_completion_tokens=int(data.get("cumulative_completion_tokens", 0) or 0),
            cumulative_cache_read_tokens=int(data.get("cumulative_cache_read_tokens", 0) or 0),
            turn_count=int(data.get("turn_count", 0) or 0),
            compaction_count=int(data.get("compaction_count", 0) or 0),
            last_updated_at=float(data.get("last_updated_at", 0) or 0),
            last_compacted_at=float(data.get("last_compacted_at", 0) or 0),
            source="cached",
        )

    def _persist(self, session_id: str, state: UsageState) -> None:
        try:
            from openprogram.runtime.context.session_db import default_db
            data = {
                "context_usage_version": 2,
                "last_prompt_tokens": state.last_prompt_tokens,
                "last_completion_tokens": state.last_completion_tokens,
                "last_cache_read_tokens": state.last_cache_read_tokens,
                "last_cache_write_tokens": state.last_cache_write_tokens,
                "cumulative_prompt_tokens": state.cumulative_prompt_tokens,
                "cumulative_completion_tokens": state.cumulative_completion_tokens,
                "cumulative_cache_read_tokens": state.cumulative_cache_read_tokens,
                "turn_count": state.turn_count,
                "compaction_count": state.compaction_count,
                "last_updated_at": state.last_updated_at,
                "last_compacted_at": state.last_compacted_at,
            }
            with self._lock:
                projected = self._projection.get(session_id)
                branches = {key: {field: value for field, value in row.items() if field != "source"}
                            for key, row in (self._branches.get(session_id) or {}).items()}
            if projected is not None:
                data["branch_id"] = projected
            if branches:
                data["branches"] = branches
            default_db().update_session(
                session_id,
                **{self.storage_key: json.dumps(data, default=str)},
            )
        except Exception:
            # Persistence is best-effort; the in-memory cache stays correct,
            # so the turn continues on accurate numbers either way.
            _log.debug(
                "usage persistence failed for session %s", session_id,
                exc_info=True,
            )


# Process-wide singleton — like a registry, but with state.
default_tracker = UsageTracker()
