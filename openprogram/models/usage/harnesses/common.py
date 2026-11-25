"""Small helpers the readers share."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

PROMPT_CHARS = 160


def parse_ts(value) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def cut_prompt(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= PROMPT_CHARS else text[: PROMPT_CHARS - 1] + "…"


def real_homes(paths: list[Path]) -> list[Path]:
    """Existing folders, each once by its real path."""
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        try:
            real = os.path.realpath(path)
        except OSError:
            continue
        if real in seen or not os.path.isdir(real):
            continue
        seen.add(real)
        out.append(Path(real))
    return out
