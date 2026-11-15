"""Bounded, read-only discovery of weekly report evidence and dated memory."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json
import os
import re
from itertools import islice
from pathlib import Path

from openprogram.host.sandbox import validate_read_path
from openprogram.state.worktree.path_resolve import resolve_path


def _record(text, week, source, audience="candidate"):
    return {"id": hashlib.sha256((source + text).encode()).hexdigest()[:20],
            "week": week, "text": text, "source": source, "audience": audience}


def memory_candidates(week, query):
    from openprogram.state.memory import is_enabled, store
    from openprogram.state.memory.retrieval import inspect
    from openprogram.state.memory.policy import allowed
    if not is_enabled() or not allowed():
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
        if not isinstance(content, str) or not content.strip() or len(content) >= 1200:
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
        result.append({**_record(content, week, source),
                       "source_dates": [d for d in dates if isinstance(d, str)],
                       "source_date": max(precise),
                       # Topic prose is the writer's distillation, not the owner's own words.
                       "trusted_owner": False})
    if not allowed():
        return []
    return sorted(result, key=lambda item: (item["trusted_owner"], item["source_date"]), reverse=True)


def original_owner(message):
    """Strict original owner attribution; never infer legacy/delegated ownership."""
    from openprogram.state.authority import normalize_authority
    authority = normalize_authority(message)
    return (message.get('role') == 'user' and authority.get('speaker_kind') == 'owner'
            and authority.get('authority_tier') == 'owner' and message.get('purpose') != 'test'
            and not str(message.get('id', '')).startswith('synthetic_'))


def conversation_candidates(week):
    """Read bounded original owner messages with their actual reporting dates.

    These remain candidates: a dated request or plan is not completed work.
    Generated assistant text and runtime-delegated instructions are excluded.
    """
    from zoneinfo import ZoneInfo
    from openprogram.runtime.context.session_db import default_db
    from openprogram.state.store.session.transcript import session_read_violation
    from openprogram.state.memory.policy import allowed, consume_sources

    if not allowed():
        return []
    monday = date.fromisocalendar(int(week[:4]), int(week[6:]), 1)
    sunday = monday + timedelta(days=6)
    db = default_db()
    result, consumed, seen, size = [], [], set(), 0
    for session in db.list_sessions(limit=20):
        sid = session['id']
        if session_read_violation(db, sid):
            continue
        for message in reversed(db.get_messages(sid, limit=200)):
            if not original_owner(message):
                continue
            text = message.get('content')
            if not isinstance(text, str) or not text.strip() or text in seen:
                continue
            try:
                day = datetime.fromtimestamp(float(message['timestamp']), ZoneInfo('Asia/Shanghai')).date()
            except (KeyError, ValueError, TypeError, OverflowError, OSError):
                continue
            if not monday <= day <= sunday:
                continue
            source = 'conversation:' + sid + '#' + str(message['id'])
            item = {**_record(text, week, source), 'source_date':day.isoformat(),
                    'source_dates':[day.isoformat()], 'trusted_owner':True}
            cost = len(json.dumps(item, ensure_ascii=False).encode())
            if size + cost > 18000 or len(result) >= 30:
                continue
            result.append(item)
            consumed.append(message)
            seen.add(text)
            size += cost
    if not allowed():
        return []
    consume_sources(consumed)
    return sorted(result, key=lambda item: item['source_date'], reverse=True)


def expand_memory_candidates(week, queries, existing):
    """One bounded, same-week detail search; preserve anchors and source identities."""
    if (not isinstance(queries, list) or len(queries) > 3
            or any(not isinstance(q, str) or not q.strip() or len(q) > 160 for q in queries)):
        raise ValueError("Expected up to three nonempty detail queries, each at most 160 characters")
    candidates, warnings = list(existing), []
    for query in dict.fromkeys(q.strip() for q in queries):
        try:
            candidates.extend(memory_candidates(week, query))
        except (OSError, ValueError, RuntimeError) as exc:
            warnings.append("Detail memory unavailable: " + str(exc))
    bounded, seen, size = [], set(), 0
    for item in candidates:
        identity = item['id']
        if identity in seen:
            continue
        seen.add(identity)
        cost = len(json.dumps(item, ensure_ascii=False).encode())
        if len(bounded) >= 30 or size + cost > 18000:
            warnings.append("Additional detail exceeds this run's context budget")
            continue
        bounded.append(item)
        size += cost
    return {"materials": [], "candidates": bounded, "warnings": warnings}


def collect(week, report_roots=None, query="腾讯工作 周报 本周进展"):
    """Prefer dated original Tencent evidence; otherwise return review candidates.

    Reads original source JSON; execution checkpoints and derived exports are not evidence.
    Directory and byte limits bound discovery; symlinks are never traversed.
    """
    if not isinstance(week, str) or not re.fullmatch(r"\d{4}-W\d{2}", week):
        raise ValueError("Expected week YYYY-Www")
    date.fromisocalendar(int(week[:4]), int(week[6:]), 1)
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
                entries.sort(key=lambda path: (week not in path.name, path.name))
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
                    if path.name == "checkpoints":
                        continue
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
                if isinstance(data, dict) and data.get("kind") in (
                    "tencent_model", "tencent_sources", "tencent_delivery", "report"
                ):
                    continue
                if path.name == "sources.json" and (path.parent / "summary.md").is_file():
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
                    provenance = row.get("source", "")
                    if isinstance(provenance, str) and (
                        ".json#" in provenance or provenance.startswith(("memory:", "conversation:"))
                    ):
                        # These are our exported evidence references. Query the
                        # original source instead of inheriting its assigned week.
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
            candidates.extend(conversation_candidates(week))
        except (OSError, ValueError, RuntimeError) as exc:
            warnings.append("Conversations unavailable: " + str(exc))
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
            continue
        bounded.append(item)
        size += cost
    return {"materials": bounded if certain else [],
            "candidates": [] if certain else bounded, "warnings": warnings}
