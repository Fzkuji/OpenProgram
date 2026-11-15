"""Where a request's cached prefix broke, and why.

The provider reuses a cached prompt prefix only when every byte before the
new part is what an earlier request sent. Two checks say whether that held:

1. **Before the request goes out** (:func:`check_request`). The request
   the adapter captured (``providers/recording/capture.py``) is cut into
   units: tools, system texts, history items. It is compared with the last
   answered request on the same **lane**: the session, the kind of call, the
   label the runtime gave it (a Goal verification turn has its own prompt,
   so its own lane), the agent and the conversation branch. When the
   previous request's units are not a prefix of this one, the first unit
   that differs is the break and names the cause (``system:open_work``,
   ``tools:read``, ``history:assistant``). Units match by their bytes
   alone; the runtime's section names only say where a changed system text
   first differs, so a prompt sent again after a restart, cut into sections
   differently or not at all, still matches. A changed model, reasoning
   effort, cache key, cache lifetime or tool choice is a cause of its own
   (``model``, ``setting:effort`` …), checked first. This needs no provider
   usage, so a provider that reports no cache tokens is checked the same
   way. The check only reads; nothing is written before the request goes
   out.
2. **After the reply** (:func:`settle_check`). With the request intact,
   cached input that fell by more than 5% and more than 2,000 tokens since
   the previous request is put down to the cache lifetime passing
   (``ttl``) or, inside it, to the provider (``server``). The answered
   request then becomes the lane's baseline. A request sent again mended
   inside one attempt is checked again, so the baseline is the request the
   provider answered.

Only conversation-shaped calls are tracked: chat, Agent-method and
subagent turns. A one-shot side call (permission check, memory writing,
summary, title, prediction, a tool's own call) sends a prompt of its own
each time and is not compared.

A break the runtime causes on purpose (tool results aging, a compaction, a
rewind, a model switch) is announced first with :func:`expect_break`; the
next request on the lane records it as ``expected``. The first request on a
new branch is compared with the last request on the branch it left, and
records ``branch`` as expected.

Baselines are kept in memory per lane and written to the usage database
beside the ledger, so a Worker restart compares its first request with the
last one answered before it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import weakref
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from .context import UsageContext, current_usage_context
from .event import CALL_KIND_CHAT, CALL_KIND_EXEC, CALL_KIND_SUBAGENT

_log = logging.getLogger(__name__)

#: A fall in cached input counts when it is more than this share of the
#: previous request's cached input …
CACHE_DROP_SHARE = 0.05
#: … and more than this many tokens.
CACHE_DROP_TOKENS = 2_000
#: Settings checked before the units, in this order.
SETTINGS = ("model", "cache_key", "ttl", "effort", "format", "tool_choice")
#: Lanes not written to for this long are dropped.
BASELINE_KEPT_S = 30 * 24 * 3600
#: The kinds of call whose requests carry a conversation on.
TRACKED_KINDS = frozenset({CALL_KIND_CHAT, CALL_KIND_EXEC, CALL_KIND_SUBAGENT})

_TTL_SECONDS = {"5m": 300, "short": 300, "in_memory": 300, "1h": 3600, "long": 3600, "24h": 86400}


@dataclass(frozen=True)
class PrefixCheck:
    """What the check found for one request."""

    prefix_hash: str
    lane: Optional[str] = None
    units: int = 0
    break_at: Optional[int] = None
    break_cause: Optional[str] = None
    expected: Optional[str] = None
    gap_s: Optional[float] = None
    ttl_s: float = 300.0
    previous_cache_read: Optional[int] = None
    sent_at: float = 0.0
    family: Optional[str] = None
    session_id: Optional[str] = None
    #: The announced causes this request answers, cleared when it settles.
    seen_expected: Optional[str] = None
    fingerprint: dict = field(default_factory=dict, repr=False, compare=False)

    def event_fields(self) -> dict[str, Any]:
        return {"prefix_hash": self.prefix_hash, "break_at": self.break_at,
                "break_cause": self.break_cause, "expected": self.expected}


def fingerprint(request: Any) -> dict[str, Any]:
    """The request's units as ``[digest, label]`` pairs (a system text adds
    its sections as ``[name, end, digest]``), its settings, and a hash of
    both."""
    units = []
    for unit in request.units():
        sections = getattr(unit, "sections", ())
        units.append([unit.digest, unit.label, [list(row) for row in sections]] if sections
                     else [unit.digest, unit.label])
    settings = request.settings()
    whole = json.dumps([settings, [entry[0] for entry in units]], sort_keys=True)
    return {"units": units, "settings": settings,
            "hash": hashlib.sha256(whole.encode("utf-8")).hexdigest()[:16]}


def first_break(previous: dict, current: dict) -> tuple[Optional[int], Optional[str]]:
    """``(index, cause)`` of the first place ``current`` stops extending
    ``previous``; ``(None, None)`` when it extends it."""
    before, now = previous.get("settings") or {}, current.get("settings") or {}
    for name in SETTINGS:
        if before.get(name) != now.get(name):
            return 0, name if name == "model" else f"setting:{name}"
    old, new = previous.get("units") or [], current.get("units") or []
    for index, (was, unit) in enumerate(zip(old, new)):
        if was[0] == unit[0]:
            continue
        if was[1] == unit[1] == "system":
            return index, _section_changed(was, unit)
        return index, unit[1] if unit[1] == was[1] else _changed(was[1], unit[1])
    if len(new) < len(old):
        return len(new), f"removed:{old[len(new)][1]}"
    return None, None


def _section_changed(was: list, now: list) -> str:
    """The section of a changed system text where it first differs: past
    the longest start both texts share at a section end they both have."""
    before = {(int(end), digest) for _name, end, digest in (was[2] if len(was) > 2 else [])}
    sections = now[2] if len(now) > 2 else []
    same = max((int(end) for _name, end, digest in sections if (int(end), digest) in before), default=0)
    for name, end, _digest in sections:
        if int(end) > same:
            return f"system:{name}"
    gone = [name for name, end, _digest in (was[2] if len(was) > 2 else []) if int(end) > same]
    return f"removed:system:{gone[0]}" if gone else "system"


def _changed(was: str, now: str) -> str:
    """A unit replaced by one of another kind: name the part of the prompt
    (``tools``, ``system``, ``history``) when both are in it."""
    kind_was, kind_now = was.split(":", 1)[0], now.split(":", 1)[0]
    if kind_was != kind_now or ":" not in now:
        return now
    return f"{kind_now}:{now.split(':', 1)[1]}"


def _family(ctx: UsageContext) -> Optional[str]:
    if not ctx.session_id or ctx.call_kind not in TRACKED_KINDS:
        return None
    return "|".join((ctx.session_id, ctx.call_kind, ctx.prefix_lane or ctx.call_label or "",
                     ctx.agent_id or ""))


def lane(context: Optional[UsageContext] = None) -> Optional[str]:
    """The lane a request is compared on, or None for a request that is not
    a conversation's turn (outside any session, or a one-shot side call)."""
    ctx = context or current_usage_context()
    family = _family(ctx)
    return None if family is None else f"{family}|{ctx.branch_id or ''}"


