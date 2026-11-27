"""Chat attachments: the one marker lexicon, read and written in both directions.

Every file that travels through a chat message is referenced by an
absolute path in a single marker::

    [attachment: <name> (<ext>, <N> KB[, <count>]) @json "<abs path>"]

The JSON string makes every legal path delimiter and trailing space
reversible. The historical unquoted ``@ <path>`` spelling remains readable.
The current spelling is written by all four producers — browser upload,
``@``-mention, inbound channel attachment, and the agent's own
``send_file`` — and read by all four consumers: the web chip parser
(``apps/web/components/chat/messages/attachments/user-attachments.tsx``), the sidebar
title stripper, the channel delivery step, and this module. Adding a
fifth producer means calling :func:`format_marker`, not inventing a
fifth spelling. Divergence here is not cosmetic: a marker the chip
regex misses is rendered to the user as raw prose.

Which directories an attachment may come from is
:mod:`openprogram.state.attachments`.
"""
from __future__ import annotations

import json
import os
import re
from typing import Iterable


#: Names/paths are interpolated into a bracketed marker, so a literal
#: bracket or paren inside a filename would truncate it at parse time.
_MARKER_UNSAFE = re.compile(r"[\[\]()]+")

#: Current marker uses a JSON string so every absolute-path character,
#: including ``]``, quotes, backslashes, and trailing spaces, round-trips.
JSON_MENTION_RE = re.compile(
    r"\[attach(?:ed|ment):\s*([^()\[\]]+?)\s*"
    r"\(([^,)]+),\s*([\d.]+)\s*KB(?:,\s*([^)]+))?\)"
    r"\s*@json\s*(\"(?:\\.|[^\"\\])*\")"
    r"(?:\s*@previewjson\s*(\"(?:\\.|[^\"\\])*\"))?\]"
)
#: Historical unquoted marker, retained for stored conversations.
MENTION_RE = re.compile(
    r"\[attach(?:ed|ment):\s*([^()\[\]]+?)\s*"
    r"\(([^,)]+),\s*([\d.]+)\s*KB(?:,\s*([^)]+))?\)"
    r"\s*@(?!json\s)\s*([^\]]+)\]"
)


def safe_marker_text(value: str) -> str:
    """Strip the characters that would truncate a marker at parse time."""
    return _MARKER_UNSAFE.sub("_", (value or "").strip()) or "file"


def kb_of(size_bytes: int) -> int:
    """Size in KB as the marker spells it — matching the web composer's
    ``Math.max(1, Math.round(bytes / 1024))`` so an upload's optimistic
    chip and the stored marker agree."""
    return max(1, round((size_bytes or 0) / 1024))


def ext_of(name: str, mime: str = "") -> str:
    """The marker's type word: the filename extension, else the mime
    subtype, else ``file``."""
    ext = os.path.splitext(name or "")[1].lstrip(".").lower()
    if ext:
        return safe_marker_text(ext)
    sub = (mime or "").partition(";")[0].rpartition("/")[2].strip().lower()
    return safe_marker_text(sub) if sub else "file"


def format_marker(
    name: str, path: str | os.PathLike, size_bytes: int,
    mime: str = "", count: str = "",
    preview_path: str | os.PathLike | None = None,
) -> str:
    """One attachment marker. ``count`` is the optional scope badge
    ("500 pages" / "4210 lines") the web chip shows as a third field."""
    safe_name = safe_marker_text(name or os.path.basename(str(path)))
    extra = f", {count}" if count else ""
    encoded_path = json.dumps(str(path), ensure_ascii=False)
    preview = (f" @previewjson {json.dumps(str(preview_path), ensure_ascii=False)}"
               if preview_path is not None else "")
    return (f"[attachment: {safe_name} "
            f"({ext_of(safe_name, mime)}, {kb_of(size_bytes)} KB{extra})"
            f" @json {encoded_path}{preview}]")


def find_markers(text: str) -> list[tuple[str, str, str]]:
    """``[(whole marker, display name, absolute path), …]`` in ``text``."""
    found: list[tuple[int, str, str, str]] = []
    for match in JSON_MENTION_RE.finditer(text or ""):
        try:
            path = json.loads(match.group(5))
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(path, str):
            found.append((match.start(), match.group(0),
                          match.group(1).strip(), path))
    for match in MENTION_RE.finditer(text or ""):
        found.append((match.start(), match.group(0), match.group(1).strip(),
                      match.group(5).strip()))
    found.sort(key=lambda item: item[0])
    return [(whole, name, path) for _, whole, name, path in found]


def _marker_matches(text: str) -> list[re.Match]:
    """Every path-bearing marker in ``text``, in order, none overlapping.

    ``@json"/p"`` (no space) is both forms at once; the current one wins,
    so the span is replaced once and the text after it survives.
    """
    matches = sorted([*JSON_MENTION_RE.finditer(text), *MENTION_RE.finditer(text)],
                     key=lambda m: (m.start(), m.re is not JSON_MENTION_RE))
    kept: list[re.Match] = []
    for match in matches:
        if not kept or match.start() >= kept[-1].end():
            kept.append(match)
    return kept


def markers_as_names(text: str, keep: Iterable[str] = ()) -> str:
    """``text`` with each marker whose file is not in ``keep`` replaced by
    the file's name as plain text: the words stay, the card and the path go.

    A marker stays only when every path it names is kept: the one it
    attaches, and a ``@previewjson`` path, which is the file its card
    opens. One whose path does not decode names nothing and goes too.
    """
    kept = set(keep)
    text = text or ""
    for match in reversed(_marker_matches(text)):
        if match.re is JSON_MENTION_RE:
            try:
                paths = [json.loads(g) for g in match.groups()[4:6] if g is not None]
            except (TypeError, json.JSONDecodeError):
                paths = []
        else:
            paths = [match.group(5).strip()]
        if paths and all(isinstance(p, str) and p in kept for p in paths):
            continue
        text = text[:match.start()] + match.group(1).strip() + text[match.end():]
    return text


def strip_markers(text: str) -> str:
    """Remove current and historical path-bearing attachment markers."""
    text = text or ""
    for match in reversed(_marker_matches(text)):
        text = text[:match.start()] + text[match.end():]
    return text
