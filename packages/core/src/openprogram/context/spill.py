"""Large-node spilling — a WRITE-path concern.

A node whose text blows past the render cap gets its full text written
once to the session's ``large_nodes/`` dir at the moment it is recorded,
and the path stamped on ``metadata.spilled``. Rendering then only reads
that stamp and cites the file.

Why the write path: spilling used to happen inside the renderer, which
made "build the prompt" a side-effecting operation. Two renders of the
same history wrote files twice, a replay of an old turn could mint new
files, and a read-only consumer (the Context tab, a token count) mutated
the session directory just by looking. Recording is the one moment a
node's text is genuinely new, so it is the one place the spill belongs.

The spill file is plain multi-line text, not the node's history JSON:
JSON is one minified line, so ``read``'s line-based offset/limit can't
index into it. A real .txt lets the marker cite an exact line range and
the ``read()`` call that fetches the elided middle.
"""
from __future__ import annotations

import os
from typing import Any, Optional

# How big any ONE node's rendered text may be. Even a few nodes overflow
# the prompt if ONE carries a huge payload (a web_search dump, a giant
# return value). Set high (32k chars) so normal nodes pass through
# untouched — this only fires on abnormally large nodes. Env-overridable.
NODE_RENDER_CAP = int(os.environ.get("OPENPROGRAM_NODE_RENDER_CAP", "32000"))

# Spilling on/off. Off ⇒ nothing is written to large_nodes/ and an
# over-cap node falls back to plain char truncation with a generic
# pointer — i.e. the model loses the "read the elided middle by line
# number" affordance. That contrast is the point of the ablation.
SPILL_ENABLED = os.environ.get(
    "OPENPROGRAM_NODE_SPILL", "on",
).strip().lower() not in {"0", "off", "false", "no", ""}

HEAD_LINES = 60   # lines kept from the top of an over-cap node
TAIL_LINES = 40   # lines kept from the bottom


def large_dir_for(history_dir: Optional[str]) -> Optional[str]:
    """Sibling ``large_nodes/`` dir next to a session's ``history/`` dir."""
    if not history_dir:
        return None
    return os.path.join(os.path.dirname(history_dir.rstrip("/")), "large_nodes")


def spill_if_large(text: Any, *, node_key: str,
                   large_dir: Optional[str]) -> Optional[dict]:
    """Write ``text`` to ``large_dir`` when it is over cap.

    Returns the ``spilled`` stamp for ``node.metadata`` — ``{"path",
    "total_lines", "total_chars"}`` — or None when the node is under cap,
    is too few lines to usefully elide, or no spill dir is known.
    """
    # Module-level lookup so a monkeypatched flag takes effect.
    import openprogram.context.spill as _self
    if not _self.SPILL_ENABLED:
        return None
    if not isinstance(text, str) or len(text) <= _self.NODE_RENDER_CAP:
        return None
    if not large_dir or not node_key:
        return None
    lines = text.splitlines()
    if len(lines) <= (HEAD_LINES + TAIL_LINES):
        return None
    try:
        os.makedirs(large_dir, exist_ok=True)
        path = os.path.join(large_dir, f"{node_key}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        return None
    return {"path": path, "total_lines": len(lines), "total_chars": len(text)}


def elide_spilled(text: str, spilled: Optional[dict]) -> str:
    """Render-side: head + pointer + tail for a node that was spilled.

    No spill stamp → the text is returned unchanged. A stamp whose text
    no longer exceeds the cap (cap raised since) also passes through, so
    raising the cap widens what the model sees without a rewrite.
    """
    import openprogram.context.spill as _self
    if len(text) <= _self.NODE_RENDER_CAP:
        return text
    if not isinstance(spilled, dict):
        # No spill file (spilling off, or the write failed): the node is
        # still over cap and must not go to the model whole.
        return _char_elide(text)
    path = spilled.get("path")
    lines = text.splitlines()
    if not path or len(lines) <= (HEAD_LINES + TAIL_LINES):
        return _char_elide(text)
    total = len(lines)
    head = "\n".join(lines[:HEAD_LINES])
    tail = "\n".join(lines[-TAIL_LINES:])
    mid_start = HEAD_LINES + 1        # 1-based first elided line
    mid_end = total - TAIL_LINES      # 1-based last elided line
    n_mid = mid_end - mid_start + 1
    return (
        f"{head}\n\n"
        f"[... lines {mid_start}-{mid_end} ({n_mid:,} lines) of this "
        f"node elided. Full text saved to {path} ({total:,} lines). "
        f"To read the elided middle: "
        f'read("{path}", offset={mid_start}, limit={n_mid}). ...]\n\n'
        f"{tail}"
    )


def _char_elide(text: str, cap: Optional[int] = None) -> str:
    """Fallback when there's no usable spill file: char-level head/tail."""
    import openprogram.context.spill as _self
    if cap is None:
        cap = _self.NODE_RENDER_CAP
    head_c = int(cap * 0.6)
    tail_c = cap - head_c
    elided = len(text) - head_c - tail_c
    return (
        text[:head_c]
        + f"\n\n[... {elided:,} chars elided of {len(text):,} total in this "
        f"node; full artifacts are saved as files in the working directory "
        f"— read those for complete content. ...]\n\n"
        + text[-tail_c:]
    )


__all__ = [
    "NODE_RENDER_CAP",
    "SPILL_ENABLED",
    "large_dir_for",
    "spill_if_large",
    "elide_spilled",
]
