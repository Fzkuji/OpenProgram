"""Aging boundary: where "old enough to collapse" starts.

Two properties this module exists to guarantee:

**Ratcheted.** The boundary advances only when a turn commits, never
between the LLM calls inside one turn. A rolling "last N llm nodes"
window recomputed per render would move mid-turn — every tool call adds
an llm node, so the second call of a turn would age content the first
call had seen in full. That rewrites the middle of the prompt, which
kills the KV cache and makes the model's view of history unstable
inside a single reasoning episode. The boundary is therefore computed
once per turn id and cached.

**Replayable.** The boundary a call actually used is stamped on that
call's llm node as ``metadata.render_manifest``. Re-rendering with that
manifest reproduces the exact bytes the model saw, regardless of what
the current global policy constants say. Without it, changing
``TAIL_TURNS`` silently invalidates every historical render.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

# Bump whenever the aging RULE changes (not when a knob is retuned —
# the manifest records the resulting boundary, so a retune replays fine;
# a rule change does not).
POLICY_VERSION = "aging-v1"


# turn_id → aged_before_seq. Bounded by hand: only the current turn is
# ever read, so we keep the last few and drop the rest.
# ponytail: plain dict + FIFO trim, an LRU class would be more machinery
# than the ~8 entries this ever holds.
_BOUNDARY_CACHE: "dict[str, int]" = {}
_CACHE_MAX = 8


def _current_turn_id() -> str:
    try:
        from openprogram.store import _current_turn_id as var
        return var.get() or ""
    except Exception:
        return ""


def aged_before_seq(nodes: list) -> int:
    """Seq below which code nodes age into stubs, ratcheted per turn.

    ``nodes`` are the Calls under consideration, any order. Returns -1
    when nothing should age (the whole conversation fits in the tail
    window), otherwise the seq of the ``TAIL_TURNS``-th-from-last llm
    node — code nodes strictly below it are old.

    Within one turn the answer is memoised, so repeated renders during
    a multi-call turn return the identical boundary.
    """
    turn = _current_turn_id()
    if turn and turn in _BOUNDARY_CACHE:
        return _BOUNDARY_CACHE[turn]

    boundary = _compute_boundary(nodes)

    if turn:
        _BOUNDARY_CACHE[turn] = boundary
        if len(_BOUNDARY_CACHE) > _CACHE_MAX:
            for stale in list(_BOUNDARY_CACHE)[:-_CACHE_MAX]:
                _BOUNDARY_CACHE.pop(stale, None)
    return boundary


def _compute_boundary(nodes: list) -> int:
    try:
        # Module (not from-import) so an env/monkeypatch override of
        # TAIL_TURNS is picked up without a reimport.
        from openprogram.context.tool_aging import policy
    except Exception:
        return -1
    tail = policy.TAIL_TURNS
    llm_seqs = sorted(n.seq for n in nodes if n.is_llm())
    if len(llm_seqs) <= tail:
        return -1
    return llm_seqs[-tail]


def reset_boundary_cache() -> None:
    """Drop memoised boundaries (tests; turn teardown)."""
    _BOUNDARY_CACHE.clear()


def build_manifest(aged_before: int, spilled: list) -> dict:
    """The render_manifest stamped on an llm node at call time."""
    return {
        "policy_version": POLICY_VERSION,
        "aged_before_seq": aged_before,
        "spilled": list(spilled),
    }


# The manifest of the most recent render on this execution context.
# ``render_dag_messages`` publishes here; the code that closes the llm
# node stamps it onto ``metadata.render_manifest``. A ContextVar rather
# than a return value because the render call and the node close sit on
# opposite sides of the provider call, several frames apart, and
# threading a manifest through every signature between them would touch
# code that has no business knowing about aging.
_LAST_MANIFEST: ContextVar = ContextVar(
    "openprogram_last_render_manifest", default=None,
)


def publish_manifest(manifest: dict) -> None:
    _LAST_MANIFEST.set(manifest)


def last_manifest() -> Optional[dict]:
    return _LAST_MANIFEST.get()


def manifest_boundary(manifest: Optional[dict]) -> Optional[int]:
    """``aged_before_seq`` out of a manifest, or None if unusable."""
    if not isinstance(manifest, dict):
        return None
    val = manifest.get("aged_before_seq")
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return int(val)


__all__ = [
    "POLICY_VERSION",
    "aged_before_seq",
    "reset_boundary_cache",
    "build_manifest",
    "manifest_boundary",
    "publish_manifest",
    "last_manifest",
]
