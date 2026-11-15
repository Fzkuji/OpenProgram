"""Central timeout policy for HTTP-based LLM providers.

One source of truth so every provider loosens / tightens consistently and
nobody re-introduces a too-tight read timeout. The philosophy mirrors the
reference frameworks (see ``docs/design/providers/reliability/llm-fault-tolerance.md``):

  * **Bound connection establishment tightly** — a dead proxy/VPN should
    fail fast (connect/write/pool ~30 s), not hang.
  * **Be GENEROUS on the streaming body** — reasoning models pause for
    minutes between tokens, and a tight read timeout false-positives over a
    buffering proxy/VPN. The body read is governed by the SSE idle budget
    (10 min with no byte at all, reset by any byte, keepalives included),
    not a short httpx read.
  * **Bound the wait for each event** — the "no real data" guard is the
    stream idle bound of ``stream_idle.py`` (300 s, as Codex CLI and Claude
    Code): keepalives do not reset it, so a stream that only echoes pings
    ends and is retried. There is no default total-duration ceiling.
  * **Optionally scale the budgets up with prompt size** (hermes pattern):
    a 200k-token request legitimately spends longer in prefill / thinking.

Every value is overridable via env. The ``OPENPROGRAM_SSE_*`` names are
kept (back-compat with the codex provider's original locals).
"""

from __future__ import annotations

import os
from typing import Optional

from .stream_idle import event_idle_timeout_s


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

# --- Streaming body budgets — generous ---------------------------------
# "no bytes AT ALL" (any received line resets it): a dead connection. Codex's
# own client gives the same backends 5 min; 10 min keeps double that for a
# buffering proxy and still notices a dead connection three times sooner than
# the 30 min this used to be (a turn hung that long before its retry).
STREAM_IDLE_TIMEOUT_S: float = _f("OPENPROGRAM_SSE_IDLE_TIMEOUT_S", 600.0)
# "no real data event" (only parsed payloads reset it): the stream idle bound
# every streamed request is read under (stream_idle.py), here for the Codex
# transport, which applies it inside its own retry loop.
_event_idle = event_idle_timeout_s()
STREAM_DATA_STALL_TIMEOUT_S: float = _f(
    "OPENPROGRAM_SSE_DATA_STALL_TIMEOUT_S", _event_idle if _event_idle > 0 else float("inf"),
)
# No default total-duration cap: a healthy stream may keep making progress.
# Existing HTTP/SSE consumers compare numeric deadlines, so infinity disables
# only the total clock while leaving connect/read/idle/cancellation intact.
_total_budget = _f("OPENPROGRAM_SSE_TOTAL_TIMEOUT_S", 0.0)
STREAM_TOTAL_TIMEOUT_S: float = _total_budget if _total_budget > 0 else float("inf")


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
