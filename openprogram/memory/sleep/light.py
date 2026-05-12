"""Light phase — dedupe and stage candidates.

Reads every journal entry within ``max_age_days``, groups
near-duplicate texts by normalized form, computes per-group scores,
and writes a candidate list to ``.state/sleep-stage.json``.

No wiki writes happen in this phase.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import store, journal
from ..schema import JournalEntry
from .scoring import (
    CandidateScore,
    THRESHOLDS,
    WEIGHTS,
    conceptual_richness,
    consolidation_factor,
    recency_factor,
)


STAGE_FILE = "sleep-stage.json"


def _normalize(text: str) -> str:
    """Aggressive normalize for grouping: lowercase, strip punct, collapse spaces."""
    s = text.lower().strip()
    s = re.sub(r"[\W_]+", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _read_recall_counts() -> dict[str, dict[str, Any]]:
    """Recall-count store: { normalized_text: { "count": n, "queries": [...] } }.

    Maintained by ``builtin.recall.recall_for_prompt`` callsites
    (incremented when an entry shows up in a hit). We don't populate
    it yet — sleep falls back to text-frequency only when empty.
    """
    p = store.recall_counts_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def run() -> dict[str, Any]:
    """Score and stage candidates. Returns a small report."""
    max_age_days = THRESHOLDS["max_age_days"]
    half_life = THRESHOLDS["recency_half_life_days"]
    now = datetime.now(timezone.utc)

    counts = _read_recall_counts()
    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "entries": [],
        "days": set(),
        "tags": set(),
        "type": "",
        "max_confidence": 0.0,
    })

    pruned_old = 0
    for date_iso, entry in journal.all_entries():
        try:
            day = datetime.strptime(date_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        age = (now - day).total_seconds() / 86400.0
        if age > max_age_days:
            pruned_old += 1
            continue
        key = _normalize(entry.text)
        if not key:
            continue
        g = groups[key]
        g["entries"].append({
            "date": date_iso,
            "text": entry.text,
            "type": entry.type,
            "confidence": entry.confidence,
            "tags": entry.tags,
            "session": entry.session_id,
        })
        g["days"].add(date_iso)
        g["tags"].update(entry.tags)
        if entry.type and entry.type != "observation":
            g["type"] = entry.type
        elif not g["type"]:
            g["type"] = entry.type
        g["max_confidence"] = max(g["max_confidence"], entry.confidence)

    candidates: list[dict[str, Any]] = []
    for key, g in groups.items():
        n = len(g["entries"])
        if n == 0:
            continue
        n_days = len(g["days"])
        recall_info = counts.get(key, {})
        recall_count = int(recall_info.get("count", 0))
        distinct_queries = len(set(recall_info.get("queries") or []))

        # Pick the most recent entry's age for recency factor.
        most_recent_iso = max(g["days"])
        most_recent_day = datetime.strptime(most_recent_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        age_days = (now - most_recent_day).total_seconds() / 86400.0

        # Compute weighted score (each component 0–1).
        f = min(1.0, n / 5.0)                    # frequency: saturates at 5 occurrences
        r = min(1.0, recall_count / 5.0)         # recall_count saturation
        q = min(1.0, distinct_queries / 3.0)     # query_diversity saturation
        rec = recency_factor(age_days, half_life)
        cons = consolidation_factor(n_days)
        conc = conceptual_richness(g["type"], list(g["tags"]))

        score = (
            WEIGHTS["frequency"] * f
            + WEIGHTS["recall_count"] * r
            + WEIGHTS["query_diversity"] * q
            + WEIGHTS["recency"] * rec
            + WEIGHTS["consolidation"] * cons
            + WEIGHTS["conceptual"] * conc
        )
        # Boost by confidence so well-attested facts edge ahead.
        score *= 0.5 + g["max_confidence"]

        candidates.append({
            "key": key,
            "text": _representative(g["entries"]),
            "score": round(score, 4),
            "type": g["type"] or "fact",
            "tags": sorted(g["tags"]),
            "n_occurrences": n,
            "n_distinct_days": n_days,
            "recall_count": recall_count,
            "distinct_queries": distinct_queries,
            "age_days": round(age_days, 2),
            "max_confidence": round(g["max_confidence"], 3),
            "sources": [
                f"journal/{e['date']}.md" for e in sorted(g["entries"], key=lambda x: x["date"])
            ],
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)

    stage_path = store.state_dir() / STAGE_FILE
    stage_path.write_text(
        json.dumps({
            "generated_at": now.isoformat(),
            "weights": WEIGHTS,
            "thresholds": THRESHOLDS,
            "pruned_old": pruned_old,
            "candidates": candidates,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "phase": "light",
        "candidates": len(candidates),
        "above_threshold": sum(1 for c in candidates if c["score"] >= THRESHOLDS["min_score"]),
        "pruned_old": pruned_old,
        "stage_file": str(stage_path),
    }


def read_stage() -> dict[str, Any]:
    p = store.state_dir() / STAGE_FILE
    if not p.exists():
        return {"candidates": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"candidates": []}


def _representative(entries: list[dict[str, Any]]) -> str:
    """Pick a representative text from a duplicate group.

    Heuristic: the longest text (more informative), with a tiebreak to
    the most recent.
    """
    sorted_entries = sorted(
        entries, key=lambda e: (-len(e["text"]), e["date"]), reverse=False,
    )
    return sorted_entries[0]["text"]
