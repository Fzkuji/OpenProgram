"""REM phase — reflect on themes across the wiki.

Walks every wiki page, optionally runs an LLM pass, and appends a
dated entry to ``wiki/reflections.md``. Never edits topic pages
themselves — that's ingest / survey / refactor.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from .. import store, wiki

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """\
You are the REM-sleep reflection layer for an AI agent's memory wiki.
You receive the full wiki page set and produce a short reflection:

  - Recurring themes across pages
  - Tensions / contradictions between pages
  - Missing cross-references between related pages
  - Topics that have grown enough to deserve refactoring into subtopics

Output 5-15 lines of markdown. No frontmatter, no headers.
Each line is a single observation. Be concrete; cite [[wikilinks]].
"""


def run(*, llm: Callable[[str, str], str] | None = None) -> dict[str, Any]:
    pages = list(wiki.iter_pages())
    if not pages:
        return {"phase": "rem", "skipped": "wiki empty"}

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
    from ..wiki_ops import lint as wiki_lint
    obs: list[str] = [f"- Pages: {len(pages)} total."]
    report = wiki_lint().splitlines()
    obs.extend(f"- {line.lstrip('- ')}" for line in report[2:10] if line.strip())
    return obs


def _wiki_dump(pages: list) -> str:
    lines: list[str] = []
    root = store.wiki_dir()
    for p in pages:
        rel = p.relative_to(root)
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        lines.append(f"## {rel}")
        lines.append(text)
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
