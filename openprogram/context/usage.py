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

Tracker also handles prompt-cache observation: every Anthropic response
carries ``cache_read_input_tokens`` separately from ``input_tokens`` —
we track the split so an engine can tell "we're sending 100K tokens but
99K of it is cached" (don't compact) vs "100K all cold" (compact now).
"""
from __future__ import annotations

import json
import time
from threading import Lock
from typing import Any, Optional

from openprogram.context.types import UsageSnapshot


class UsageTracker:
    """Thread-safe in-memory snapshot store, backed by SessionDB.

    One instance per process. Get/set methods are cheap; the persist
    sidecar writes to ``session.extra_meta._usage`` opportunistically.

    The ``storage_key`` controls which extra_meta field the sidecar
    reads/writes. The default engine uses ``_usage``. Custom engines
    that want isolated state pass their own key (e.g. ``_usage_retrieval``)
    when constructing the tracker.
    """

    def __init__(self, *, storage_key: str = "_usage") -> None:
        self._cache: dict[str, UsageSnapshot] = {}
        self._lock = Lock()
        self.storage_key = storage_key

    # ---- Read ----------------------------------------------------------

    def get(self, session_id: str) -> UsageSnapshot:
        """Return the latest snapshot for ``session_id`` — load from DB
        on a cache miss."""
        with self._lock:
            cached = self._cache.get(session_id)
            if cached is not None:
                return cached
        snap = self._load_from_db(session_id)
        with self._lock:
            self._cache[session_id] = snap
        return snap

    # ---- Write ---------------------------------------------------------

    def record_turn(self, session_id: str, *,
                    usage: dict[str, Any] | None,
                    persist: bool = True) -> UsageSnapshot:
        """Record one turn's provider-reported usage.

        ``usage`` shape mirrors what the dispatcher gets from
        ``_extract_usage`` — keys we honour: ``input_tokens``,
        ``output_tokens``, ``cache_read_tokens``, ``cache_write_tokens``.
        Missing keys default to 0.

        Returns the updated snapshot for callers that want to immediately
        check thresholds.
        """
        prev = self.get(session_id)
        u = usage or {}
        new = UsageSnapshot(
            last_prompt_tokens=int(u.get("input_tokens", 0) or 0),
            last_completion_tokens=int(u.get("output_tokens", 0) or 0),
            last_cache_read_tokens=int(u.get("cache_read_tokens", 0) or 0),
            last_cache_write_tokens=int(u.get("cache_write_tokens", 0) or 0),
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
            source="provider" if usage else "estimate",
        )
        with self._lock:
            self._cache[session_id] = new
        if persist:
            self._persist(session_id, new)
        return new

    def record_compaction(self, session_id: str) -> UsageSnapshot:
        snap = self.get(session_id)
        snap.compaction_count += 1
        snap.last_compacted_at = time.time()
        with self._lock:
            self._cache[session_id] = snap
        self._persist(session_id, snap)
        return snap

    # ---- Estimate fallback --------------------------------------------

    def estimated_input(self, session_id: str,
                        fresh_estimate: int) -> tuple[int, str]:
        """Decide whether to trust a fresh estimate or the last
        provider-reported number.

        Returns ``(best_value, source)``. ``source`` is one of
        ``provider`` / ``estimate`` so the engine can mark it on
        TurnPrep for the UI.

        Logic: if the cached snapshot has a recent
        ``last_prompt_tokens`` (set within the last turn) and the
        fresh estimate isn't drastically larger, trust the provider's
        figure — it's authoritative. If the estimate is much bigger,
        new content was added since the last turn (the typical case
        between turns); blend.
        """
        snap = self.get(session_id)
        if snap.source != "provider" or snap.last_prompt_tokens <= 0:
            return fresh_estimate, "estimate"
        # Heuristic blend: take the provider's number from last turn
        # PLUS the estimated growth between then and now.
        growth = max(0, fresh_estimate - snap.last_prompt_tokens)
        return snap.last_prompt_tokens + growth, "provider+delta"

    # ---- Lifecycle hooks ----------------------------------------------

    def on_session_end(self, session_id: str) -> None:
        """Flush + drop the in-memory cache slot for a closed session."""
        with self._lock:
            self._cache.pop(session_id, None)

    def reset(self, session_id: str) -> None:
        """Wipe everything for this session (used by /reset, clear_sessions)."""
        self._cache.pop(session_id, None)
        self._persist(session_id, UsageSnapshot())

    # ---- Persistence ---------------------------------------------------

    def _load_from_db(self, session_id: str) -> UsageSnapshot:
        try:
            from openprogram.agent.session_db import default_db
            db = default_db()
            sess = db.get_session(session_id) or {}
            raw = (sess.get("extra_meta") or {}).get(self.storage_key)
        except Exception:
            raw = None
        if not raw:
            return UsageSnapshot()
        try:
            data = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            return UsageSnapshot()
        return UsageSnapshot(
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

    def _persist(self, session_id: str, snap: UsageSnapshot) -> None:
        try:
            from openprogram.agent.session_db import default_db
            data = {
                "last_prompt_tokens": snap.last_prompt_tokens,
                "last_completion_tokens": snap.last_completion_tokens,
                "last_cache_read_tokens": snap.last_cache_read_tokens,
                "last_cache_write_tokens": snap.last_cache_write_tokens,
                "cumulative_prompt_tokens": snap.cumulative_prompt_tokens,
                "cumulative_completion_tokens": snap.cumulative_completion_tokens,
                "cumulative_cache_read_tokens": snap.cumulative_cache_read_tokens,
                "turn_count": snap.turn_count,
                "compaction_count": snap.compaction_count,
                "last_updated_at": snap.last_updated_at,
                "last_compacted_at": snap.last_compacted_at,
            }
            default_db().update_session(
                session_id,
                **{self.storage_key: json.dumps(data, default=str)},
            )
        except Exception:
            # Persistence is best-effort; in-memory cache stays correct
            pass


# Process-wide singleton — like a registry, but with state.
default_tracker = UsageTracker()
