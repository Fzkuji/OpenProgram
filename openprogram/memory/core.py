"""``core.md`` — always-on memory block.

Tiny (<2 KB) document injected into every agent's system prompt at
session start. Frozen during a session so the prefix cache survives.

Source under the new schema:

  1. The body of the wiki page named ``Core`` (any folder), or
  2. The body of ``User Preferences`` if no Core page, or
  3. A short folder-tree snippet as last-resort placeholder.

Sleep's deep phase calls :func:`refresh_from_wiki` to rewrite this.
"""
from __future__ import annotations

from pathlib import Path

from . import store
from .schema import today_iso

CORE_BUDGET_CHARS = 2048
CORE_HEADER = "OpenProgram memory (machine-wide)"


def read() -> str:
    path = store.core()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_raw(body: str, *, last_consolidated: str = "") -> Path:
    rule = "═" * 60
    when = last_consolidated or today_iso()
    body = body.strip()
    used = len(body)
    pct = int(round(used / CORE_BUDGET_CHARS * 100))
    head = (
        f"{rule}\n"
        f"{CORE_HEADER} — {pct}% ({used}/{CORE_BUDGET_CHARS} chars), "
        f"last consolidated {when}\n"
        f"{rule}\n\n"
    )
    foot = "\n\n[for full context start with `memory_browse`]\n"
    path = store.core()
    path.write_text(head + body + foot, encoding="utf-8")
    return path


def strip_chrome(text: str) -> str:
    """Drop the rule-line header and the trailing pointer ``write_raw`` adds.

    The header reports a byte count and the footer repeats the
    memory_browse hint that ``system_prompt_block`` already appends —
    neither is content, and counting them against the budget would evict
    real memory in favour of decoration.
    """
    lines = text.strip().splitlines()
    rule = "═" * 10
    # Header: rule / title / rule. Drop through the SECOND rule line.
    rule_positions = [i for i, ln in enumerate(lines[:4]) if ln.startswith(rule)]
    if len(rule_positions) >= 2:
        lines = lines[rule_positions[1] + 1:]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and (not lines[-1].strip()
                     or lines[-1].lstrip().startswith("[for full context")):
        lines.pop()
    return "\n".join(lines).strip()


def truncate_to_budget(body: str, budget: int = CORE_BUDGET_CHARS) -> str:
    """Cut ``body`` down to ``budget`` chars at a section boundary.

    This block is on the system prompt of every single turn, so its size
    is paid on every call — the budget is declared, printed in the file
    header, and now actually enforced. Sections are markdown headings;
    whole headings are kept or dropped so the model never reads half a
    thought. When nothing was dropped the text comes back untouched.
    """
    body = body.strip()
    if len(body) <= budget:
        return body

    # Split on markdown headings, keeping each heading with its body.
    sections: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        if line.startswith("#") and current:
            sections.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current))

    note = "\n\n[trimmed to fit the memory budget — read the rest with `memory_browse`]"
    room = budget - len(note)

    kept: list[str] = []
    used = 0
    for section in sections:
        cost = len(section) + (2 if kept else 0)
        if used + cost > room:
            break
        kept.append(section)
        used += cost

    if not kept:
        # A single oversized section (or no headings at all). Fall back to
        # the next boundary down — paragraph, then sentence — so the block
        # never ends mid-word.
        head = body[:max(0, room)]
        for sep in ("\n\n", ". "):
            cut = head.rfind(sep)
            if cut > room // 2:
                head = head[:cut + len(sep.rstrip())]
                break
        return head.rstrip() + note
    return "\n\n".join(kept).rstrip() + note


def system_prompt_block() -> str:
    raw = truncate_to_budget(strip_chrome(read()))
    has_wiki = any(
        p for p in store.wiki_dir().rglob("*.md")
        if p.name not in store.GOVERNANCE_PAGES
    )
    if not raw and not has_wiki:
        return ""

    pointer = (
        "Memory tools: `memory_browse` (folder tree + recent days), "
        "`memory_get(target)` (read a wiki page by filename or a "
        "`YYYY-MM-DD` journal day), `memory_recall(query)` (FTS "
        "fallback), `memory_reflect(query)` (multi-page synthesis), "
        "`memory_note(...)` (record observation), `memory_ingest` "
        "(manual consolidation), `memory_lint` (health check). "
        "Browse before recalling."
    )
    if raw:
        return raw.rstrip() + "\n\n" + pointer
    return pointer


def refresh_from_wiki() -> Path:
    """Rewrite ``core.md`` from the wiki state.

    Hunts for a top-level Core / User Preferences page; falls back to
    a folder-tree snippet.
    """
    from . import wiki
    from .wiki.helpers import parse_frontmatter

    body = ""
    for name in ("Core", "User Preferences", "User"):
        p = wiki.find(name)
        if p is None:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        _fm, page_body = parse_frontmatter(text)
        body = page_body.strip()
        if body:
            break

    if not body:
        tree = wiki.tree(max_depth=2).strip()
        if tree:
            body = (
                "Top-level topics — use `memory_browse` for the catalog, "
                "`memory_get <Name>` to read a page.\n\n"
                f"```\n{tree}\n```\n"
            )

    return write_raw(body)
