"""Short-term schema + helpers.

The wiki side moved to an Obsidian-style folder vault with minimal
frontmatter handled by ``wiki_helpers.py``. The ``WikiPage`` /
``Claim`` dataclasses that used to live here are gone — what remains
is just what the short-term log needs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ShortTermEntry:
    timestamp: str                   # ISO 8601 or HH:MM
    text: str
    type: str = "observation"
    tags: list[str] = field(default_factory=list)
    session_id: str = ""
    confidence: float = 0.5


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# Short-term file format
#
# ## HH:MM — type: <kind>, tags: [a, b]
# <body text>
# <!-- session: <sid>, confidence: 0.8 -->


_ENTRY_HEADER_RE = re.compile(
    r"^##\s+(?P<time>\d{2}:\d{2})\s+—\s+type:\s+(?P<type>[\w-]+)"
    r"(?:,\s+tags:\s+\[(?P<tags>[^\]]*)\])?$"
)
_META_RE = re.compile(
    r"<!--\s*session:\s*(?P<session>[^,]+?),\s*confidence:\s*(?P<conf>[\d.]+)\s*-->"
)


def render_short_term_entry(e: ShortTermEntry) -> str:
    time = e.timestamp.split("T", 1)[-1][:5] if "T" in e.timestamp else e.timestamp[:5]
    header = f"## {time} — type: {e.type}"
    if e.tags:
        header += f", tags: [{', '.join(e.tags)}]"
    meta = f"<!-- session: {e.session_id or '-'}, confidence: {e.confidence:.2f} -->"
    return f"{header}\n{e.text.strip()}\n{meta}\n"


def parse_short_term_file(text: str) -> list[ShortTermEntry]:
    entries: list[ShortTermEntry] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _ENTRY_HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue
        time = m.group("time")
        kind = m.group("type")
        raw_tags = m.group("tags") or ""
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()]

        body_lines: list[str] = []
        session_id = ""
        confidence = 0.5
        i += 1
        while i < len(lines) and not _ENTRY_HEADER_RE.match(lines[i]):
            line = lines[i]
            meta = _META_RE.search(line)
            if meta:
                session_id = meta.group("session").strip()
                try:
                    confidence = float(meta.group("conf"))
                except ValueError:
                    confidence = 0.5
            else:
                body_lines.append(line)
            i += 1
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        entries.append(ShortTermEntry(
            timestamp=time, text=body, type=kind, tags=tags,
            session_id=session_id, confidence=confidence,
        ))
    return entries
