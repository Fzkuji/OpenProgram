"""OpenCode Go: windows from ``GET https://opencode.ai/zen/go/v1/usage``.

Unverified: the shape is Oh My Pi's (``packages/ai/src/usage/opencode-go.ts``):
``usage.{rolling,weekly,monthly}`` each with ``percent`` (0-100 used),
``status`` (``ok`` or ``rate-limited``) and ``resetsAt`` (ISO time). The
monthly window follows the subscription date, so it carries no length.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ..quota import SOURCE_ENDPOINT, STATUS_EXHAUSTED, QuotaLimit

PROVIDERS = ("opencode-go",)
VERIFIED = False
ENDPOINT = "GET https://opencode.ai/zen/go/v1/usage"
SHAPE_FROM = "oh-my-pi packages/ai/src/usage/opencode-go.ts"
URL = "https://opencode.ai/zen/go/v1/usage"

_WINDOWS = (("rolling", "primary", "5h", 300), ("weekly", "secondary", "Weekly", 10080),
            ("monthly", "monthly", "Monthly", None))


def request(cred: Any) -> tuple[str, dict, Optional[str]]:
    return URL, {"Authorization": f"Bearer {cred.token}", "Accept": "application/json"}, URL


def parse(payload: Any, now: float) -> list[QuotaLimit]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return []
    out: list[QuotaLimit] = []
    for field, key, label, minutes in _WINDOWS:
        window = usage.get(field)
        if not isinstance(window, dict):
            continue
        percent = window.get("percent")
        if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 <= percent <= 100:
            continue
        try:
            resets = datetime.fromisoformat(str(window.get("resetsAt")).replace("Z", "+00:00")).timestamp()
        except ValueError:
            resets = None
        spent = window.get("status") == "rate-limited"
        out.append(QuotaLimit(
            limit_key=key, label=label, unit="percent", source=SOURCE_ENDPOINT, fetched_at=now,
            used=float(percent), limit=100.0, remaining=100.0 - percent, used_fraction=percent / 100.0,
            window_minutes=minutes, resets_at=resets,
            **({"status": STATUS_EXHAUSTED} if spent else {}),
        ))
    return out
