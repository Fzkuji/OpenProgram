"""Merge a BM25 ranking and an embedding ranking into one hybrid ranking.

Each ranking becomes Reciprocal Rank Fusion scores, ``1 / (k + rank)``, so a
raw BM25 score and a cosine similarity are never added together. The two
are then blended with a weight, and a Topic that several hits come from has
its best hit lifted: several paragraphs matching in one file is evidence
that the file is the right subject.
"""

from __future__ import annotations

import re
from typing import Any

from ..management.config import HYBRID_WEIGHTINGS

RRF_K = 60
# Each backend is asked for this many times the requested hits, so the
# union the fusion ranks is wide enough to reorder.
CANDIDATE_MULTIPLIER = 5
# The best hit of the most-matched file gains up to this share of the top
# fused score; other files gain in proportion to their own matches.
FILE_BOOST_SHARE = 0.2

# A date, a quoted phrase, or a capitalised name after a lowercase word:
# such a query wants the exact words, so keywords count for more.
_LITERAL_QUERY = re.compile(
    r"\b\d{4}(?:-\d{2}(?:-\d{2})?)?\b"
    r"|[\"'“‘「『].+?[\"'”’」』]"
    r"|(?<=[a-z] )[A-Z][a-z]+"
)


def embedding_weight(query: str, weighting: str = "auto") -> float:
    """Share of the fused score that the embedding ranking carries."""
    fixed = HYBRID_WEIGHTINGS.get(weighting)
    if fixed is not None:
        return fixed
    return 0.3 if _LITERAL_QUERY.search(query) else 0.5


def _key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row.get("path"), row.get("line"), row.get("event_id"))


def _rrf(rows: list[dict[str, Any]]) -> dict[tuple[Any, ...], float]:
    scores: dict[tuple[Any, ...], float] = {}
    for rank, row in enumerate(rows, start=1):
        scores.setdefault(_key(row), 1.0 / (RRF_K + rank))
    return scores


def fuse(
    lexical: list[dict[str, Any]],
    semantic: list[dict[str, Any]],
    *,
    embedding_share: float = 0.5,
    file_boost: bool = True,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    """Rank the union of both lists; a hit in only one keeps that rank."""
    lexical_rrf = _rrf(lexical)
    semantic_rrf = _rrf(semantic)
    rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in semantic + lexical:  # BM25 rows carry richer fields; they win
        rows.setdefault(_key(row), {}).update(row)
    scores = {
        key: embedding_share * semantic_rrf.get(key, 0.0)
        + (1.0 - embedding_share) * lexical_rrf.get(key, 0.0)
        for key in rows
    }
    top = max(scores.values(), default=0.0)
    if file_boost and top > 0.0:
        per_file: dict[Any, float] = {}
        best: dict[Any, tuple[Any, ...]] = {}
        for key, score in scores.items():
            path = key[0]
            per_file[path] = per_file.get(path, 0.0) + score
            if path not in best or score > scores[best[path]]:
                best[path] = key
        most = max(per_file.values())
        for path, key in best.items():
            scores[key] += top * FILE_BOOST_SHARE * per_file[path] / most
    ordered = sorted(
        scores,
        key=lambda key: (-scores[key], str(key[0]), int(key[1] or 0), str(key[2])),
    )
    fused = []
    for key in ordered[: max(1, int(top_k))]:
        matched = [
            name for name, ranking in (
                ("bm25", lexical_rrf), ("embedding", semantic_rrf),
            )
            if key in ranking
        ]
        fused.append({
            **rows[key],
            "final_score": round(scores[key], 6),
            "matched_by": matched,
        })
    return fused
