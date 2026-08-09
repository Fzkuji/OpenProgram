"""Fused BM25 + embedding retrieval merged with Reciprocal Rank Fusion.

Modelled on semble's hybrid code search: each backend's ranking is converted
to RRF scores 1/(k + rank) so raw BM25 scores and cosine similarities never
mix, then blended with an alpha weight and boosted for file coherence.
"""

from __future__ import annotations

import re
from typing import Any

from .bm25 import _normalize_path_prefix

_RRF_K = 60
# Widen each backend's candidate pool so the fused union is large enough.
_CANDIDATE_MULTIPLIER = 5
# Several matching blocks in the same topic file is evidence that file is the
# right subject; the file's best row gains up to 20% of the max fused score.
_FILE_COHERENCE_BOOST_FRAC = 0.2

# Literal cues: an explicit date, a quoted string, or a capitalized proper
# noun mid-query. Such queries want exact lexical matching, so semantic
# similarity gets less weight (alpha 0.3); plain natural-language queries are
# balanced (alpha 0.5) — same split semble uses for symbol vs NL queries.
_LITERAL_QUERY_RE = re.compile(
    r"\b\d{4}(?:-\d{2}(?:-\d{2})?)?\b"  # date or year
    r"|[\"'“‘].+?[\"'”’]"  # quoted string
    r"|(?<=[a-z] )[A-Z][a-z]+"  # proper noun after a lowercase word
)
_ALPHA_LITERAL = 0.3
_ALPHA_BALANCED = 0.5


def resolve_alpha(query: str) -> float:
    """Weight for the embedding side; the rest goes to BM25."""
    return _ALPHA_LITERAL if _LITERAL_QUERY_RE.search(query) else _ALPHA_BALANCED


def _row_key(row: dict[str, Any]) -> tuple:
    return (row["path"], row["line"], row.get("event_id") or row.get("event"))


def _rrf(rows: list[dict[str, Any]]) -> dict[tuple, float]:
    return {_row_key(row): 1.0 / (_RRF_K + rank) for rank, row in enumerate(rows, 1)}


def rrf_fuse(
    bm25_rows: list[dict[str, Any]],
    embedding_rows: list[dict[str, Any]],
    *,
    alpha: float = _ALPHA_BALANCED,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    """Merge two ranked result lists; rows in only one list keep that ranking.

    Returns rows shaped for :func:`src.retrieval.bm25.render_search_results`.
    """
    bm25_rrf = _rrf(bm25_rows)
    embedding_rrf = _rrf(embedding_rows)

    rows_by_key: dict[tuple, dict[str, Any]] = {}
    for row in embedding_rows + bm25_rows:  # bm25 last: richer fields win
        rows_by_key.setdefault(_row_key(row), {}).update(row)

    scores = {
        key: alpha * embedding_rrf.get(key, 0.0)
        + (1.0 - alpha) * bm25_rrf.get(key, 0.0)
        for key in rows_by_key
    }

    # File-coherence boost: the best row of each file gains in proportion to
    # the file's aggregate score across all its matching rows.
    max_score = max(scores.values(), default=0.0)
    if max_score > 0.0:
        file_sum: dict[str, float] = {}
        best_key: dict[str, tuple] = {}
        for key, score in scores.items():
            path = key[0]
            file_sum[path] = file_sum.get(path, 0.0) + score
            if path not in best_key or score > scores[best_key[path]]:
                best_key[path] = key
        max_file_sum = max(file_sum.values())
        boost_unit = max_score * _FILE_COHERENCE_BOOST_FRAC
        for path, key in best_key.items():
            scores[key] += boost_unit * file_sum[path] / max_file_sum

    fused = []
    for key in sorted(scores, key=lambda k: (-scores[k], k[0], k[1])):
        row = rows_by_key[key]
        features = []
        if key in bm25_rrf:
            features.append("bm25")
        if key in embedding_rrf:
            features.append("embedding")
        fused.append({
            "path": row["path"],
            "line": row["line"],
            "content": row["content"],
            "refs": row.get("refs", []),
            "dates": row.get("dates") or ([row["date"]] if row.get("date") else []),
            "final_score": round(scores[key], 6),
            "rule_features": features,
        })
    return fused[: max(1, int(top_k))]


def fused_search(
    bm25_index: Any,
    embedding_index: Any,
    query: str,
    *,
    top_k: int = 8,
    path_prefix: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Run both backends wide, RRF-fuse, boost file coherence, cut to top_k."""
    candidate_count = top_k * _CANDIDATE_MULTIPLIER
    bm25_rows = bm25_index.search(
        query,
        top_k=candidate_count,
        path_prefix=path_prefix,
        date_from=date_from,
        date_to=date_to,
    )
    embedding_rows = embedding_index.search(
        query,
        top_k=candidate_count,
        date_from=date_from,
        date_to=date_to,
    )
    if path_prefix:
        prefix = _normalize_path_prefix(path_prefix)
        embedding_rows = [
            row for row in embedding_rows if row["path"].startswith(prefix)
        ]
    return rrf_fuse(
        bm25_rows, embedding_rows, alpha=resolve_alpha(query), top_k=top_k
    )
