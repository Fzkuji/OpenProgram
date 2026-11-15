"""Hybrid recall merges BM25 and embedding rankings with settings the user can tune."""
from __future__ import annotations

from openprogram.state.memory.management.config import load_memory_config
from openprogram.state.memory.retrieval import fusion


def _row(path, line, content="x"):
    return {"path": path, "line": line, "content": content}


def test_auto_weighting_leans_on_keywords_for_literal_queries():
    assert fusion.embedding_weight("what did Dave say on 2026-05-01") == 0.3
    assert fusion.embedding_weight("会议提到的“预算表”") == 0.3
    assert fusion.embedding_weight("how does the writer schedule work") == 0.5


def test_fixed_weightings_override_auto():
    assert fusion.embedding_weight("2026-05-01", "semantic") == 0.7
    assert fusion.embedding_weight("anything", "keyword") == 0.3
    assert fusion.embedding_weight("anything", "balanced") == 0.5


def test_weighting_decides_which_ranking_leads():
    lexical = [_row("topics/a.md", 1), _row("topics/b.md", 1)]
    semantic = [_row("topics/b.md", 1), _row("topics/a.md", 1)]
    keyword = fusion.fuse(lexical, semantic, embedding_share=0.3, file_boost=False)
    semantic_first = fusion.fuse(lexical, semantic, embedding_share=0.7, file_boost=False)
    assert keyword[0]["path"] == "topics/a.md"
    assert semantic_first[0]["path"] == "topics/b.md"


def test_hit_in_one_ranking_is_kept_and_labelled():
    fused = fusion.fuse([_row("topics/a.md", 1)], [_row("topics/b.md", 3)])
    assert {row["path"] for row in fused} == {"topics/a.md", "topics/b.md"}
    by_path = {row["path"]: row["matched_by"] for row in fused}
    assert by_path == {"topics/a.md": ["bm25"], "topics/b.md": ["embedding"]}


def test_file_boost_lifts_a_topic_with_several_matches():
    lexical = [_row("topics/one.md", 1), _row("topics/many.md", 1),
               _row("topics/many.md", 5), _row("topics/many.md", 9)]
    semantic = [_row("topics/one.md", 1), _row("topics/many.md", 1)]
    plain = fusion.fuse(lexical, semantic, file_boost=False)
    boosted = fusion.fuse(lexical, semantic, file_boost=True)
    assert plain[0]["path"] == "topics/one.md"
    assert boosted[0]["path"] == "topics/many.md"


def test_settings_load_hybrid_switches_and_reject_unknown_values():
    config = load_memory_config({"memory": {"retrieval": {"hybrid": {
        "weighting": "semantic", "file_boost": False,
    }}}})
    assert (config.hybrid_weighting, config.hybrid_file_boost) == ("semantic", False)
    fallback = load_memory_config({"memory": {"retrieval": {"hybrid": {
        "weighting": "loud", "file_boost": "yes",
    }}}})
    assert (fallback.hybrid_weighting, fallback.hybrid_file_boost) == ("auto", True)
