"""Watchdog timings for CLI-backed runtimes.

Python port of ``references/openclaw/src/agents/cli-watchdog-defaults.ts``
and the ``reliability`` sub-object of ``CliBackendConfig``.

A watchdog fires when a CLI goes silent for longer than the configured
budget. Fresh runs (no prior session) get a generous budget; resumed
runs get a tighter one because a stuck resume usually means broken
state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


CLI_WATCHDOG_MIN_TIMEOUT_MS: int = 1_000
"""Lower bound enforced regardless of config (floor sanity)."""


@dataclass(frozen=True)
class WatchdogTiming:
    """No-output watchdog budget for one lifecycle phase.

    - ``no_output_timeout_ms`` — fixed timeout. Overrides ratio when set.
    - ``no_output_timeout_ratio`` — fraction of the overall call timeout.
    - ``min_ms`` / ``max_ms`` — clamp the computed value.
    """

    no_output_timeout_ms: Optional[int] = None
    no_output_timeout_ratio: Optional[float] = None
    min_ms: Optional[int] = None
    max_ms: Optional[int] = None


@dataclass(frozen=True)
class WatchdogConfig:
    """Fresh-run vs resume-run watchdog settings."""

    fresh: Optional[WatchdogTiming] = None
    resume: Optional[WatchdogTiming] = None


@dataclass(frozen=True)
class ReliabilityConfig:
    """``CliBackendConfig.reliability`` sub-object."""

    watchdog: Optional[WatchdogConfig] = None


# Defaults ported verbatim from openclaw
# (src/agents/cli-watchdog-defaults.ts:3-13).

CLI_FRESH_WATCHDOG_DEFAULTS: WatchdogTiming = WatchdogTiming(
    no_output_timeout_ratio=0.8,
    min_ms=180_000,
    max_ms=600_000,
)
"""Fresh-session default: 80% of overall timeout, clamped to [3m, 10m]."""


CLI_RESUME_WATCHDOG_DEFAULTS: WatchdogTiming = WatchdogTiming(
    no_output_timeout_ratio=0.3,
    min_ms=60_000,
    max_ms=180_000,
)
"""Resume-session default: 30% of overall timeout, clamped to [1m, 3m]."""


__all__ = [
    "CLI_WATCHDOG_MIN_TIMEOUT_MS",
    "CLI_FRESH_WATCHDOG_DEFAULTS",
    "CLI_RESUME_WATCHDOG_DEFAULTS",
    "WatchdogTiming",
    "WatchdogConfig",
    "ReliabilityConfig",
]
