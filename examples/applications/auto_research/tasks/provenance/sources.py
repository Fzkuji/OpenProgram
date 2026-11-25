"""The texts a large single insert is compared with, read only from what text_overlap
already has on disk: its cache of cited works' full texts (drafts/text_overlap/cache),
the own papers its last run compared, and workflow/own_papers. No network.
"""

import json
from pathlib import Path

from ..overlap.prose import norm_words


def latest_overlap(paper: Path) -> dict:
    runs = sorted((paper / "drafts" / "text_overlap").glob("*/overlap.json"))
    for path in reversed(runs):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return {}


def load(paper: Path) -> tuple[list[dict], list[str]]:
    """Sources as tasks/overlap's match.find takes them ({id, title, words}), and the
    files that could not be read."""
    record = latest_overlap(paper)
    titles = {s.get("id"): s for s in record.get("sources") or [] if isinstance(s, dict)}
    out, problems = [], []
    cache = paper / "drafts" / "text_overlap" / "cache"
    for path in sorted(cache.glob("*.txt")) if cache.is_dir() else []:
        sid = "arXiv:" + path.stem.replace("_", "/")
        known = titles.get(sid) or {}
        words = norm_words(path.read_text(encoding="utf-8", errors="ignore"))
        if words:
            out.append({"id": sid, "kind": "reference", "title": known.get("title") or sid,
                        "key": known.get("file") or "", "words": words})
    own_paths = []
    for s in titles.values():
        if s.get("kind") == "own" and s.get("file"):
            p = Path(s["file"]).expanduser()
            own_paths.append(p if p.is_absolute() else paper / p)
    try:
        from ..overlap.gather import own_files
        own_paths += own_files(paper, "")
    except Exception as e:  # noqa: BLE001  a broken own_papers folder is named, the run goes on
        problems.append(f"workflow/own_papers: {type(e).__name__}")
    seen = set()
    for path in own_paths:
        path = path.resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            from ..overlap.gather import read_own
            words = read_own(path)
        except Exception as e:  # noqa: BLE001  an unreadable own paper is named, the run goes on
            problems.append(f"{path.name}: {type(e).__name__}")
            continue
        shown = path.relative_to(paper).as_posix() if path.is_relative_to(paper) else str(path)
        if words:
            out.append({"id": f"own:{shown}", "kind": "own", "title": path.stem, "key": "", "words": words})
    return out, problems


__all__ = ["load"]
