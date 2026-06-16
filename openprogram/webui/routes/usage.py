"""Token-usage aggregation route — read-only summary across all sessions.

No new instrumentation: token data is already recorded at two layers
(see ``openprogram/context/usage.py`` and the per-message history files),
this route only aggregates what's on disk.

Two views, both returned by ``GET /api/usage/summary``:

  * ``totals`` — grand totals (input / output / cache / cost / sessions /
    turns). The authoritative cumulative figures come from each session's
    ``extra_meta._usage`` ledger (UsageTracker), which has no model
    dimension.
  * ``by_model`` — per-model breakdown built from the per-message history
    (each assistant message carries ``token_model`` + token counts), so
    the user can see which model burned what. Cost is derived from the
    model catalog's per-MTok pricing where the model is known.

Sessions where the provider never reported usage (e.g. some OpenAI-compat
backends) simply contribute zero — they're still counted in ``sessions``.
"""
from __future__ import annotations

import json

from fastapi.responses import JSONResponse


def _usage_meta(session: dict) -> dict:
    """Pull the UsageTracker ledger out of a session row, tolerating both
    the dict and JSON-string storage shapes."""
    raw = (session.get("extra_meta") or {}).get("_usage")
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return dict(raw)


def _model_cost(model_id: str, inp: int, out: int, cr: int, cw: int) -> float | None:
    """USD cost for the given token split, using the catalog's per-MTok
    pricing. Returns None when the model isn't in the catalog (unknown
    pricing — better to show nothing than a wrong $0)."""
    if not model_id:
        return None
    try:
        from openprogram.providers.models_generated import MODELS
    except Exception:
        return None
    m = MODELS.get(model_id)
    if m is None:
        # token_model is a bare id (no provider prefix); scan for a match.
        m = next((x for x in MODELS.values() if x.id == model_id), None)
    if m is None or getattr(m, "cost", None) is None:
        return None
    c = m.cost
    return (
        inp / 1_000_000 * (c.input or 0)
        + out / 1_000_000 * (c.output or 0)
        + cr / 1_000_000 * (c.cache_read or 0)
        + cw / 1_000_000 * (c.cache_write or 0)
    )


def _provider_of(model_id: str) -> str:
    """Best-effort provider name for a bare token_model id."""
    if not model_id:
        return "unknown"
    if ":" in model_id:  # already provider-prefixed (e.g. "minimax-cn:M3")
        return model_id.split(":", 1)[0]
    try:
        from openprogram.providers.models_generated import MODELS
        m = next((x for x in MODELS.values() if x.id == model_id), None)
        if m is not None:
            return m.provider
    except Exception:
        pass
    return "unknown"


def register(app):
    @app.get("/api/usage/summary")
    async def api_usage_summary():
        """Aggregate token usage across every session on disk."""
        from openprogram.agent.session_db import default_db

        db = default_db()
        try:
            sessions = db.list_sessions(limit=10**9)
        except Exception:
            sessions = []

        # turn_count comes from the authoritative per-session ledger; the
        # token totals are summed from the per-message history below so they
        # always match the by_model breakdown (the message layer is the only
        # one carrying the model dimension, and some sessions have a ledger
        # but no per-message counts, or vice-versa — summing one source keeps
        # totals and breakdown consistent).
        tot_turns = 0
        sessions_with_usage = 0
        for s in sessions:
            u = _usage_meta(s)
            tot_turns += int(u.get("turn_count") or 0)

        # Per-model breakdown from the message history.
        by_model: dict[str, dict] = {}
        for s in sessions:
            try:
                msgs = db.get_messages(s["id"])
            except Exception:
                continue
            session_counted = False
            for m in msgs:
                if m.get("role") != "assistant":
                    continue
                mid = m.get("token_model")
                if not mid:
                    continue
                inp = int(m.get("input_tokens") or 0)
                out = int(m.get("output_tokens") or 0)
                cr = int(m.get("cache_read_tokens") or 0)
                cw = int(m.get("cache_write_tokens") or 0)
                if not (inp or out or cr or cw):
                    continue
                if not session_counted:
                    sessions_with_usage += 1
                    session_counted = True
                bucket = by_model.setdefault(mid, {
                    "model": mid,
                    "provider": _provider_of(mid),
                    "input_tokens": 0, "output_tokens": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0,
                    "messages": 0,
                })
                bucket["input_tokens"] += inp
                bucket["output_tokens"] += out
                bucket["cache_read_tokens"] += cr
                bucket["cache_write_tokens"] += cw
                bucket["messages"] += 1

        tot_in = sum(b["input_tokens"] for b in by_model.values())
        tot_out = sum(b["output_tokens"] for b in by_model.values())
        tot_cr = sum(b["cache_read_tokens"] for b in by_model.values())

        rows = []
        total_cost = 0.0
        any_cost = False
        for b in by_model.values():
            cost = _model_cost(
                b["model"], b["input_tokens"], b["output_tokens"],
                b["cache_read_tokens"], b["cache_write_tokens"],
            )
            if cost is not None:
                any_cost = True
                total_cost += cost
            b["cost"] = cost
            rows.append(b)
        rows.sort(
            key=lambda r: r["input_tokens"] + r["output_tokens"],
            reverse=True,
        )

        return JSONResponse(content={
            "totals": {
                "input_tokens": tot_in,
                "output_tokens": tot_out,
                "cache_read_tokens": tot_cr,
                "total_tokens": tot_in + tot_out,
                "turns": tot_turns,
                "sessions": len(sessions),
                "sessions_with_usage": sessions_with_usage,
                "cost": total_cost if any_cost else None,
            },
            "by_model": rows,
        })
