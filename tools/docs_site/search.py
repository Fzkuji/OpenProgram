"""Build the client-side search index: one record per page (title + plain text)."""

from __future__ import annotations

import json
import re
from pathlib import Path

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def plain_text(html: str, limit: int = 2000) -> str:
    text = _TAG_RE.sub(" ", html)
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " "))
    text = _WS_RE.sub(" ", text).strip()
    return text[:limit]


def write_index(records: list[dict], out_dir: Path) -> None:
    (out_dir / "search-index.json").write_text(
        json.dumps(records, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
