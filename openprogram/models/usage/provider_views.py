"""Provider state where the chat and the detail page need it.

Three readings of the same records as ``provider_status`` (keys, quota,
usage ledger), each sized for one place:

  * :func:`picker_states` — per provider, what the composer's model picker
    shows: configured or not, the health state and its fix, the limit
    closest to running out (as % left, with the ring and warning rules) and
    a balance. It is carried by ``GET /api/models/enabled`` beside the
    models, so the picker makes no second request. Stored values only, plus
    a keyless loopback probe of a local server at most every 30 s.
  * :func:`provider_usage` — one provider's spend over 7 to 90 days, by day
    and by model, with the quota samples for the limit line
    (``GET /api/providers/{id}/usage``).
  * :func:`fallback_for` — the model the quota-exhausted reply card offers:
    the same model id on another healthy provider, else the most used
    healthy model of the last 7 days with the same capabilities and a
    context window that holds the conversation
    (``GET /api/providers/fallback``). It never switches by itself.

Thresholds follow the bar rule of the design: warning tone from 80 % used,
danger from 95 % or when spent; the ring appears from 50 % used, only for
windows and key limits; the early-warning flag is Claude Code's time-aware
rule (``quota.should_warn``).
"""
from __future__ import annotations

import time
from typing import Any, Literal, Optional, TypedDict

from .provider_status import (
    DAYS,
    FixWire,
    HealthState,
    ProviderQuotaFrame,
    Tone,
    fix_for,
    health_of,
    is_subscription,
    keys_of,
    local_health,
    quota_of,
)
from .quota import DANGER_AT, WARNING_AT, QuotaLimit, default_store, should_warn

#: The ring in the model picker appears from this share used.
RING_FROM = 0.50
_MONEY = ("balance", "key_limit", "credits")


class PickerQuota(TypedDict):
    """The limit of the key in use that is closest to running out."""

    limit_key: str
    label: str
    left_fraction: float
    resets_at: Optional[float]
    tone: Tone
    #: Draw the 16 px ring: a window or key limit with half or more used.
    ring: bool
    #: Usage is ahead of the window's elapsed time from 80 %, or 95 % used.
    warn: bool


class PickerBalance(TypedDict):
    amount: float
    unit: str
    label: str


class PickerProvider(TypedDict):
    id: str
    #: Has a key, a sign-in, or (local) a server address.
    configured: bool
    state: HealthState
    #: For ``rate_limited``: when it clears (Unix seconds).
    until: Optional[float]
    fix: Optional[FixWire]
    quota: Optional[PickerQuota]
    balance: Optional[PickerBalance]
    #: The values are older than 15 minutes.
    stale: bool
    #: Mark the picker trigger: an action is needed, or the quota warns.
    attention: bool


def _tone(fraction: Optional[float], status: str) -> Tone:
    if status == "exhausted" or (fraction is not None and fraction >= DANGER_AT):
        return "danger"
    if fraction is not None and fraction >= WARNING_AT:
        return "warning"
    return "normal"


def _ringable(limit: QuotaLimit) -> bool:
    base = limit.limit_key.rsplit(":", 1)[-1]
    return limit.unit == "percent" or (limit.window_minutes or 0) > 1 or base == "key_limit"


def picker_quota(limits: list[QuotaLimit], now: Optional[float] = None) -> Optional[PickerQuota]:
    """The window or key limit closest to running out, as the picker draws it."""
    ranked = [limit for limit in limits if limit.used_fraction is not None and _ringable(limit)]
    if not ranked:
        return None
    worst = max(ranked, key=lambda limit: (limit.status == "exhausted", limit.used_fraction))
    used = float(worst.used_fraction or 0.0)
    return {
        "limit_key": worst.limit_key,
        "label": worst.label,
        "left_fraction": round(1 - used, 4),
        "resets_at": worst.resets_at,
        "tone": _tone(used, worst.status),
        "ring": used >= RING_FROM,
        "warn": should_warn(worst, now) or worst.status == "exhausted",
    }