def _ttl_seconds(settings: dict) -> float:
    return float(_TTL_SECONDS.get(str(settings.get("ttl") or "5m"), 300))


def _ledger(ledger: Any = None) -> Any:
    if ledger is not None:
        return ledger
    from .ledger import default_ledger
    return default_ledger


# Baselines ------------------------------------------------------------------

#: Per ledger, the baseline of each lane read or written in this process:
#: ``{"ts", "fingerprint", "cache_read", "expected", "session_id", "family"}``.
_memory: "weakref.WeakKeyDictionary[Any, dict[str, dict]]" = weakref.WeakKeyDictionary()


def _known(lg: Any) -> dict[str, dict]:
    try:
        return _memory.setdefault(lg, {})
    except TypeError:  # a ledger that cannot be weakly referenced
        return {}


def _row(row: Any) -> dict:
    return {"ts": float(row["ts"]), "fingerprint": json.loads(row["fingerprint_json"]),
            "cache_read": row["cache_read"], "expected": row["expected"],
            "session_id": row["session_id"], "family": row["family"]}


def _baseline(lg: Any, key: str) -> Optional[dict]:
    """The lane's baseline: from memory, else (first request on the lane in
    this process) from the database."""
    known = _known(lg)
    if key in known:
        return known[key]
    try:
        with lg.read() as conn:
            row = conn.execute("SELECT * FROM prefix_baselines WHERE lane = ?", (key,)).fetchone()
    except Exception:
        _log.warning("prefix baseline not read for lane %s", key, exc_info=True)
        return None
    if row is None:
        return None
    known[key] = _row(row)
    return known[key]


def _family_baseline(lg: Any, family: str) -> Optional[dict]:
    """The latest baseline of another branch of the same lane: where a new
    branch left from."""
    try:
        with lg.read() as conn:
            row = conn.execute("SELECT * FROM prefix_baselines WHERE family = ? ORDER BY ts DESC LIMIT 1",
                               (family,)).fetchone()
    except Exception:
        _log.warning("prefix baseline not read for %s", family, exc_info=True)
        return None
    return None if row is None else _row(row)


def check_request(request: Any, *, context: Optional[UsageContext] = None,
                  ledger: Any = None, now: Optional[float] = None) -> PrefixCheck:
    """Compare ``request`` with its lane's last answered request."""
    now = time.time() if now is None else now
    current = fingerprint(request)
    ctx = context or current_usage_context()
    key, family = lane(ctx), _family(ctx)
    check = PrefixCheck(prefix_hash=current["hash"], lane=key, units=len(current["units"]),
                        ttl_s=_ttl_seconds(current["settings"]), sent_at=now, family=family,
                        session_id=ctx.session_id, fingerprint=current)
    if key is None:
        return check
    lg = _ledger(ledger)
    previous = _baseline(lg, key)
    announced = None if previous is None else previous.get("expected")
    if previous is None and family is not None:
        previous = _family_baseline(lg, family)
        if previous is None:
            return check
        announced = ",".join(filter(None, (previous.get("expected"), "branch")))
    elif previous is None:
        return check
    at, cause = first_break(previous["fingerprint"], current)
    return replace(check, break_at=at, break_cause=cause,
                   expected=announced or None if cause is not None else None,
                   seen_expected=announced or None,
                   gap_s=max(0.0, now - float(previous["ts"])),
                   previous_cache_read=previous.get("cache_read"))


