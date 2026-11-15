"""Routing overview — which session and branch is talking to which model.

``GET /api/routing/overview`` feeds the Overview pane of Settings → LLM
Providers. It joins four sources that already exist:

* the session index — titles, entry point (``source`` / ``channel``) and
  whether a turn is running;
* each visible session's node graph — every branch tip and the model of the
  last reply on it;
* the usage ledger — provider calls in the window, child sessions (rows whose
  ``parent_session_id`` is a visible session), calls no listed session
  accounts for (shown together as background calls) and failover switches;
* the credential store — accounts per provider that are cooling down after a
  rate limit or need attention.

Only sessions that are running or were active inside the window are listed;
the rest are counted. The response never carries credential values.
"""
from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any

from fastapi import Query as QParam
from fastapi.responses import JSONResponse

# A call this recent counts as in flight for a lane whose session index has
# no running flag (child sessions of a program run, for example).
_LIVE_SECONDS = 90
_MAX_SESSIONS = 8
_MAX_BRANCHES = 4

# (session_id, index updated_at) -> (session, branch lanes). Opening a session
# reads its whole node graph from disk, file by file; every read gives up the
# GIL, and on a server whose other threads are busy each one waits to get it
# back, so a cold open costs seconds. The index row's ``updated_at`` moves
# whenever the session does, so an unchanged session is never opened again.
_lane_cache: dict[tuple, tuple[dict | None, list[dict]]] = {}
# Cold sessions open on one background thread; a request waits at most
# _OPEN_BUDGET seconds for them and shows the rest from the index and the
# ledger until they are ready.
_OPEN_BUDGET = 1.5
_opener = ThreadPoolExecutor(max_workers=1, thread_name_prefix="routing-overview")
_opening: dict[tuple, Future] = {}


def _split_model(value: Any) -> tuple[str, str]:
    if isinstance(value, dict):
        return str(value.get("provider") or ""), str(value.get("id") or value.get("model") or "")
    text = str(value or "")
    for sep in (":", "/"):
        if sep in text:
            provider, model = text.split(sep, 1)
            return provider, model
    return "", text


def _branch_lanes(db, session: dict) -> list[dict]:
    """One lane per open branch tip: its name and the model of the last reply
    on it. The longest chain is the trunk, shown as ``main``."""
    sid = session["id"]
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.session_store.shared import _is_spawn_root

    tips = [t for t in db.list_branches(sid) if not t.get("archived")]
    tips.sort(key=lambda t: t.get("updated_at") or 0, reverse=True)
    nodes = SessionNodeWriter(db, sid).load().nodes if tips else {}
    session_provider, session_model = _split_model(session.get("model"))
    lanes = []
    for tip in tips:
        head = tip["head_msg_id"]
        provider = model = ""
        depth, spawned, seen, cursor = 0, False, set(), head
        while cursor and cursor not in seen:
            seen.add(cursor)
            node = nodes.get(cursor)
            if node is None:
                break
            depth += 1
            meta = node.metadata or {}
            if not model and node.is_llm():
                provider = str(meta.get("provider") or "")
                model = str(meta.get("model") or node.name or "")
            if _is_spawn_root(meta):
                spawned = True
            cursor = node.predecessor
        if not model:
            provider, model = session_provider, session_model
        if not model:
            continue
        if not provider:
            provider = session_provider
        lanes.append({
            "kind": "sub_agent" if spawned else "branch",
            "id": head,
            "name": tip.get("name") or "",
            "provider": provider,
            "model": model,
            "depth": depth,
            "updated_at": tip.get("updated_at") or 0,
            "active": head == session.get("head_id"),
        })
    trunk = max((l for l in lanes if l["kind"] == "branch"), key=lambda l: l["depth"], default=None)
    for lane in lanes:
        lane["trunk"] = lane is trunk
    return lanes


def _open_session(db, sid: str, key: tuple) -> None:
    session = db.get_session(sid)
    try:
        lanes = _branch_lanes(db, session) if session is not None else []
    except Exception:
        lanes = []
    if len(_lane_cache) > 256:
        _lane_cache.clear()
    _lane_cache[key] = (session, lanes)


