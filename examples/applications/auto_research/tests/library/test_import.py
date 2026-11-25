"""import_library: dedupe, key collisions, CSL-JSON. No network."""

import json

import pytest

from auto_research.tasks.citations.refs import parse_bib
from auto_research.tasks.library import import_library

MAIN = """@article{wang2024,
  title = {Agent Workflow Memory},
  doi = {10.5555/awm},
  year = {2024}
}

@article{smith2020, title={Old Paper on Planning}, eprint={2001.01234}}
"""

EXPORT = """@article{zotero_awm, title={Agent {W}orkflow Memory}, doi={https://doi.org/10.5555/AWM}}
@misc{smith_arxiv, title={A Different Title}, url={https://arxiv.org/abs/2001.01234v2}}
@article{wang2024, title={Another Paper by Wang},   year={2024}, note={kept as written}}
@inproceedings{fresh, title={Fresh Ideas}, booktitle={ICLR}}
@article{fresh_again, title={Fresh   ideas.}}
"""


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    repo.mkdir()
    (repo / "references.bib").write_text(MAIN)
    return repo


def test_dedupe_and_key_collision(paper, tmp_path):
    export = tmp_path / "zotero.bib"
    export.write_text(EXPORT)
    out = json.loads(import_library(str(paper), str(export)))
    assert out["target"] == str(paper / "references.bib")
    assert out["added"] == ["wang2024_2", "fresh"]
    assert out["renamed"] == {"wang2024": "wang2024_2"}
    assert {(s["key"], s["duplicate_of"], s["by"]) for s in out["skipped"]} == {
        ("zotero_awm", "wang2024", "doi"), ("smith_arxiv", "smith2020", "arxiv"), ("fresh_again", "fresh", "title")}
    text = (paper / "references.bib").read_text()
    assert text.startswith(MAIN)                                         # existing entries untouched
    assert "@article{wang2024_2, title={Another Paper by Wang},   year={2024}, note={kept as written}}" in text
    assert [r["key"] for r in parse_bib(text)] == ["wang2024", "smith2020", "wang2024_2", "fresh"]

    again = json.loads(import_library(str(paper), str(export)))         # rerunning adds nothing
    assert again["added"] == [] and (paper / "references.bib").read_text() == text


def test_csl_json_export_and_explicit_target(paper, tmp_path):
    items = [{"id": "http://zotero.org/items/ABC", "type": "paper-conference", "title": "Graph Memory {Agents}",
              "author": [{"family": "Chen", "given": "Li"}, {"literal": "OpenAI Team"}],
              "issued": {"date-parts": [[2025, 3]]}, "container-title": "NeurIPS", "DOI": "10.9/gm"},
             {"id": "kim2023", "type": "article", "title": "Preprint on Tools", "number": "arXiv:2305.11111",
              "publisher": "arXiv", "issued": {"date-parts": [[2023]]}},
             {"id": "dup", "type": "article-journal", "title": "Agent workflow memory", "DOI": "10.1/x"}]
    export = tmp_path / "zotero.json"
    export.write_text(json.dumps(items))
    out = json.loads(import_library(str(paper), str(export), target="extra.bib"))
    assert out["target"] == str(paper / "extra.bib")
    assert out["added"] == ["chen2025graph", "kim2023", "dup"]          # a new file holds none of them yet
    refs = {r["key"]: r for r in parse_bib((paper / "extra.bib").read_text())}
    assert refs["chen2025graph"]["doi"] == "10.9/gm" and refs["chen2025graph"]["year"] == "2025"
    assert refs["chen2025graph"]["venue"] == "NeurIPS" and refs["chen2025graph"]["title"] == "Graph Memory Agents"
    assert refs["chen2025graph"]["authors"] == ["Li Chen", "OpenAI Team"]
    assert refs["kim2023"]["arxiv"] == "2305.11111"

    out = json.loads(import_library(str(paper), str(export)))           # into the main bibliography
    assert out["added"] == ["chen2025graph", "kim2023"]
    assert out["skipped"] == [{"key": "dup", "duplicate_of": "wang2024", "by": "title"}]


def test_missing_export_raises(paper, tmp_path):
    with pytest.raises(ValueError):
        import_library(str(paper), str(tmp_path / "nope.bib"))
