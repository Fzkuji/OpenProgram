"""impact_forecast with every OpenAlex request replaced by a fake fetch."""

import json
from datetime import date
from pathlib import Path

import pytest

from auto_research.tasks.citations import sources
from auto_research.tasks.impact import forecast as fc

TEX = r"""\documentclass{article}
\title{Learning-Rate Schedules for Linear Probes}
\begin{document}
\begin{abstract}
Linear probes on frozen features depend on the learning rate schedule. A short schedule lowers probe loss.
\end{abstract}
\end{document}
"""
TOPIC = "https://openalex.org/T1"


class Day(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 24)


def work(i, title, year=2021, month=3, counts=None, topic=TOPIC, venue="International Conference on Learning "
         "Representations", kind="conference", authors=3, arxiv=False, code=False, abstract="linear probes"):
    abstract = abstract + (" Code is available at github.com/x/y." if code else "")
    locations = [{"source": {"display_name": venue, "type": kind}, "landing_page_url": "https://x"}]
    if arxiv:
        locations.append({"source": {"display_name": "arXiv (Cornell University)", "type": "repository"},
                          "landing_page_url": f"https://arxiv.org/abs/2101.{i:05d}"})
    return {"id": f"https://openalex.org/W{1000 + i}", "title": title, "publication_year": year,
            "publication_date": f"{year}-{month:02d}-01", "primary_topic": {"id": topic, "display_name": "Probing"},
            "counts_by_year": [{"year": y, "cited_by_count": n} for y, n in (counts or {}).items()],
            "cited_by_count": sum((counts or {}).values()), "authorships": [{}] * authors,
            "primary_location": locations[0], "locations": locations, "type": "article",
            "abstract_inverted_index": {w: [j] for j, w in enumerate(abstract.split())}}


def similar(n, base=10):
    """n works about linear probe schedules, citations growing with i, half with code."""
    return [work(i, f"Linear probe learning rate schedule study {i}", year=2021, month=3,
                 counts={2021: base + i, 2022: base + i, 2023: base + i, 2024: 1},
                 code=i % 2 == 0, arxiv=i % 3 == 0, authors=2 + i % 5,
                 abstract="linear probes frozen features learning rate schedule probe loss")
            for i in range(n)]


OWN = work(999, "Learning-Rate Schedules for Linear Probes", year=2024, month=9,
           counts={2024: 5, 2025: 400, 2026: 900})


@pytest.fixture
def paper(tmp_path):
    p = tmp_path / "paper"
    (p / "workflow").mkdir(parents=True)
    (p / "main.tex").write_text(TEX)
    (p / "workflow" / "project.md").write_text("Kind: method\nPublished: arXiv:2409.00001\n")
    (p / "workflow" / "venue.md").write_text("# Venue\n\n## Venue\nName: ICLR 2027\n")
    return p


def fake(monkeypatch, search, sample, own=OWN):
    calls = []

    def fetch(url, params=None, as_json=True, tries=3, label="", headers=None):
        calls.append((url, dict(params or {})))
        if url.endswith("doi:10.48550/arxiv.2409.00001"):
            return own
        if "sample" in (params or {}):
            out = sample
        else:
            out = search
        if out is None:
            sources.UNAVAILABLE.add(label)
            return None
        return {"results": out}

    monkeypatch.setattr(sources, "fetch", fetch)
    monkeypatch.setattr(fc, "date", Day)
    return calls


