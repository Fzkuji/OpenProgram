"""How much of the field the search found, from the overlap of the sources."""

import pytest

from auto_research.tasks.survey import coverage


def found(*sources):
    return {"found_by": list(sources)}


def test_overlap_of_two_sources_estimates_the_field():
    # 10 by arxiv, 10 by openalex, 5 by both: Chapman gives 11*11/6 - 1 ≈ 19.2 papers; 15 found
    papers = [found("arxiv", "openalex")] * 5 + [found("arxiv")] * 5 + [found("openalex")] * 5
    est = coverage.estimate(papers)
    assert est["found"] == 15 and est["estimated"] == 19 and est["coverage"] == pytest.approx(0.78, abs=0.01)
    assert est["by_source"] == {"arxiv": 10, "semantic scholar": 0, "openalex": 10}
    assert est["only_one_source"] == 10 and coverage.note(est) == ""


def test_little_overlap_asks_for_more_queries_and_none_cannot_be_estimated():
    low = [found("arxiv", "openalex")] + [found("arxiv")] * 9 + [found("openalex")] * 9
    assert coverage.estimate(low)["coverage"] < coverage.LOW
    assert "add queries" in coverage.note(coverage.estimate(low))
    none = [found("arxiv")] * 3 + [found("openalex")] * 3
    assert coverage.estimate(none)["coverage"] is None and coverage.note(coverage.estimate(none)) == ""
    assert coverage.estimate([])["coverage"] is None