def _session_lanes(db, sids: list[str], by_id: dict) -> dict[str, tuple[dict | None, list[dict]]]:
    """Each session and its branch lanes, opened only when the index says it
    changed since the last look. Sessions still opening when the budget runs
    out are left out of the result."""
    keys = {sid: (sid, (by_id.get(sid) or {}).get("updated_at")) for sid in sids}
    pending = []
    for sid, key in keys.items():
        if key in _lane_cache:
            continue
        future = _opening.get(key)
        if future is None:
            future = _opening[key] = _opener.submit(_open_session, db, sid, key)
            future.add_done_callback(lambda _f, key=key: _opening.pop(key, None))
        pending.append(future)
    if pending:
        wait(pending, timeout=_OPEN_BUDGET)
    out = {}
    for sid, key in keys.items():
        cached = _lane_cache.get(key)
        if cached is not None:
            session, lanes = cached
            out[sid] = (session, [dict(lane) for lane in lanes])
    return out


def _accounts(providers: list[str]) -> dict[str, list[dict]]:
    """Accounts of each provider and whether each can take requests now."""
    try:
        from openprogram.models.auth.account.account_labels import effective_account_label
        from openprogram.models.auth.account.aliases import resolve
        from openprogram.models.auth.store import get_store
        pools = get_store().list_pools()
    except Exception:
        return {p: [] for p in providers}
    now_ms = int(time.time() * 1000)
    return {p: _pool_accounts(resolve(p), pools, now_ms, effective_account_label) for p in providers}


def _pool_accounts(pool_id: str, pools, now_ms: int, effective_account_label) -> list[dict]:
    out = []
    for pool in pools:
        if pool.provider_id != pool_id or not pool.credentials:
            continue
        cred = pool.credentials[0]
        status = getattr(cred, "status", "") or "valid"
        until = int(getattr(cred, "cooldown_until_ms", 0) or 0)
        if status == "rate_limited" and until <= now_ms:
            status, until = "valid", 0
        out.append({
            "label": effective_account_label(cred, pool.account_id),
            "status": status,
            "until_ms": until if until > now_ms else 0,
        })
    return out