def test_forecast_band_actual_and_factors(paper, monkeypatch):
    journal = [work(100 + i, f"Unrelated journal work {i}", kind="journal", venue="Some Journal",
                    counts={2021: 500}) for i in range(5)]
    off_topic = [work(200 + i, f"Protein folding {i}", topic="https://openalex.org/T9", kind="journal",
                      abstract="protein structure folding", counts={2021: 0}) for i in range(5)]
    calls = fake(monkeypatch, similar(20) + journal + [OWN], off_topic)
    out = json.loads(fc.impact_forecast(str(paper)))
    # searches: 2020-2023, the three kinds of work, then a topic sample of the topic of the published version
    searches = [p for u, p in calls if "search" in p]
    assert searches and all(p["filter"].startswith("publication_year:2020-2023,type:") for p in searches)
    sample = [p for u, p in calls if "sample" in p][0]
    assert sample["filter"].startswith("primary_topic.id:T1,") and sample["seed"] == fc.SEED
    record = json.loads(Path(out["json"]).read_text())
    assert record["venue"]["key"] == "iclr" and record["restricted_to_type"]
    ids = {c["id"] for c in record["comparables"]}
    assert OWN["id"] not in ids and not any("journal" in c["title"] for c in record["comparables"])
    assert record["topic"]["from"] == "published"
    f = out["forecast"]
    # March 2021: year 1 ends with 2021, year 3 with 2023; cumulative 10+i, 2(10+i), 3(10+i)
    assert f["1"]["n"] == 20 and 10 <= f["1"]["q25"] < f["1"]["q50"] < f["1"]["q75"] <= 29
    assert f["3"]["q50"] == pytest.approx(3 * f["1"]["q50"], rel=0.25)
    # the published version (September 2024): year 1 ends with 2025, year 2 with 2026 (not over)
    actual = out["actual"]["horizons"]
    assert actual["1"] == {"citations": 405, "partial": False, "where": "above"}
    assert actual["2"]["partial"] and "3" not in actual
    factors = {x["factor"]: x for x in record["factors"]}
    assert factors["code"]["n_yes"] >= 10 and factors["code"]["ratio"] is not None
    assert factors["authors"]["spearman"] is not None
    report = Path(out["report"]).read_text()
    assert "<svg" in report and "实际 405" in report and "只是相关，不是因果" in report
    assert out["status"] == "PASS"
    trail = json.loads(Path(out["run"]).read_text())
    assert [s["stage"] for s in trail["stages"]] == ["search", "topic", "forecast", "factors"]


def test_a_failing_source_is_a_note_not_a_crash(paper, monkeypatch):
    fake(monkeypatch, None, None, own=None)
    out = json.loads(fc.impact_forecast(str(paper)))
    assert out["status"] == "PASS_WITH_NOTES" and out["comparables"] == 0
    assert "OpenAlex" in out["unavailable_sources"]
    report = Path(out["report"]).read_text()
    assert "OpenAlex 没有找到发表版本" in report and "没有取到：OpenAlex" in report


def test_topic_from_similar_works_and_venue_argument(paper, monkeypatch):
    (paper / "workflow" / "project.md").write_text("Kind: method\n")
    calls = fake(monkeypatch, similar(12), [])
    out = json.loads(fc.impact_forecast(str(paper), venue="Transactions on Machine Learning Research"))
    record = json.loads(Path(out["json"]).read_text())
    assert record["topic"] == {"id": TOPIC, "name": "Probing", "from": "similar works"}
    assert record["venue"]["type"] == "journal" and not record["restricted_to_type"]
    assert out["actual"] is None and out["forecast"]["2"]["n"] == 12
    assert not any("doi:" in u for u, _ in calls)


def test_plain_code_pieces():
    assert fc.weighted_quantile([(1, 1), (2, 1), (3, 1)], 0.5) == 2
    assert fc.weighted_quantile([(1, 1), (10, 3)], 0.5) == pytest.approx(7.75)   # midpoints at 0.125 and 0.625
    assert fc.weighted_quantile([(0, 1), (10, 1)], 0.5) == 5
    w = work(1, "x", year=2022, month=8, counts={2021: 2, 2022: 1, 2023: 4, 2024: 8, 2025: 16})
    today = date(2026, 11, 24)
    assert [fc.cumulative(w, k, today) for k in (1, 2, 3)] == [7, 15, 31]   # Aug 2022: ends 2023, 2024, 2025
    w["publication_date"] = "2022-02-01"
    assert [fc.cumulative(w, k, today) for k in (1, 2, 3)] == [3, 7, 15]
    w["publication_year"], w["publication_date"] = 2024, "2024-09-01"
    assert fc.cumulative(w, 2, today) is None                                  # ends 2026, not over
    assert fc.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert fc.venue_of(Path("/nonexistent"), "NeurIPS 2026") == {"name": "NeurIPS 2026", "key": "neurips",
                                                                  "type": "conference"}
    assert fc.CODE.search("our code is available at") and not fc.CODE.search("the source of errors")


def test_no_title_or_abstract_raises(tmp_path):
    p = tmp_path / "empty"
    (p / "workflow").mkdir(parents=True)
    with pytest.raises(ValueError, match="no title or abstract"):
        fc.impact_forecast(str(p))


def test_a_published_version_with_another_title_is_flagged(paper, monkeypatch):
    other = dict(OWN, title="BERT: Pre-training of Deep Bidirectional Transformers",
                 primary_topic={"id": "https://openalex.org/T9", "display_name": "Other"})
    fake(monkeypatch, similar(12), [], own=other)
    out = json.loads(fc.impact_forecast(str(paper)))
    record = json.loads(Path(out["json"]).read_text())
    assert record["topic"]["id"] == TOPIC and record["paper_factors"]["authors"] is None
    assert out["actual"]["horizons"] and "和论文标题不一致" in Path(out["report"]).read_text()
