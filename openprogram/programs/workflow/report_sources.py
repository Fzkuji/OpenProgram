"""Bounded, read-only discovery of weekly report evidence and dated memory."""
from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
import os
import re
from itertools import islice
from pathlib import Path

from openprogram.sandbox import validate_read_path
from openprogram.worktree.path_resolve import resolve_path


def _record(text, week, source, audience="candidate"):
    return {"id": hashlib.sha256((source + text).encode()).hexdigest()[:20],
            "week": week, "text": text, "source": source, "audience": audience}


def memory_candidates(week, query):
    from openprogram.memory import is_enabled, store
    from openprogram.memory.retrieval import inspect
    if not is_enabled():
        return []
    root = store.root()
    violation = validate_read_path(str(root))
    if violation:
        raise OSError("Memory read denied: " + violation)
    monday = date.fromisocalendar(int(week[:4]), int(week[6:]), 1)
    found = inspect.search(root, query, method="bm25", top_k=10,
                           date_from=monday.isoformat(),
                           date_to=(monday + timedelta(days=6)).isoformat())
    result = []
    for hit in found.get("results", []):
        content = hit.get("content")
        if (hit.get("trust_state") == "pending" or not isinstance(content, str)
                or not content.strip() or len(content) >= 1200):
            continue
        if str(hit.get("path", "")).startswith("sources/") and hit.get("speaker_trusted") is not True:
            continue
        dates = hit.get("dates", [])
        if not isinstance(dates, list):
            dates = []
        dates = [*dates, hit.get("date")]
        precise = []
        for stamp in dates:
            if not isinstance(stamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp):
                continue
            try:
                day = date.fromisoformat(stamp)
            except ValueError:
                continue
            if monday <= day <= monday + timedelta(days=6):
                precise.append(stamp)
        if not precise:
            continue
        source = "memory:" + str(hit.get("path", "")) + "#" + str(hit.get("event_id", ""))
        result.append({**_record(content, week, source), "source_dates": [d for d in dates if isinstance(d, str)]})
    return result


def collect(week, report_roots=None, query="腾讯工作 周报 本周进展"):
    """Prefer dated original Tencent evidence; otherwise return review candidates.

    Reads source/checkpoint JSON, never generated summary.md as original evidence.
    Directory and byte limits bound discovery; symlinks are never traversed.
    """
    roots = ["reports"] if report_roots is None else report_roots
    if not isinstance(roots, list) or not 1 <= len(roots) <= 5 or any(not isinstance(p, str) for p in roots):
        raise ValueError("report_roots must contain 1–5 paths")
    certain, candidates, warnings, seen = [], [], [], set()
    scanned = 0
    for raw in roots:
        root = Path(resolve_path(raw)[0]).absolute()
        violation = validate_read_path(str(root))
        if violation:
            warnings.append("Report root not readable: " + str(root))
            continue
        pending = [(root, 0)]
        while pending and scanned < 200:
            directory, depth = pending.pop()
            if directory.is_symlink() or not directory.is_dir():
                continue
            try:
                with os.scandir(directory) as listing:
                    entries = [Path(entry.path) for entry in islice(listing, 200 - scanned)]
            except OSError as exc:
                warnings.append(str(exc))
                continue
            for path in entries:
                if scanned >= 200:
                    break
                scanned += 1
                if path.is_symlink() or validate_read_path(str(path)):
                    continue
                if path.is_dir() and depth < 4:
                    pending.append((path, depth + 1))
                    continue
                if path.suffix != ".json" or not path.is_file():
                    continue
                try:
                    with path.open("rb") as handle:
                        raw_data = handle.read(262145)
                    if len(raw_data) > 262144:
                        continue
                    data = json.loads(raw_data)
                except (OSError, ValueError, UnicodeError):
                    continue
                request = data.get("request", data) if isinstance(data, dict) else {}
                if not isinstance(request, dict):
                    continue
                rows = data if isinstance(data, list) else request.get("materials", [])
                if not isinstance(rows, list):
                    continue
                tencent = isinstance(data, dict) and str(data.get("kind", "")).startswith("tencent_")
                for row in rows[:30]:
                    if not isinstance(row, dict) or row.get("week") != week:
                        continue
                    text = row.get("text")
                    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 18000:
                        continue
                    audience = row.get("audience")
                    category = "tencent" if audience == "tencent" or (tencent and audience is None) else "candidate"
                    if audience not in (None, "tencent", "personal") or (text, category) in seen:
                        continue
                    # Explicit test records cannot become user work evidence.
                    if row.get("purpose") == "test" or str(row.get("id", "")).startswith("synthetic_"):
                        continue
                    seen.add((text, category))
                    item = _record(text, week, str(path) + "#" + str(row.get("id", "")))
                    if audience == "tencent" or (tencent and audience in (None, "tencent")):
                        item["audience"] = "tencent"
                        certain.append(item)
                    elif audience in (None, "personal"):
                        candidates.append(item)
    if not certain:
        try:
            candidates.extend(memory_candidates(week, query))
        except (OSError, ValueError, RuntimeError) as exc:
            warnings.append("Memory unavailable: " + str(exc))
    selected = certain if certain else candidates
    bounded, size = [], 0
    for item in selected:
        cost = len(json.dumps(item, ensure_ascii=False).encode())
        if size + cost > 18000 or len(bounded) >= 30:
            warnings.append("Additional source records exceed this run's context budget")
            break
        bounded.append(item)
        size += cost
    return {"materials": bounded if certain else [],
            "candidates": [] if certain else bounded, "warnings": warnings}
