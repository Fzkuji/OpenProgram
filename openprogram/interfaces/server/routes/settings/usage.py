"""Token-usage aggregation routes — backed by UsageLedger (SQLite WAL).

Endpoints:
  GET /api/usage/summary          overall totals + per-model breakdown
  GET /api/usage/trend?bucket=day  time-series (day or hour buckets)
  GET /api/usage/by-kind           per call_kind breakdown
  GET /api/usage/harnesses?days=30   Claude Code and Codex sessions on this
                                   computer (models/usage/harnesses)
  GET /api/usage/tuning?days=30    the compaction share and cache lifetime
                                   that would have cost the user's own
                                   calls the least (runtime/context/token_accounting/tuning.py)

All queries support ``since`` / ``until`` epoch-seconds query params.

Cost is split the way it is billed: ``billed_cost`` is what pay-per-token
providers charge (priced calls only), ``included_cost`` is the API list
price of calls a subscription already covers (shown as "Included", the
amount only in a tooltip), and ``unpriced_events`` counts pay-per-token
calls with no known price, so one unpriced call never hides the total.
"""
from __future__ import annotations

from fastapi import Query as QParam
from fastapi.responses import JSONResponse


def _ledger():
    from openprogram.models.usage.ledger import default_ledger
    return default_ledger


def _included(provider: str) -> bool:
    from openprogram.models.listing.provider_status import is_subscription
    return is_subscription(provider or "")


def _split_cost(rows) -> dict:
    """``billed_cost``, ``included_cost``, ``unpriced_events`` of rows grouped by provider."""
    billed = included = 0.0
    unpriced = 0
    for r in rows:
        if _included(r.keys.get("provider") or ""):
            included += r.cost_total
        else:
            billed += r.cost_total
            unpriced += r.unknown_cost_events
    return {"billed_cost": round(billed, 6), "included_cost": round(included, 6), "unpriced_events": unpriced}


def _split_by(rows, keys: tuple[str, ...]) -> dict[tuple, dict]:
    """The cost split of rows also grouped by provider, keyed by ``keys``."""
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault(tuple(r.keys.get(k) or "" for k in keys), []).append(r)
    return {key: _split_cost(group) for key, group in groups.items()}


