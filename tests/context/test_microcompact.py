"""Microcompactor — idle-gap tool-result clearing.

Fires only after the session has been idle longer than the gap
threshold; an actively running task is never trimmed.
"""

from __future__ import annotations

from openprogram.context.microcompact import Microcompactor

_BIG = "word " * 1200          # ~1500 tokens — well over the 800 floor
_SMALL = "tiny"


def _tr(ts: float, content: str) -> dict:
    """A message carrying one tool_result block."""
    return {
        "role": "user",
        "timestamp": ts,
        "extra": {"blocks": [{"type": "tool_result", "content": content}]},
    }


def test_noop_while_session_is_active():
    now = 2_000_000.0
    hist = [
        {"role": "assistant", "timestamp": now - 30.0},   # 30s ago — active
        _tr(now - 20.0, _BIG),
        _tr(now - 10.0, _BIG),
    ]
    out, n, freed = Microcompactor(keep_recent=1).microcompact(hist, now=now)
    assert (n, freed) == (0, 0)
    assert out is hist


def test_fires_after_idle_gap():
    now = 2_000_000.0
    hist = [
        {"role": "assistant", "timestamp": now - 7200.0},  # 2h ago — idle
        _tr(now - 7100.0, _BIG),    # old — cleared
        _tr(now - 7000.0, _BIG),    # most recent — kept (keep_recent=1)
    ]
    out, n, freed = Microcompactor(keep_recent=1).microcompact(hist, now=now)

    assert n == 1 and freed > 0
    assert "cleared" in out[1]["extra"]["blocks"][0]["content"]
    assert out[2]["extra"]["blocks"][0]["content"] == _BIG     # kept verbatim
    assert hist[1]["extra"]["blocks"][0]["content"] == _BIG    # input unmutated


def test_keep_recent_protects_the_tail():
    now = 2_000_000.0
    hist = [{"role": "assistant", "timestamp": now - 7200.0}]
    hist += [_tr(now - 7000.0 + i, _BIG) for i in range(4)]
    # 4 tool results, keep_recent=2 → the 2 oldest cleared, 2 newest kept.
    out, n, freed = Microcompactor(keep_recent=2).microcompact(hist, now=now)
    assert n == 2
    cleared = ["cleared" in m["extra"]["blocks"][0]["content"] for m in out[1:]]
    assert cleared == [True, True, False, False]


def test_small_results_left_alone():
    now = 2_000_000.0
    hist = [
        {"role": "assistant", "timestamp": now - 7200.0},
        _tr(now - 7100.0, _SMALL),   # old but tiny — not worth clearing
        _tr(now - 7000.0, _BIG),
    ]
    out, n, freed = Microcompactor(keep_recent=1).microcompact(hist, now=now)
    assert (n, freed) == (0, 0)
