"""OpenAI Codex (ChatGPT sign-in): plan windows from ``GET {backend}/wham/usage``.

Unverified: the shape is Oh My Pi's (``packages/ai/src/usage/openai-codex.ts``:
``rate_limit.{primary,secondary}_window.{used_percent, limit_window_seconds,
reset_at, reset_after_seconds}``, ``additional_rate_limits[]``,
``credits.{has_credits, unlimited, balance}``), and the Codex CLI calls the
same path (``codex-rs/backend-client``). Every Codex reply already carries
these windows in its headers or its ``codex.rate_limits`` frame, so this read
only matters before the first reply of a session.
"""
from __future__ import annotations

from typing import Any, Optional

from openprogram.models.providers.utils.rate_limit import CodexCredits, CodexRateLimits, CodexWindow

from ..quota import SOURCE_ENDPOINT, QuotaLimit, codex_limits

PROVIDERS = ("openai-codex",)
VERIFIED = False
ENDPOINT = "GET https://chatgpt.com/backend-api/wham/usage"
SHAPE_FROM = "oh-my-pi packages/ai/src/usage/openai-codex.ts"
DEFAULT_BASE = "https://chatgpt.com/backend-api"


def request(cred: Any) -> tuple[str, dict, Optional[str]]:
    base = (cred.base_url or DEFAULT_BASE).rstrip("/")
    headers = {"Authorization": f"Bearer {cred.token}"}
    try:
        from openprogram.models.providers.adapters.openai_codex.oauth import _get_account_id_from_jwt
        account = _get_account_id_from_jwt(cred.token)
    except Exception:
        account = None
    if account:
        headers["ChatGPT-Account-Id"] = account
    return f"{base}/wham/usage", headers, base


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _window(value: Any, now: float) -> Optional[CodexWindow]:
    if not isinstance(value, dict):
        return None
    used = _number(value.get("used_percent"))
    if used is None:
        return None
    seconds = _number(value.get("limit_window_seconds"))
    reset = _number(value.get("reset_at"))
    if reset is None and _number(value.get("reset_after_seconds")) is not None:
        reset = now + float(value["reset_after_seconds"])
    return CodexWindow(
        used_percent=used,
        window_minutes=int(seconds // 60) if seconds else None,
        resets_at=int(reset) if reset else None,
    )


def _family(limit_id: str, rate_limit: Any, now: float, name: Optional[str] = None) -> CodexRateLimits:
    rate_limit = rate_limit if isinstance(rate_limit, dict) else {}
    return CodexRateLimits(
        limit_id=limit_id, limit_name=name,
        primary=_window(rate_limit.get("primary_window"), now),
        secondary=_window(rate_limit.get("secondary_window"), now),
    )


def parse(payload: Any, now: float) -> list[QuotaLimit]:
    if not isinstance(payload, dict):
        return []
    main = _family("codex", payload.get("rate_limit"), now)
    credits = payload.get("credits")
    if isinstance(credits, dict) and isinstance(credits.get("has_credits"), bool) \
            and isinstance(credits.get("unlimited"), bool):
        balance = credits.get("balance")
        main.credits = CodexCredits(
            has_credits=credits["has_credits"], unlimited=credits["unlimited"],
            balance=None if balance in (None, "") else str(balance),
        )
    families = [main]
    for extra in payload.get("additional_rate_limits") or []:
        if not isinstance(extra, dict):
            continue
        name = extra.get("limit_name") or extra.get("metered_feature")
        if not isinstance(name, str) or not name.strip():
            continue
        limit_id = name.strip().lower().replace("-", "_").replace(" ", "_")
        families.append(_family(limit_id, extra.get("rate_limit"), now, name.strip()))
    return codex_limits([f for f in families if f.present], source=SOURCE_ENDPOINT, now=now)
