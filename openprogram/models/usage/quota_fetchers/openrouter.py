"""OpenRouter: what a key may still spend, from ``GET /api/v1/key``.

The key check already reads this endpoint (``models/listing/credentials.py``,
layer 1); the body's ``data.limit``, ``data.limit_remaining`` and
``data.usage`` (US dollars) are kept as one ``key_limit`` row instead of a
yes/no. A key with no limit (``limit: null``) reports what it has spent and
no remaining amount. Hermes reads the same fields
(``agent/account_usage.py``).
"""
from __future__ import annotations

from typing import Any, Optional

from ..quota import SOURCE_ENDPOINT, STATUS_OK, QuotaLimit

PROVIDERS = ("openrouter",)
VERIFIED = True
ENDPOINT = "GET {base}/key"
SHAPE_FROM = "OpenProgram key check (models/listing/credentials.py)"
DEFAULT_BASE = "https://openrouter.ai/api/v1"

_RESET_LABELS = {"daily": "today", "weekly": "this week", "monthly": "this month"}


def request(cred: Any) -> tuple[str, dict, Optional[str]]:
    base = (cred.base_url or DEFAULT_BASE).rstrip("/")
    return f"{base}/key", {"Authorization": f"Bearer {cred.token}"}, base


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse(payload: Any, now: float) -> list[QuotaLimit]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    limit = _number(data.get("limit"))
    remaining = _number(data.get("limit_remaining"))
    used = _number(data.get("usage"))
    if limit is None and remaining is None and used is None:
        return []
    reset = data.get("limit_reset")
    label = "Key limit"
    if isinstance(reset, str) and reset in _RESET_LABELS:
        label = f"Key limit {_RESET_LABELS[reset]}"
    if limit is None:
        # No limit on the key: it spends from the account's credits.
        return [QuotaLimit(
            limit_key="key_limit", label=label, unit="usd", source=SOURCE_ENDPOINT,
            fetched_at=now, used=used, status=STATUS_OK,
        )]
    if remaining is None and used is not None:
        remaining = max(0.0, limit - used)
    return [QuotaLimit(
        limit_key="key_limit", label=label, unit="usd", source=SOURCE_ENDPOINT, fetched_at=now,
        used=used if used is not None else (limit - remaining if remaining is not None else None),
        limit=limit, remaining=remaining,
    )]
