"""DeepSeek: the account's balance, from ``GET https://api.deepseek.com/user/balance``.

The body is ``{"is_available": bool, "balance_infos": [{"currency": "CNY",
"total_balance": "110.00", "granted_balance": "10.00", "topped_up_balance":
"100.00"}]}``: one entry per currency the account holds, amounts as
strings. ``is_available`` false means the balance cannot pay for a call,
which is the spent state whatever the figure reads (it can be below zero).
"""
from __future__ import annotations

from typing import Any, Optional

from ..quota import SOURCE_ENDPOINT, STATUS_EXHAUSTED, STATUS_OK, QuotaLimit

PROVIDERS = ("deepseek",)
VERIFIED = True
ENDPOINT = "GET https://api.deepseek.com/user/balance"
SHAPE_FROM = "a recorded response (tests/unit/usage/test_quota_fetchers.py)"
URL = "https://api.deepseek.com/user/balance"


def request(cred: Any) -> tuple[str, dict, Optional[str]]:
    return URL, {"Authorization": f"Bearer {cred.token}", "Accept": "application/json"}, URL


def parse(payload: Any, now: float) -> list[QuotaLimit]:
    infos = payload.get("balance_infos") if isinstance(payload, dict) else None
    rows: list[tuple[str, float]] = []
    for info in infos if isinstance(infos, list) else []:
        if not isinstance(info, dict):
            continue
        try:
            amount = float(info.get("total_balance"))
        except (TypeError, ValueError):
            continue
        rows.append((str(info.get("currency") or "usd").lower(), amount))
    status = STATUS_OK if payload.get("is_available") is not False else STATUS_EXHAUSTED
    return [
        QuotaLimit(
            limit_key="balance" if len(rows) == 1 else f"{unit}:balance", label="Balance", unit=unit,
            source=SOURCE_ENDPOINT, fetched_at=now, remaining=amount, status=status,
        )
        for unit, amount in rows
    ]