def settle_check(check: Optional[PrefixCheck], usage: Any, *, ledger: Any = None) -> Optional[PrefixCheck]:
    """Add what the reply's usage says to ``check`` and make its request,
    with its cached input, the lane's baseline."""
    if check is None:
        return check
    cache_read = None if usage is None else int(getattr(usage, "cache_read", 0) or 0)
    previous = check.previous_cache_read
    if (check.break_cause is None and previous and cache_read is not None
            and previous - cache_read > max(CACHE_DROP_TOKENS, previous * CACHE_DROP_SHARE)):
        late = check.gap_s is not None and check.gap_s > check.ttl_s
        check = replace(check, break_cause="ttl" if late else "server")
    if check.lane is not None:
        _keep(_ledger(ledger), check, cache_read)
    return check


def _keep(lg: Any, check: PrefixCheck, cache_read: Optional[int]) -> None:
    known = _known(lg)
    pending = (known.get(check.lane) or {}).get("expected")
    # A cause announced while this request was out is still owed.
    left = ",".join(cause for cause in (pending or "").split(",")
                    if cause and cause not in (check.seen_expected or "").split(",")) or None
    known[check.lane] = {"ts": check.sent_at, "fingerprint": check.fingerprint, "cache_read": cache_read,
                         "expected": left, "session_id": check.session_id, "family": check.family}
    try:
        with lg.immediate() as conn:
            if check.previous_cache_read is None and check.gap_s is None:
                conn.execute("DELETE FROM prefix_baselines WHERE ts < ?", (check.sent_at - BASELINE_KEPT_S,))
            conn.execute(
                "INSERT INTO prefix_baselines (lane, family, session_id, ts, fingerprint_json, cache_read, expected) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(lane) DO UPDATE SET family = excluded.family, "
                "session_id = excluded.session_id, ts = excluded.ts, "
                "fingerprint_json = excluded.fingerprint_json, cache_read = excluded.cache_read, "
                "expected = excluded.expected",
                (check.lane, check.family, check.session_id, check.sent_at,
                 json.dumps(check.fingerprint, separators=(",", ":")), cache_read, left))
    except Exception:
        _log.warning("prefix baseline not kept for lane %s", check.lane, exc_info=True)


def _add(expected: Optional[str], cause: str) -> str:
    causes = [item for item in (expected or "").split(",") if item]
    return ",".join(causes if cause in causes else [*causes, cause])


_EVERYWHERE = object()


def expect_break_everywhere(cause: str, *, ledger: Any = None) -> None:
    """:func:`expect_break` for every lane of every session: a setting that
    applies to all conversations changed (the cache lifetime). Never raises."""
    expect_break(_EVERYWHERE, cause, ledger=ledger)  # type: ignore[arg-type]


def expect_break(session_id: Optional[str], cause: str, *, ledger: Any = None) -> None:
    """Announce that the next request breaks its prefix on purpose, because
    of ``cause``. With ``session_id`` every lane of that session is told;
    with None, only the lane of the call being prepared (aging and
    compaction rewrite the request that is about to go out). Never raises."""
    try:
        lg = _ledger(ledger)
        known = _known(lg)
        if session_id is _EVERYWHERE:
            for baseline in known.values():
                baseline["expected"] = _add(baseline.get("expected"), cause)
            where, args = "1 = 1", ()
        elif session_id is None:
            key = lane()
            if key is None or _baseline(lg, key) is None:
                return
            known[key]["expected"] = _add(known[key].get("expected"), cause)
            where, args = "lane = ?", (key,)
        else:
            for baseline in known.values():
                if baseline.get("session_id") == session_id:
                    baseline["expected"] = _add(baseline.get("expected"), cause)
            where, args = "session_id = ?", (session_id,)
        with lg.immediate() as conn:
            conn.execute(
                "UPDATE prefix_baselines SET expected = CASE WHEN expected IS NULL OR expected = '' "
                "THEN ? WHEN instr(',' || expected || ',', ',' || ? || ',') > 0 THEN expected "
                f"ELSE expected || ',' || ? END WHERE {where}",
                (cause, cause, cause, *args))
    except Exception:
        _log.warning("expected prefix break not recorded for %s", session_id, exc_info=True)


__all__ = [
    "CACHE_DROP_SHARE", "CACHE_DROP_TOKENS", "PrefixCheck", "TRACKED_KINDS", "check_request",
    "expect_break", "expect_break_everywhere", "fingerprint", "first_break", "lane", "settle_check",
]