def register(app):
    # Sync (``def``): the replay reads a month of calls; FastAPI runs it in
    # its threadpool instead of on the event loop.
    @app.get("/api/usage/tuning")
    def api_usage_tuning(days: float = QParam(30.0)):
        if not 1 <= days <= 90:
            return JSONResponse(content={"error": "days must be in [1, 90]"}, status_code=400)
        from openprogram.runtime.context.token_accounting.tuning import tune
        return JSONResponse(content=tune(days=days, ledger=_ledger()).to_dict())

    # Sync: a scan of what Claude Code or Codex appended runs first, in
    # FastAPI's threadpool (docs/reference/design/integrations/third-party-harnesses.html).
    @app.get("/api/usage/harnesses")
    def api_usage_harnesses(
        days: int = QParam(30),
        since: float | None = QParam(None),
        until: float | None = QParam(None),
    ):
        from openprogram.models.usage.harnesses import report
        days = max(1, min(int(days or 30), 92))
        return JSONResponse(content=report(since=since, until=until, days=days))

    @app.get("/api/usage/summary")
    async def api_usage_summary(
        since: float | None = QParam(None),
        until: float | None = QParam(None),
    ):
        lg = _ledger()
        kw = dict(since=since, until=until)

        by_model = lg.query(group_by=["model_id", "provider"], **kw)
        totals = lg.query(**kw)
        by_kind = lg.query(group_by=["call_kind", "call_label"], **kw)
        by_label = lg.query(group_by=["call_label"], **kw)
        # Source and function rows split their cost as the totals do, so a
        # subscription's calls read "Included" there too.
        kind_split = _split_by(lg.query(group_by=["call_kind", "call_label", "provider"], **kw), ("call_kind", "call_label"))
        label_split = _split_by(lg.query(group_by=["call_label", "provider"], **kw), ("call_label",))

        tot = totals[0] if totals else None
        split = _split_cost(by_model)
        rows = []
        for r in by_model:
            rows.append({
                "included": _included(r.keys.get("provider") or ""),
                "unpriced_events": r.unknown_cost_events,
                "model": r.keys.get("model_id") or "",
                "provider": r.keys.get("provider") or "",
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cache_read_tokens": r.cache_read_tokens,
                "cache_write_tokens": r.cache_write_tokens,
                "total_tokens": r.total_tokens,
                "cost": r.cost_total,
                "events": r.events,
            })
        rows.sort(key=lambda r: r["total_tokens"], reverse=True)

        kind_rows = []
        for r in by_kind:
            kind = r.keys.get("call_kind") or "direct"
            label = r.keys.get("call_label") or ""
            display = f"{kind}:{label}" if label else kind
            kind_rows.append({
                "kind": display,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "total_tokens": r.total_tokens,
                "cost": r.cost_total,
                "events": r.events,
                **kind_split.get((r.keys.get("call_kind") or "", label), {}),
            })
        kind_rows.sort(key=lambda r: r["total_tokens"], reverse=True)

        label_rows = []
        for r in by_label:
            label_rows.append({
                "label": r.keys.get("call_label") or "",
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "total_tokens": r.total_tokens,
                "cost": r.cost_total,
                "events": r.events,
                **label_split.get((r.keys.get("call_label") or "",), {}),
            })
        label_rows.sort(key=lambda r: r["total_tokens"], reverse=True)

        return JSONResponse(content={
            "totals": {
                "input_tokens": tot.input_tokens if tot else 0,
                "output_tokens": tot.output_tokens if tot else 0,
                "cache_read_tokens": tot.cache_read_tokens if tot else 0,
                "cache_write_tokens": tot.cache_write_tokens if tot else 0,
                "total_tokens": tot.total_tokens if tot else 0,
                "cost": tot.cost_total if tot else 0.0,
                "cost_known": tot.cost_known if tot else True,
                "unknown_cost_events": tot.unknown_cost_events if tot else 0,
                "events": tot.events if tot else 0,
                **split,
            },
            "by_model": rows,
            "by_kind": kind_rows,
            "by_label": label_rows,
        })

    @app.get("/api/usage/trend")
    async def api_usage_trend(
        bucket: str = QParam("day"),
        days: int = QParam(30),
        group: str = QParam(""),
        since: float | None = QParam(None),
        until: float | None = QParam(None),
    ):
        """Time-series usage with optional grouping dimension.

        ``group`` can be ``model_id``, ``call_kind``, or ``call_label`` —
        the response then contains per-category series so the chart can
        colour-code stacked bars.  Without ``group`` a single total series
        is returned (backward-compatible).

        Every bucket in the contiguous window is filled (zeros where no
        data), so the chart always shows a full date axis.
        """
        import time as _time

        if bucket not in ("day", "hour"):
            bucket = "day"
        bucket_secs = 86400 if bucket == "day" else 3600
        days = max(1, min(int(days or 30), 366))

        VALID_GROUPS = {"model_id", "call_kind", "call_label"}
        group = group if group in VALID_GROUPS else ""

        now = _time.time()
        now_bucket = int(now // bucket_secs)
        start_bucket = now_bucket - (days - 1)
        win_since = since if since is not None else start_bucket * bucket_secs
        win_until = until if until is not None else (now_bucket + 1) * bucket_secs

        lg = _ledger()
        group_by = [bucket] + ([group] if group else [])
        rows = lg.query(group_by=group_by, since=win_since, until=win_until)
        # What pay-per-token providers charged per bucket (and category):
        # subscription calls and unpriced ones count nothing here.
        billed: dict[tuple, float] = {}
        for r in lg.query(group_by=group_by + ["provider"], since=win_since, until=win_until):
            if _included(r.keys.get("provider") or ""):
                continue
            key = (int(r.keys.get(bucket) or 0), r.keys.get(group) or "" if group else "")
            billed[key] = billed.get(key, 0.0) + r.cost_total

        lo = int(win_since // bucket_secs)
        hi = int((win_until - 1) // bucket_secs)

        if not group:
            by_bucket = {int(r.keys.get(bucket) or 0): r for r in rows}
            trend = []
            for b in range(lo, hi + 1):
                r = by_bucket.get(b)
                trend.append({
                    "ts": b * bucket_secs,
                    "input_tokens": r.input_tokens if r else 0,
                    "output_tokens": r.output_tokens if r else 0,
                    "cache_read_tokens": r.cache_read_tokens if r else 0,
                    "total_tokens": r.total_tokens if r else 0,
                    "cost": r.cost_total if r else 0.0,
                    "billed_cost": round(billed.get((b, ""), 0.0), 6),
                    "events": r.events if r else 0,
                })
            return JSONResponse(content={
                "bucket": bucket, "days": days, "group": "", "trend": trend,
            })

        # Grouped: build {category: {bucket_idx: row}} then emit per-category series.
        cats: dict[str, dict[int, object]] = {}
        for r in rows:
            cat = r.keys.get(group) or ""
            b_idx = int(r.keys.get(bucket) or 0)
            cats.setdefault(cat, {})[b_idx] = r

        series = {}
        for cat, bmap in sorted(cats.items(), key=lambda kv: -sum(
                (getattr(r, "total_tokens", 0) for r in kv[1].values()))):
            pts = []
            for b in range(lo, hi + 1):
                r = bmap.get(b)
                pts.append({
                    "ts": b * bucket_secs,
                    "total_tokens": r.total_tokens if r else 0,
                    "cost": r.cost_total if r else 0.0,
                    "billed_cost": round(billed.get((b, cat), 0.0), 6),
                    "events": r.events if r else 0,
                })
            series[cat] = pts

        return JSONResponse(content={
            "bucket": bucket, "days": days, "group": group, "series": series,
        })
