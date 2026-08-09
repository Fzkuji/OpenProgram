"""Read a provider's own spend counter, so a run can report what it cost.

`estimated_cost_usd` multiplies recorded token counts by prices supplied on the
command line. Both inputs are unreliable: cache reads bill far below fresh
input, and two gateways count the same work differently — one conversation
recorded 4.11M input tokens on one provider and 26M on another. Reading the
provider's own cumulative counter before and after a run sidesteps both.

The counter is cumulative and shared by every key on the account, so a delta is
only attributable to this run when nothing else is spending concurrently. It is
reported as an observation, never as a substitute for the provider's invoice.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

# OpenAI-compatible gateways expose the same dashboard path. Each entry is
# (path, key holding the number). Tried in order until one answers.
USAGE_ENDPOINTS = (
    ("/v1/dashboard/billing/usage", "total_usage"),
    ("/api/v1/credits", "total_usage"),
)
TIMEOUT_S = 15.0


@dataclass(frozen=True)
class SpendReading:
    """One observation of a provider's cumulative spend counter."""

    value: float
    endpoint: str
    # Providers report either dollars or cents and rarely say which, so the
    # raw number is kept and interpretation is left to the reader.
    raw: Any = None


def read_spend(base_url: str, api_key: str) -> SpendReading | None:
    """Return the provider's cumulative spend, or None if it does not report one.

    Never raises: a run must not fail because a billing endpoint is missing,
    slow, or shaped differently than expected.
    """
    root = str(base_url or "").rstrip("/")
    # The Anthropic-compatible base may already end in /v1; the dashboard path
    # is relative to the host, not to that suffix.
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    if not root:
        return None
    for path, key in USAGE_ENDPOINTS:
        payload = _get_json(root + path, api_key)
        if not isinstance(payload, dict):
            continue
        value = _extract(payload, key)
        if value is not None:
            return SpendReading(value=value, endpoint=path, raw=payload)
    return None


def _get_json(url: str, api_key: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError, TimeoutError):
        return None


def _extract(payload: dict[str, Any], key: str) -> float | None:
    candidate = payload.get(key)
    if isinstance(candidate, (int, float)):
        return float(candidate)
    # OpenRouter nests the figures one level down.
    data = payload.get("data")
    if isinstance(data, dict):
        total = data.get("total_usage")
        credits, usage = data.get("total_credits"), data.get("total_usage")
        if isinstance(credits, (int, float)) and isinstance(usage, (int, float)):
            return float(usage)
        if isinstance(total, (int, float)):
            return float(total)
    return None


def spend_delta(
    before: SpendReading | None, after: SpendReading | None
) -> dict[str, Any] | None:
    """Summarize what the provider's counter moved by across a run."""
    if before is None or after is None:
        return None
    return {
        "before": before.value,
        "after": after.value,
        "delta": round(after.value - before.value, 6),
        "endpoint": after.endpoint,
        "note": (
            "Provider-reported cumulative spend, in whatever unit that "
            "provider uses. Shared across every key on the account, so the "
            "delta includes any concurrent usage."
        ),
    }
