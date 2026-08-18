"""Central timeout policy for HTTP-based LLM providers.

One source of truth so every provider loosens / tightens consistently and
nobody re-introduces a too-tight read timeout. The philosophy mirrors the
reference frameworks (see ``docs/design/providers/reliability/llm-fault-tolerance.md``):

  * **Bound connection establishment tightly** — a dead proxy/VPN should
    fail fast (connect/write/pool ~30 s), not hang.
  * **Be GENEROUS on the streaming body** — reasoning models pause for
    minutes between tokens. OpenClaw uses a 30-min budget reset on *any*
    byte (its undici ``bodyTimeout``); a tight read timeout false-positives
    over a buffering proxy/VPN. So the body read is effectively governed by
    the SSE idle budget, not a short httpx read.
  * **Keep a separate, generous "no real data" progress guard** (our extra
    over OpenClaw — catches a stream that only echoes pings) and a runaway
    total ceiling.
  * **Optionally scale the budgets up with prompt size** (hermes pattern):
    a 200k-token request legitimately spends longer in prefill / thinking.

Every value is overridable via env. The ``OPENPROGRAM_SSE_*`` names are
kept (back-compat with the codex provider's original locals).
"""

from __future__ import annotations

import os
from typing import Optional


def _f(env: str, default: float) -> float:
    """Read a float from ``env`` (empty/invalid → ``default``)."""
    try:
        return float(os.environ.get(env, "") or default)
    except (TypeError, ValueError):
        return float(default)


# --- Connection establishment — fail fast on a dead VPN/proxy ----------
CONNECT_TIMEOUT_S: float = _f("OPENPROGRAM_HTTPX_CONNECT_TIMEOUT_S", 30.0)
WRITE_TIMEOUT_S: float = _f("OPENPROGRAM_HTTPX_WRITE_TIMEOUT_S", 30.0)
POOL_TIMEOUT_S: float = _f("OPENPROGRAM_HTTPX_POOL_TIMEOUT_S", 30.0)

# --- Streaming body budgets — generous, OpenClaw-aligned ---------------
# "no bytes AT ALL" (any received line resets it) ≈ OpenClaw bodyTimeout.
STREAM_IDLE_TIMEOUT_S: float = _f("OPENPROGRAM_SSE_IDLE_TIMEOUT_S", 1800.0)
# "no real data event" (only parsed payloads reset it) — our extra guard.
STREAM_DATA_STALL_TIMEOUT_S: float = _f("OPENPROGRAM_SSE_DATA_STALL_TIMEOUT_S", 900.0)
# Single-stream runaway backstop. OpenClaw has none; we keep a high one.
STREAM_TOTAL_TIMEOUT_S: float = _f("OPENPROGRAM_SSE_TOTAL_TIMEOUT_S", 7200.0)


def httpx_read_timeout_s() -> float:
    """httpx read-timeout backstop.

    Sits just above :data:`STREAM_IDLE_TIMEOUT_S` so a provider's own SSE
    idle parser fires first (producing a clean, classified error), while
    httpx still bounds the initial header wait (TTFB). Never set this
    *below* the idle budget or it undercuts the parser.
    """
    return _f("OPENPROGRAM_HTTPX_READ_TIMEOUT_S", STREAM_IDLE_TIMEOUT_S + 60.0)


# --- Context-scaled budgets (hermes pattern) --------------------------

def _scale_factor(context_tokens: Optional[int]) -> float:
    """Multiplier for the body budgets based on prompt size.

    Bigger prompts legitimately spend longer in prefill / reasoning, so a
    fixed budget false-positives on them. Mirrors hermes' tiered scaling.
    """
    if not context_tokens or context_tokens <= 0:
        return 1.0
    if context_tokens > 200_000:
        return 2.0
    if context_tokens > 100_000:
        return 1.5
    if context_tokens > 50_000:
        return 1.25
    return 1.0


def idle_timeout_s(context_tokens: Optional[int] = None) -> float:
    """No-bytes-at-all budget, scaled up for large prompts."""
    return STREAM_IDLE_TIMEOUT_S * _scale_factor(context_tokens)


def data_stall_timeout_s(context_tokens: Optional[int] = None) -> float:
    """No-real-data progress guard, scaled up for large prompts."""
    return STREAM_DATA_STALL_TIMEOUT_S * _scale_factor(context_tokens)


def build_httpx_timeout(read: Optional[float] = None):
    """An ``httpx.Timeout`` with connection bounded but read generous.

    Lazy-imports httpx so CLI-only providers don't pay the import cost.
    Pass ``read`` to override the backstop (e.g. a context-scaled value).
    """
    import httpx
    return httpx.Timeout(
        connect=CONNECT_TIMEOUT_S,
        read=read if read is not None else httpx_read_timeout_s(),
        write=WRITE_TIMEOUT_S,
        pool=POOL_TIMEOUT_S,
    )


__all__ = [
    "CONNECT_TIMEOUT_S",
    "WRITE_TIMEOUT_S",
    "POOL_TIMEOUT_S",
    "STREAM_IDLE_TIMEOUT_S",
    "STREAM_DATA_STALL_TIMEOUT_S",
    "STREAM_TOTAL_TIMEOUT_S",
    "httpx_read_timeout_s",
    "idle_timeout_s",
    "data_stall_timeout_s",
    "build_httpx_timeout",
]
