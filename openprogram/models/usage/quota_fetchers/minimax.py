"""MiniMax token plan: windows from ``GET /v1/token_plan/remains``.

Unverified: the shape is Oh My Pi's (``packages/ai/src/usage/minimax-code.ts``)
and MiniMax Code's: ``model_remains[]`` buckets, each with an interval
(``start_time``, ``end_time``, ``current_interval_remaining_percent``,
``current_interval_status``) and a week (``weekly_*``). Status 2 is spent;
a bucket with zero totals and both windows "unlimited" (status 3) is not in
the plan and is left out.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlsplit

from ..quota import SOURCE_ENDPOINT, STATUS_EXHAUSTED, QuotaLimit, window_label

PROVIDERS = ("minimax", "minimax-cn")
VERIFIED = False
ENDPOINT = "GET https://api.minimax.io/v1/token_plan/remains"
SHAPE_FROM = "oh-my-pi packages/ai/src/usage/minimax-code.ts"
_ORIGINS = {"minimax": "https://api.minimax.io", "minimax-cn": "https://api.minimaxi.com"}


def request(cred: Any) -> tuple[str, dict, Optional[str]]:
    origin = _ORIGINS.get(cred.provider, _ORIGINS["minimax"])
    if cred.base_url:
        parts = urlsplit(cred.base_url)
        if parts.scheme and parts.netloc:
            origin = f"{parts.scheme}://{parts.netloc}"
    return f"{origin}/v1/token_plan/remains", {"Authorization": f"Bearer {cred.token}"}, origin


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _seconds(value: Any) -> Optional[float]:
    number = _number(value)
    if number is None or number <= 0:
        return None
    return number / 1000.0 if number > 1e12 else number


def _limit(name: str, key: str, remaining_percent: Any, status: Any, start: Any, end: Any,
           now: float, default_minutes: Optional[int]) -> Optional[QuotaLimit]:
    left = _number(remaining_percent)
    exhausted = _number(status) == 2
    if left is None and not exhausted:
        return None
    used = 100.0 if exhausted else max(0.0, min(100.0, 100.0 - left))
    begin, finish = _seconds(start), _seconds(end)
    minutes = int((finish - begin) // 60) if begin and finish and finish > begin else default_minutes
    prefix = "" if name == "general" else f"{name} "
    return QuotaLimit(
        limit_key=f"{name}:{key}", label=f"{prefix}{window_label(minutes)}", unit="percent",
        source=SOURCE_ENDPOINT, fetched_at=now, used=used, limit=100.0, remaining=100.0 - used,
        used_fraction=used / 100.0, window_minutes=minutes, resets_at=finish,
        **({"status": STATUS_EXHAUSTED} if exhausted else {}),
    )


def parse(payload: Any, now: float) -> list[QuotaLimit]:
    buckets = payload.get("model_remains") if isinstance(payload, dict) else None
    out: list[QuotaLimit] = []
    for bucket in buckets if isinstance(buckets, list) else []:
        if not isinstance(bucket, dict):
            continue
        name = str(bucket.get("model_name") or "").strip()
        if not name:
            continue
        not_in_plan = (
            _number(bucket.get("current_interval_total_count")) == 0
            and _number(bucket.get("current_weekly_total_count")) == 0
            and _number(bucket.get("current_interval_status")) == 3
            and _number(bucket.get("current_weekly_status")) == 3
        )
        if not_in_plan:
            continue
        for limit in (
            _limit(name, "interval", bucket.get("current_interval_remaining_percent"),
                   bucket.get("current_interval_status"), bucket.get("start_time"),
                   bucket.get("end_time"), now, None),
            _limit(name, "weekly", bucket.get("current_weekly_remaining_percent"),
                   bucket.get("current_weekly_status"), bucket.get("weekly_start_time"),
                   bucket.get("weekly_end_time"), now, 10080),
        ):
            if limit is not None:
                out.append(limit)
    return out
