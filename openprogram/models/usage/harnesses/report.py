"""What the Third-Party Harnesses page shows.

Calls are grouped in SQL by agent, model, speed and whether the prompt
went past the model's context tier, then priced here, so a catalogue
update reprices history without reading any file again.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any, Iterable, Optional

from .pricing import Price, price

SESSION_LIMIT = 200
_TOKENS = ("input", "cache_read", "cache_write", "cache_write_1h", "output")


class _Sum:
    __slots__ = ("calls", "input", "cache_read", "cache_write", "cache_write_1h", "output",
                 "cost", "unpriced", "models")

    def __init__(self) -> None:
        self.calls = 0
        self.input = self.cache_read = self.cache_write = self.cache_write_1h = self.output = 0
        self.cost = 0.0
        self.unpriced = 0
        self.models: dict[str, int] = {}

    def add(self, row, cost: Optional[float]) -> None:
        self.calls += row["calls"]
        for k in _TOKENS:
            setattr(self, k, getattr(self, k) + (row[k] or 0))
        if cost is None:
            self.unpriced += row["calls"]
        else:
            self.cost += cost
        tokens = (row["input"] or 0) + (row["cache_read"] or 0) + (row["cache_write"] or 0) + (row["output"] or 0)
        self.models[row["model"]] = self.models.get(row["model"], 0) + tokens

    def out(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input,
            "cache_read_tokens": self.cache_read,
            "cache_write_tokens": self.cache_write,
            "output_tokens": self.output,
            "total_tokens": self.input + self.cache_read + self.cache_write + self.output,
            "cost": round(self.cost, 6),
            "unpriced_calls": self.unpriced,
        }


def _prices(conn, since: float, until: float) -> dict[tuple[str, str], Optional[Price]]:
    from . import reader

    out: dict[tuple[str, str], Optional[Price]] = {}
    for r in conn.execute("SELECT DISTINCT agent, model FROM calls WHERE ts >= ? AND ts < ?", (since, until)):
        mod = reader(r["agent"])
        out[(r["agent"], r["model"])] = price(mod.PROVIDER, r["model"]) if mod else None
    return out


def _grouped(conn, prices, since: float, until: float, dims: Iterable[str]) -> list:
    dims = list(dims)
    cases, params = [], []
    for (agent, model), p in prices.items():
        if p is not None and p.tier_above:
            cases.append("WHEN agent = ? AND model = ? THEN (input + cache_read + cache_write) > ?")
            params += [agent, model, p.tier_above]
    over = f"CASE {' '.join(cases)} ELSE 0 END" if cases else "0"
    dim_sql = "".join(f", {d}" for d in dims)
    sql = (f"SELECT agent, model, speed, {over} AS over{dim_sql}, COUNT(*) AS calls, "
           "MIN(ts) AS first, MAX(ts) AS last, "
           + ", ".join(f"SUM({k}) AS {k}" for k in _TOKENS)
           + f" FROM (SELECT *, date(ts, 'unixepoch', 'localtime') AS day FROM calls WHERE ts >= ? AND ts < ?)"
           + f" GROUP BY agent, model, speed, over{dim_sql}")
    return conn.execute(sql, params + [since, until]).fetchall()


def _cost(prices, row) -> Optional[float]:
    p = prices.get((row["agent"], row["model"]))
    if p is None:
        return None
    return p.cost(row["speed"], bool(row["over"]), *(row[k] or 0 for k in _TOKENS))


def _fold(rows, prices, key) -> dict:
    sums: dict = defaultdict(_Sum)
    for row in rows:
        sums[key(row)].add(row, _cost(prices, row))
    return sums


def _quota(agent: str, observed_at: float, data: dict) -> dict[str, Any]:
    windows = []
    for slot in ("primary", "secondary"):
        w = data.get(slot)
        if isinstance(w, dict) and isinstance(w.get("used_percent"), (int, float)):
            windows.append({
                "used_percent": float(w["used_percent"]),
                "window_minutes": w.get("window_minutes"),
                "resets_at": w.get("resets_at"),
            })
    credits = data.get("credits") if isinstance(data.get("credits"), dict) else {}
    balance = credits.get("balance")
    try:
        balance = float(balance) if balance is not None and not credits.get("unlimited") else None
    except (TypeError, ValueError):
        balance = None
    return {"agent": agent, "observed_at": observed_at, "plan": data.get("plan_type"),
            "windows": windows, "credits": balance}


def report(since: Optional[float] = None, until: Optional[float] = None, days: int = 30) -> dict[str, Any]:
    from . import READERS, enabled, refresh, store

    now = time.time()
    if until is None:
        until = now + 1
    if since is None:
        since = until - days * 86400
    on = enabled()
    if on:
        refresh()
    st = store()
    with st.connection() as conn:
        prices = _prices(conn, since, until)
        rows = _grouped(conn, prices, since, until, ["client", "day", "session"])
        agents = _fold(rows, prices, lambda r: r["agent"])
        models = _fold(rows, prices, lambda r: (r["agent"], r["model"]))
        clients = _fold(rows, prices, lambda r: (r["agent"], r["client"] or ""))
        days_rows = _fold(rows, prices, lambda r: (r["day"], r["agent"]))
        session_sums = _fold(rows, prices, lambda r: (r["agent"], r["session"] or ""))
        span: dict[tuple[str, str], list[float]] = {}
        for r in rows:
            k = (r["agent"], r["session"] or "")
            first, last = span.get(k, (r["first"], r["last"]))
            span[k] = [min(first, r["first"]), max(last, r["last"])]
        session_counts: dict[str, int] = defaultdict(int)
        for agent, _sid in span:
            session_counts[agent] += 1
        recent = sorted(span, key=lambda k: span[k][1], reverse=True)[:SESSION_LIMIT]
        meta = {}
        for agent, sid in recent:
            r = conn.execute("SELECT * FROM sessions WHERE agent = ? AND id = ?", (agent, sid)).fetchone()
            if r is not None:
                meta[(agent, sid)] = dict(r)
        quota = [_quota(r["agent"], r["observed_at"], json.loads(r["data"]))
                 for r in conn.execute("SELECT * FROM quota")]
        scanned = st.meta(conn, "scanned_at")

    labels = {r.AGENT: r.LABEL for r in READERS}
    sessions = []
    for agent, sid in recent:
        m = meta.get((agent, sid), {})
        s = session_sums.get((agent, sid)) or _Sum()
        mod = next((r for r in READERS if r.AGENT == agent), None)
        top = sorted(s.models, key=lambda k: s.models[k], reverse=True)
        sessions.append({
            "agent": agent, "id": sid,
            "title": m.get("name") or m.get("first_prompt") or "",
            "named": bool(m.get("name")),
            "first_prompt": m.get("first_prompt") or "",
            "cwd": m.get("cwd") or "", "branch": m.get("branch") or "", "client": m.get("client") or "",
            "models": top, "first_ts": span[(agent, sid)][0], "last_ts": span[(agent, sid)][1],
            "resume": mod.resume_command(sid, m.get("cwd")) if mod else "",
            **s.out(),
        })

    by_day: dict[str, dict[str, Any]] = {}
    for (day, agent), s in days_rows.items():
        by_day.setdefault(day, {})[agent] = {"total_tokens": s.out()["total_tokens"], "cost": round(s.cost, 6)}

    return {
        "enabled": on,
        "since": since, "until": until, "scanned_at": scanned,
        "agents": [
            {"agent": a.AGENT, "label": labels[a.AGENT], "sessions": session_counts.get(a.AGENT, 0),
             **(agents.get(a.AGENT) or _Sum()).out()}
            for a in READERS
        ],
        "by_model": sorted(
            ({"agent": a, "model": m, **s.out()} for (a, m), s in models.items()),
            key=lambda r: r["total_tokens"], reverse=True),
        "by_client": sorted(
            ({"agent": a, "client": c, **s.out()} for (a, c), s in clients.items()),
            key=lambda r: r["total_tokens"], reverse=True),
        "trend": [{"day": d, "agents": by_day[d]} for d in sorted(by_day)],
        "quota": quota,
        "sessions": sessions,
    }
