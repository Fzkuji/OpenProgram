"""GitHub Copilot: premium requests from ``GET api.github.com/copilot_internal/user``.

Unverified: the shape is Oh My Pi's (``packages/ai/src/usage/github-copilot.ts``)
and OpenCode's Copilot plugin: ``quota_snapshots.{premium_interactions,chat,
completions}`` each with ``entitlement``, ``remaining``, ``percent_remaining``
and ``unlimited``, plus ``quota_reset_date``. Unlimited buckets are left out.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ..quota import SOURCE_ENDPOINT, STATUS_OK, QuotaLimit

PROVIDERS = ("github-copilot",)
VERIFIED = False
ENDPOINT = "GET https://api.github.com/copilot_internal/user"
SHAPE_FROM = "oh-my-pi packages/ai/src/usage/github-copilot.ts"
URL = "https://api.github.com/copilot_internal/user"

_BUCKETS = (("premium_interactions", "premium", "Premium requests"), ("chat", "chat", "Chat requests"),
            ("completions", "completions", "Completions"))


def request(cred: Any) -> tuple[str, dict, Optional[str]]:
    return URL, {"Authorization": f"Bearer {cred.token}", "Accept": "application/json"}, URL


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse(payload: Any, now: float) -> list[QuotaLimit]:
    if not isinstance(payload, dict) or not isinstance(payload.get("quota_snapshots"), dict):
        return []
    try:
        resets = datetime.fromisoformat(str(payload.get("quota_reset_date")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        resets = None
    out: list[QuotaLimit] = []
    for field, key, label in _BUCKETS:
        bucket = payload["quota_snapshots"].get(field)
        if not isinstance(bucket, dict):
            continue
        entitlement = _number(bucket.get("entitlement"))
        remaining = _number(bucket.get("remaining"))
        if bucket.get("unlimited") is True:
            if key == "premium":
                out.append(QuotaLimit(limit_key=key, label=label, unit="requests", source=SOURCE_ENDPOINT,
                                      fetched_at=now, window_minutes=43200, resets_at=resets, status=STATUS_OK))
            continue
        if entitlement is None or remaining is None:
            continue
        out.append(QuotaLimit(
            limit_key=key, label=label, unit="requests", source=SOURCE_ENDPOINT, fetched_at=now,
            used=max(0.0, entitlement - remaining), limit=entitlement, remaining=max(0.0, remaining),
            window_minutes=43200, resets_at=resets,
        ))
    return out
