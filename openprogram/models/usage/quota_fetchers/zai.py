"""Z.ai / BigModel coding plan: windows from ``GET /api/monitor/usage/quota/limit``.

Unverified: the shape is Oh My Pi's (``packages/ai/src/usage/zai.ts``) and
ZCode's (``docs/coding-plan-quota-remaining-api.md``): ``data.limits[]`` with
``type``, ``percentage`` (0-100 used), ``nextResetTime`` (ms or s), and the
window as ``unit`` (3 hours, 4 days, 5 months, 6 a week) times ``number``.
The key goes in ``Authorization`` without ``Bearer``.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlsplit

from ..quota import SOURCE_ENDPOINT, QuotaLimit, window_label

PROVIDERS = ("zai",)
VERIFIED = False
ENDPOINT = "GET https://api.z.ai/api/monitor/usage/quota/limit"
SHAPE_FROM = "oh-my-pi packages/ai/src/usage/zai.ts; ZCode docs/coding-plan-quota-remaining-api.md"
DEFAULT_ORIGIN = "https://api.z.ai"

_UNIT_MINUTES = {3: 60, 4: 1440, 5: 43200, 6: 10080}


def _origin(base_url: Optional[str]) -> str:
    if not base_url:
        return DEFAULT_ORIGIN
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else DEFAULT_ORIGIN


def request(cred: Any) -> tuple[str, dict, Optional[str]]:
    origin = _origin(cred.base_url)
    return f"{origin}/api/monitor/usage/quota/limit", {"Authorization": cred.token}, origin


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse(payload: Any, now: float) -> list[QuotaLimit]:
    data = payload.get("data") if isinstance(payload, dict) else None
    items = data.get("limits") if isinstance(data, dict) else None
    out: list[QuotaLimit] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            continue
        percent = _number(item.get("percentage"))
        if percent is None:
            continue
        count = int(_number(item.get("number")) or 1)
        unit = _number(item.get("unit"))
        minutes = _UNIT_MINUTES.get(int(unit)) * count if unit is not None and int(unit) in _UNIT_MINUTES else None
        reset = _number(item.get("nextResetTime"))
        if reset is not None and reset > 1e12:
            reset /= 1000.0
        used = max(0.0, min(100.0, percent))
        kind = "requests" if "TIME" in item["type"].upper() else "tokens"
        label = window_label(minutes)
        out.append(QuotaLimit(
            limit_key=f"{kind}_{minutes or 'quota'}",
            label=label if kind == "tokens" else f"{label} requests",
            unit="percent", source=SOURCE_ENDPOINT, fetched_at=now, used=used, limit=100.0,
            remaining=100.0 - used, used_fraction=used / 100.0, window_minutes=minutes, resets_at=reset,
        ))
    return out
