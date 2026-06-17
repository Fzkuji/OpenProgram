"""Token-usage aggregation routes — backed by UsageLedger (SQLite WAL).

Endpoints:
  GET /api/usage/summary          overall totals + per-model breakdown
  GET /api/usage/trend?bucket=day  time-series (day or hour buckets)
  GET /api/usage/by-kind           per call_kind breakdown

All queries support ``since`` / ``until`` epoch-seconds query params.
"""
from __future__ import annotations

from fastapi import Query as QParam
from fastapi.responses import JSONResponse


def _ledger():
    from openprogram.metering.ledger import default_ledger
    return default_ledger


def register(app):
    @app.get("/api/usage/summary")
    async def api_usage_summary(
        since: float | None = QParam(None),
        until: float | None = QParam(None),
    ):
        lg = _ledger()
        kw = dict(since=since, until=until)

        by_model = lg.query(group_by=["model_id", "provider"], **kw)
        totals = lg.query(**kw)
        by_kind = lg.query(group_by=["call_kind"], **kw)

        tot = totals[0] if totals else None
        rows = []
        for r in by_model:
            rows.append({
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
            kind_rows.append({
                "kind": r.keys.get("call_kind") or "unknown",
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "total_tokens": r.total_tokens,
                "cost": r.cost_total,
                "events": r.events,
            })
        kind_rows.sort(key=lambda r: r["total_tokens"], reverse=True)

        return JSONResponse(content={
            "totals": {
                "input_tokens": tot.input_tokens if tot else 0,
                "output_tokens": tot.output_tokens if tot else 0,
                "cache_read_tokens": tot.cache_read_tokens if tot else 0,
                "cache_write_tokens": tot.cache_write_tokens if tot else 0,
                "total_tokens": tot.total_tokens if tot else 0,
                "cost": tot.cost_total if tot else 0.0,
                "events": tot.events if tot else 0,
            },
            "by_model": rows,
            "by_kind": kind_rows,
        })

    @app.get("/api/usage/trend")
    async def api_usage_trend(
        bucket: str = QParam("day"),
        since: float | None = QParam(None),
        until: float | None = QParam(None),
    ):
        if bucket not in ("day", "hour"):
            bucket = "day"
        lg = _ledger()
        rows = lg.query(group_by=[bucket], since=since, until=until)
        bucket_secs = 86400 if bucket == "day" else 3600
        trend = []
        for r in sorted(rows, key=lambda x: x.keys.get(bucket) or 0):
            ts_bucket = (r.keys.get(bucket) or 0) * bucket_secs
            trend.append({
                "ts": ts_bucket,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cache_read_tokens": r.cache_read_tokens,
                "total_tokens": r.total_tokens,
                "cost": r.cost_total,
                "events": r.events,
            })
        return JSONResponse(content={"bucket": bucket, "trend": trend})