def picker_balance(limits: list[QuotaLimit]) -> Optional[PickerBalance]:
    for limit in limits:
        if limit.limit_key.rsplit(":", 1)[-1] in _MONEY and limit.remaining is not None:
            return {"amount": float(limit.remaining), "unit": limit.unit, "label": limit.label}
    return None


_ACTION_STATES = frozenset({"signed_out", "no_credit", "invalid_key", "not_configured", "not_running"})


#: A local server's running/not-running probe is reused this long by the picker.
LOCAL_PROBE_MAX_AGE_S = 30.0


def picker_state(provider_id: str, *, configured: bool, now: Optional[float] = None) -> PickerProvider:
    """One provider's picker state from what is stored. No provider is asked;
    the only request is a keyless loopback probe of a local server, reused
    for :data:`LOCAL_PROBE_MAX_AGE_S`."""
    from openprogram.models.providers.catalog.local import is_local_provider
    from .quota_fetchers import last_reads
    now = time.time() if now is None else now
    keys = keys_of(provider_id)
    stored = {k["credential_id"] for k in keys} | {""}
    reports = [r for r in default_store.reports(provider_id) if r.account in stored]
    reads = {a: r for a, r in last_reads(provider_id).items() if a in stored}
    if is_local_provider(provider_id):
        health = local_health(provider_id, max_age=LOCAL_PROBE_MAX_AGE_S)
    else:
        health = health_of(keys, reports, now, reads)
        if not configured:
            health = {"state": "not_configured", "until": None, "detail": None}
    wire = quota_of(reports, keys, now, reads)
    account = wire["account"] or ""
    report = next((r for r in reports if r.account == account), None)
    limits = report.limits if report else []
    quota = picker_quota(limits, now)
    state = health["state"]
    return {
        "id": provider_id,
        "configured": configured,
        "state": state,
        "until": health["until"],
        "fix": fix_for(provider_id, state),
        "quota": quota,
        "balance": picker_balance(limits),
        "stale": bool(wire["stale"]),
        "attention": state in _ACTION_STATES or bool(quota and (quota["warn"] or quota["tone"] == "danger")),
    }


class ProviderQuotaEvent(ProviderQuotaFrame):
    """``data`` of the ``provider_quota`` WebSocket frame: one key's new
    quota plus its provider's recomputed picker state (null if unreadable)."""

    picker: Optional[PickerProvider]


def picker_states(provider_ids: list[str], *, now: Optional[float] = None) -> dict[str, PickerProvider]:
    """The picker state of each provider; a provider that fails to read is left out."""
    from openprogram.models.providers.catalog.metadata import credential_status_snapshot, is_configured
    out: dict[str, PickerProvider] = {}
    with credential_status_snapshot():
        for pid in dict.fromkeys(provider_ids):
            try:
                out[pid] = picker_state(pid, configured=is_configured(pid), now=now)
            except Exception:
                continue
    return out


# Usage of one provider

UsageGroup = Literal["day", "model", "day,model"]
USAGE_GROUPS = ("day", "model", "day,model")


class UsageTotals(TypedDict):
    calls: int
    #: Billed USD; null for a subscription (its calls are included).
    cost_usd: Optional[float]
    #: The same calls at API list price.
    list_price_usd: float
    included: bool
    unpriced_calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_tokens: int
    cache_share: Optional[float]


class DayModel(TypedDict):
    list_price_usd: float
    tokens: int


class UsageDay(TypedDict):
    day: str
    ts: float
    list_price_usd: float
    tokens: int
    calls: int
    #: Per model id (``group=day,model`` only; empty otherwise).
    models: dict[str, DayModel]


class UsageModel(TypedDict):
    model_id: str
    calls: int
    list_price_usd: float
    unpriced_calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_tokens: int


class QuotaSample(TypedDict):
    limit_key: str
    account: str
    ts: float
    remaining: Optional[float]
    used_fraction: Optional[float]


