"""REM phase — reflect on themes, contradictions, stale claims.

Scans the wiki, optionally runs an LLM pass, and appends a dated entry
to ``wiki/reflections.md``. Never writes durable claims.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import store, wiki
from ..schema import now_iso

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """\
You are the REM-sleep reflection layer for an AI agent's memory wiki.
You receive the full wiki page set and produce a short reflection entry:

  - Recurring themes across pages
  - Open contradictions between disputed claims
  - Stale claims that haven't been refreshed in a while
  - Missing cross-references between related pages

Output 5–15 lines of markdown. No frontmatter, no headers.
Each line is a single observation. Be concrete; cite slugs.
"""


def run(*, llm: Callable[[str, str], str] | None = None) -> dict[str, Any]:
    pages = list(wiki.all_pages())
    if not pages:
        return {"phase": "rem", "skipped": "wiki empty"}

    # Always emit a deterministic structural section first — works
    # without an LLM and surfaces obvious issues like dangling pages.
    structural = _structural_observations(pages)

    body = ""
    if llm is not None:
        try:
            body = llm(SYSTEM_PROMPT, _wiki_dump(pages)).strip()
        except Exception as e:  # noqa: BLE001
            logger.debug("REM llm call failed: %s", e)
            body = ""

    _append_reflection(structural, body)
    return {
        "phase": "rem",
        "wiki_pages": len(pages),
        "structural_lines": len(structural),
        "llm_used": bool(body),
    }


def _structural_observations(pages: list) -> list[str]:
    """Things we can spot without an LLM."""
    obs: list[str] = []
    disputed = []
    superseded = []
    low_conf = []
    by_kind: dict[str, list] = {}
    for p in pages:
        by_kind.setdefault(p.type, []).append(p)
        for c in p.claims:
            if c.status == "disputed":
                disputed.append(f"{p.type}/{p.id}: {c.text}")
            elif c.status == "superseded":
                superseded.append(f"{p.type}/{p.id}: {c.text}")
            elif c.confidence < 0.4:
                low_conf.append(f"{p.type}/{p.id} ({c.confidence}): {c.text}")
    counts = ", ".join(f"{k}:{len(v)}" for k, v in by_kind.items())
    obs.append(f"- Pages: {counts}, total {len(pages)}.")
    if disputed:
        obs.append(f"- {len(disputed)} disputed claim(s) need resolution:")
        for d in disputed[:5]:
            obs.append(f"  - {d}")
    if superseded:
        obs.append(f"- {len(superseded)} superseded claim(s) — consider archiving.")
    if low_conf:
        obs.append(f"- {len(low_conf)} low-confidence claim(s) (<0.4):")
        for d in low_conf[:5]:
            obs.append(f"  - {d}")
    return obs


def _wiki_dump(pages: list) -> str:
    lines = []
    for p in pages:
        lines.append(f"## {p.type}/{p.id} — {p.title} (conf={p.confidence})")
        for cl in p.claims:
            lines.append(f"- ({cl.status}, {cl.confidence}) {cl.text}")
        lines.append("")
    return "\n".join(lines)


def _append_reflection(structural: list[str], llm_body: str) -> None:
    path = store.wiki_reflections()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if not path.exists():
        path.write_text("# Reflections\n\n", encoding="utf-8")
    block = [f"## {timestamp}", ""]
    block.extend(structural)
    if llm_body:
        block.append("")
        block.append(llm_body)
    block.append("")
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(block) + "\n")