def build_overview(window: float) -> dict:
    from openprogram.models.usage.ledger import default_ledger
    from openprogram.runtime.context.session_db import default_db

    now = time.time()
    since = now - window
    db = default_db()
    activity = default_ledger.session_activity(since=since)
    events = default_ledger.route_events(since=since, limit=50)

    rows = db.list_sessions(limit=100000)
    by_id = {r["id"]: r for r in rows}
    last_call: dict[str, float] = {}
    children: dict[str, dict[tuple, dict]] = {}
    own_calls: dict[str, dict] = {}
    for row in activity:
        sid, parent = row.get("session_id"), row.get("parent_session_id")
        if not sid:
            continue
        last_call[sid] = max(last_call.get(sid, 0), row["last_ts"])
        if parent and parent != sid:
            last_call[parent] = max(last_call.get(parent, 0), row["last_ts"])
            group = children.setdefault(parent, {})
            lane = group.setdefault((row["provider"], row["model_id"]), {
                "sessions": set(), "last_ts": 0.0, "events": 0})
            lane["sessions"].add(sid)
            lane["last_ts"] = max(lane["last_ts"], row["last_ts"])
            lane["events"] += row["events"]
        elif sid not in own_calls or row["last_ts"] > own_calls[sid]["last_ts"]:
            own_calls[sid] = row

    child_ids = {sid for group in children.values() for lane in group.values() for sid in lane["sessions"]}
    candidates = {
        r["id"] for r in rows
        if r.get("status") == "running" or (r.get("updated_at") or 0) >= since
    } | set(last_call)
    candidates -= {sid for sid in child_ids if sid not in children}

    def recency(sid: str) -> tuple:
        row = by_id.get(sid) or {}
        return (row.get("status") == "running",
                max(row.get("updated_at") or 0, last_call.get(sid, 0)))

    visible = sorted(candidates, key=recency, reverse=True)[:_MAX_SESSIONS]
    opened = _session_lanes(db, visible, by_id)
    partial = False
    sessions = []
    for sid in visible:
        row = by_id.get(sid)
        if sid in opened:
            session, lanes = opened[sid]
        else:
            # Still opening: the index row and the ledger stand in, and the
            # client asks again soon.
            partial = True
            session, lanes = row, []
        if session is None:
            continue
        running = (row or {}).get("status") == "running"
        hidden_branches = max(0, len(lanes) - _MAX_BRANCHES)
        lanes = sorted(lanes, key=lambda l: (l["active"], l["updated_at"]), reverse=True)[:_MAX_BRANCHES]
        recent = last_call.get(sid, 0) >= now - _LIVE_SECONDS
        for lane in lanes:
            lane["live"] = lane["active"] and (running or recent)
        if not lanes and sid in own_calls:
            call = own_calls[sid]
            lanes.append({
                "kind": "calls", "id": "calls", "name": "", "trunk": False,
                "provider": call["provider"], "model": call["model_id"],
                "updated_at": call["last_ts"], "active": True,
                "live": call["last_ts"] >= now - _LIVE_SECONDS,
            })
        for (provider, model), lane in sorted(children.get(sid, {}).items(),
                                             key=lambda kv: kv[1]["last_ts"], reverse=True):
            lanes.append({
                "kind": "children", "id": f"children:{provider}:{model}", "name": "",
                "trunk": False, "provider": provider, "model": model,
                "count": len(lane["sessions"]), "events": lane["events"],
                "updated_at": lane["last_ts"], "active": False,
                "live": lane["last_ts"] >= now - _LIVE_SECONDS,
            })
        if not lanes:
            continue
        sessions.append({
            "id": sid,
            "title": session.get("title") or "",
            "source": session.get("source") or "",
            "channel": session.get("channel") or (row or {}).get("channel") or "",
            "running": running,
            "updated_at": max(session.get("updated_at") or 0, last_call.get(sid, 0)),
            "lanes": lanes,
            "hidden_branches": hidden_branches,
        })

    # Calls no listed session accounts for: program runtimes ("op-…" ids),
    # titling, memory and other background work. One pseudo-session keeps
    # the map's total honest instead of dropping them.
    shown = {s["id"] for s in sessions}
    background: dict[tuple, dict] = {}
    for row in activity:
        sid, parent = row.get("session_id"), row.get("parent_session_id")
        if (sid and sid in shown) or (parent and parent in shown):
            continue
        lane = background.setdefault((row["provider"], row["model_id"]), {"events": 0, "last_ts": 0.0})
        lane["events"] += row["events"]
        lane["last_ts"] = max(lane["last_ts"], row["last_ts"])
    if background:
        sessions.append({
            "id": "background", "kind": "background", "title": "", "source": "", "channel": "",
            "running": False, "updated_at": max(l["last_ts"] for l in background.values()),
            "hidden_branches": 0,
            "lanes": [
                {"kind": "background", "id": f"background:{provider}:{model}", "name": "",
                 "trunk": False, "provider": provider, "model": model, "events": lane["events"],
                 "updated_at": lane["last_ts"], "active": False,
                 "live": lane["last_ts"] >= now - _LIVE_SECONDS}
                for (provider, model), lane in sorted(background.items(),
                                                     key=lambda kv: kv[1]["last_ts"], reverse=True)
            ],
        })

    providers = sorted({lane["provider"] for s in sessions for lane in s["lanes"] if lane["provider"]}
                       | {e[k] for e in events for k in ("from_provider", "to_provider") if e[k]})
    titles = {s["id"]: s["title"] for s in sessions}
    return {
        "now": now,
        "window": window,
        "sessions": sessions,
        "idle_sessions": max(0, len(rows) - len(shown)),
        "accounts": _accounts(providers),
        "events": [
            {**e, "session_title": titles.get(e.get("session_id") or "")
             or titles.get(e.get("parent_session_id") or "") or ""}
            for e in events[:8]
        ],
        "partial": partial,
        "stats": {
            "requests": sum(int(r["events"]) for r in activity),
            "reroutes": len(events),
        },
    }


def register(app):
    @app.get("/api/routing/overview")
    def api_routing_overview(window: float = QParam(1800, ge=60, le=7 * 86400)):
        return JSONResponse(content=build_overview(window))