class ProviderUsage(TypedDict):
    provider: str
    days: int
    since: float
    until: float
    group: UsageGroup
    totals: UsageTotals
    series: list[UsageDay]
    models: list[UsageModel]
    #: Samples of each limit (one per 15 minutes) for the limit line and balance history.
    quota_samples: list[QuotaSample]


def _tokens(row: Any) -> int:
    return int(row.input_tokens + row.output_tokens + row.cache_read_tokens + row.cache_write_tokens)


def provider_usage(provider_id: str, *, days: int = DAYS, group: str = "day", ledger: Any = None,
                   now: Optional[float] = None) -> ProviderUsage:
    """One provider's usage over the last ``days`` UTC days, grouped by day and/or model."""
    if group not in USAGE_GROUPS:
        raise ValueError(f"group must be one of {', '.join(USAGE_GROUPS)}")
    if not 1 <= days <= 90:
        raise ValueError("days must be in [1, 90]")
    now = time.time() if now is None else now
    if ledger is None:
        from .ledger import default_ledger as ledger
    first = int(now // 86400) - days + 1
    since, until = first * 86400.0, (first + days) * 86400.0
    filters = {"provider": provider_id}
    total_rows = ledger.query(since=since, until=until, filters=filters)
    included = is_subscription(provider_id)
    tot = total_rows[0] if total_rows and total_rows[0].events else None
    prompt = (tot.input_tokens + tot.cache_read_tokens + tot.cache_write_tokens) if tot else 0
    price = round(tot.cost_total, 6) if tot else 0.0
    totals: UsageTotals = {
        "calls": tot.events if tot else 0,
        "cost_usd": None if included else price,
        "list_price_usd": price,
        "included": included,
        "unpriced_calls": tot.unknown_cost_events if tot else 0,
        "input_tokens": tot.input_tokens if tot else 0,
        "output_tokens": tot.output_tokens if tot else 0,
        "cache_read_tokens": tot.cache_read_tokens if tot else 0,
        "cache_write_tokens": tot.cache_write_tokens if tot else 0,
        "total_tokens": _tokens(tot) if tot else 0,
        "cache_share": round(tot.cache_read_tokens / prompt, 4) if tot and prompt else None,
    }
    series: list[UsageDay] = []
    if "day" in group:
        by_day = {int(r.keys["day"]): r for r in ledger.query(since=since, until=until, filters=filters,
                                                             group_by=["day"])}
        split: dict[int, dict[str, DayModel]] = {}
        if group == "day,model":
            for r in ledger.query(since=since, until=until, filters=filters, group_by=["day", "model_id"]):
                split.setdefault(int(r.keys["day"]), {})[r.keys.get("model_id") or ""] = {
                    "list_price_usd": round(r.cost_total, 6), "tokens": _tokens(r)}
        for index in range(first, first + days):
            r = by_day.get(index)
            series.append({
                "day": time.strftime("%Y-%m-%d", time.gmtime(index * 86400)), "ts": index * 86400.0,
                "list_price_usd": round(r.cost_total, 6) if r else 0.0, "tokens": _tokens(r) if r else 0,
                "calls": r.events if r else 0, "models": split.get(index, {}),
            })
    models: list[UsageModel] = []
    if "model" in group:
        for r in ledger.query(since=since, until=until, filters=filters, group_by=["model_id"]):
            models.append({
                "model_id": r.keys.get("model_id") or "", "calls": r.events,
                "list_price_usd": round(r.cost_total, 6), "unpriced_calls": r.unknown_cost_events,
                "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                "cache_read_tokens": r.cache_read_tokens, "cache_write_tokens": r.cache_write_tokens,
                "total_tokens": _tokens(r),
            })
        models.sort(key=lambda m: (-m["list_price_usd"], -m["total_tokens"], m["model_id"]))
    try:
        samples = ledger.quota_samples(provider=provider_id, since=since)
    except Exception:
        samples = []
    return {
        "provider": provider_id, "days": days, "since": since, "until": until, "group": group,  # type: ignore[typeddict-item]
        "totals": totals, "series": series, "models": models,
        "quota_samples": [{"limit_key": s["limit_key"], "account": s["account"], "ts": s["ts"],
                           "remaining": s["remaining"], "used_fraction": s["used_fraction"]} for s in samples],
    }


# Fallback for a spent quota

FallbackReason = Literal["same_model", "most_used"]


class FallbackModel(TypedDict):
    provider: str
    provider_label: str
    model: str
    name: str
    context_window: int
    reason: FallbackReason


class FallbackPayload(TypedDict):
    #: What ``Continue with …`` uses, or null when no healthy model fits.
    suggestion: Optional[FallbackModel]
    #: Up to four more that fit, for ``Other model…``.
    alternatives: list[FallbackModel]


def _bare(model_id: str) -> str:
    return (model_id or "").rsplit("/", 1)[-1].split(":", 1)[0].lower()


def _caps(row: dict) -> tuple[bool, bool, bool]:
    return bool(row.get("tools", True)), bool(row.get("vision")), bool(row.get("reasoning"))


def fallback_for(provider: str, model: str, *, context_tokens: int = 0, enabled: Optional[list[dict]] = None,
                 states: Optional[dict[str, PickerProvider]] = None, ledger: Any = None,
                 now: Optional[float] = None) -> FallbackPayload:
    """What to offer when ``provider``'s quota is spent while ``model`` was answering."""
    now = time.time() if now is None else now
    if enabled is None:
        from openprogram.models.listing import list_enabled_models
        enabled = list_enabled_models()
    current = next((m for m in enabled if m.get("provider") == provider and m.get("id") == model), None)
    if current is None:
        from openprogram.models.providers.catalog.sources import models_dev
        current = models_dev.lookup(provider, model) or {}
    caps = _caps(current)
    others = [m for m in enabled if m.get("provider") != provider]
    if states is None:
        states = picker_states(sorted({m["provider"] for m in others}), now=now)

    def healthy(pid: str) -> bool:
        state = states.get(pid)
        if state is None:
            return False
        spent = state["quota"] is not None and state["quota"]["left_fraction"] <= 0
        return state["state"] in ("ready", "unknown") and not spent

    def fits(row: dict) -> bool:
        window = int(row.get("context_window") or 0)
        return healthy(row["provider"]) and (not context_tokens or not window or window >= context_tokens)

    def wire(row: dict, reason: FallbackReason) -> FallbackModel:
        return {"provider": row["provider"], "provider_label": str(row.get("provider_label") or row["provider"]),
                "model": row["id"], "name": str(row.get("name") or row["id"]),
                "context_window": int(row.get("context_window") or 0), "reason": reason}

    picked: list[FallbackModel] = []
    seen: set[tuple[str, str]] = set()
    for row in others:
        if _bare(row["id"]) == _bare(model) and fits(row):
            picked.append(wire(row, "same_model"))
            seen.add((row["provider"], row["id"]))
    if ledger is None:
        from .ledger import default_ledger as ledger
    try:
        used = ledger.provider_models(since=now - 7 * 86400)
    except Exception:
        used = []
    by_key = {(m["provider"], m["id"]): m for m in others}
    ranked = [by_key[(u["provider"], u["model_id"])] for u in used if (u["provider"], u["model_id"]) in by_key]
    ranked += [m for m in others if m not in ranked]
    for row in ranked:
        key = (row["provider"], row["id"])
        if key in seen or not fits(row) or _caps(row) != caps:
            continue
        picked.append(wire(row, "most_used"))
        seen.add(key)
        if len(picked) >= 5:
            break
    return {"suggestion": picked[0] if picked else None, "alternatives": picked[1:5]}


__all__ = [
    "DayModel",
    "FallbackModel",
    "FallbackPayload",
    "PickerBalance",
    "PickerProvider",
    "PickerQuota",
    "ProviderQuotaEvent",
    "ProviderUsage",
    "QuotaSample",
    "RING_FROM",
    "USAGE_GROUPS",
    "UsageDay",
    "UsageModel",
    "UsageTotals",
    "fallback_for",
    "picker_quota",
    "picker_state",
    "picker_states",
    "provider_usage",
]
