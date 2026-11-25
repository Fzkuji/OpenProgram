"""import_annotations: Zotero's local API faked, and JSON, CSV and Markdown exports."""

import csv
import json

import pytest

from auto_research.tasks.citations import sources
from auto_research.tasks.reading import annotations as ann

BIB = r"""@inproceedings{vaswani2017, title={Attention Is All You Need}, author={Vaswani, Ashish}, year={2017},
  eprint={1706.03762}}
@article{he2016, title={Deep Residual Learning for Image Recognition}, author={He, Kaiming}, year={2016},
  doi={10.1109/CVPR.2016.90}}
@article{lewis2020, title={Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks},
  author={Lewis, Patrick}, year={2020}}
"""


def note(key, parent, text, comment="", page="", color="#ffd400"):
    return {"key": key, "version": 1, "data": {"key": key, "itemType": "annotation", "parentItem": parent,
                                               "annotationType": "highlight", "annotationText": text,
                                               "annotationComment": comment, "annotationPageLabel": page,
                                               "annotationColor": color}}


ITEMS = {
    "PDF1": {"key": "PDF1", "itemType": "attachment", "parentItem": "REG1", "title": "Full Text PDF"},
    "REG1": {"key": "REG1", "itemType": "journalArticle", "title": "Deep residual learning for image recognition",
             "DOI": "https://doi.org/10.1109/cvpr.2016.90", "creators": [{"lastName": "He"}], "date": "2016"},
    "PDF2": {"key": "PDF2", "itemType": "attachment", "parentItem": "REG2", "title": "arXiv PDF"},
    "REG2": {"key": "REG2", "itemType": "preprint", "title": "Attention is all you need (preprint)",
             "extra": "arXiv: 1706.03762", "url": "https://arxiv.org/abs/1706.03762"},
    "PDF3": {"key": "PDF3", "itemType": "attachment", "title": "Some unrelated scan"},
}
NOTES = [note("A1", "PDF1", "Residual connections ease optimization.", "supports C1", "3"),
         note("A2", "PDF2", "Attention   suffices.", "", "2"),
         note("A3", "PDF1", "", "only a comment", "5"),
         note("A4", "PDF3", "Not in the bib.")]


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    (repo / "references.bib").write_text(BIB)
    return repo


@pytest.fixture
def zotero(monkeypatch):
    calls = []

    def fetch(url, params=None, as_json=True, tries=3, label="", headers=None):
        calls.append((url, dict(params or {})))
        assert url.startswith("http://localhost:23119/api/users/0/") and tries == 1
        if url.endswith("/items"):
            assert params["itemType"] == "annotation" and params["format"] == "json"
            return NOTES[params["start"]:params["start"] + params["limit"]]
        key = url.rsplit("/", 1)[1]
        return {"key": key, "data": ITEMS[key]} if key in ITEMS else None
    monkeypatch.setattr(sources, "fetch", fetch)
    monkeypatch.setattr(ann, "PAGE", 2)
    return calls


def rows(path):
    return list(csv.DictReader(open(path, encoding="utf-8")))


def test_zotero_local_api(paper, zotero):
    (paper / "workflow" / "annotations.csv").write_text("key,page,quote,comment,color\nhe2016,1,My own row,,\n")
    out = json.loads(ann.import_annotations(str(paper)))
    pages = [p["start"] for u, p in zotero if u.endswith("/items")]
    assert pages == [0, 2, 4]                                                # paged until a short page
    assert sum(u.endswith("/items/PDF1") for u, _ in zotero) == 1            # parents are fetched once
    assert out["status"] == "PASS_WITH_NOTES" and out["matched"] == 3 and out["kept"] == 1
    assert out["unmatched"] == {"Some unrelated scan": 1}
    got = rows(out["annotations"])
    assert got[0] == {"key": "he2016", "page": "1", "quote": "My own row", "comment": "", "color": ""}
    assert {"key": "he2016", "page": "3", "quote": "Residual connections ease optimization.",
            "comment": "supports C1", "color": "#ffd400"} in got
    assert {"key": "vaswani2017", "page": "2", "quote": "Attention suffices.", "comment": "",
            "color": "#ffd400"} in got                                     # matched by arXiv id
    cands = rows(out["candidates"])
    assert cands[0] == {"claim": "", "work": "he2016", "stance": "", "study": "",
                        "quote": "Residual connections ease optimization.", "note": "p. 3; supports C1"}
    assert len(cands) == 2                                                  # a comment alone is no quote
    assert not (paper / "workflow" / "claim_support.csv").exists()
    again = json.loads(ann.import_annotations(str(paper)))                 # rerunning adds nothing
    assert again["added"] == 0 and len(rows(again["annotations"])) == len(got)
    assert "没对上 .bib" in open(out["report"], encoding="utf-8").read()


def test_zotero_not_running_says_how_to_enable(paper, monkeypatch):
    monkeypatch.setattr(sources, "fetch", lambda *a, **k: None)
    with pytest.raises(ValueError, match="Allow other applications"):
        ann.import_annotations(str(paper))


def test_json_export_of_zotero_items(paper, tmp_path):
    export = tmp_path / "zotero.json"
    export.write_text(json.dumps([n for n in NOTES] + [{"key": k, "data": v} for k, v in ITEMS.items()]))
    out = json.loads(ann.import_annotations(str(paper), str(export)))
    assert out["matched"] == 3 and out["unmatched"] == {"Some unrelated scan": 1}


def test_json_records_and_csv_export(paper, tmp_path):
    export = tmp_path / "readcube.json"
    export.write_text(json.dumps([{"title": "Retrieval-augmented generation for knowledge-intensive NLP tasks",
                                   "annotations": [{"text": "RAG mixes memories.", "note": "key idea", "page": 4,
                                                    "color": "yellow"}]}]))
    out = json.loads(ann.import_annotations(str(paper), str(export)))
    assert out["matched"] == 1 and rows(out["annotations"])[0]["key"] == "lewis2020"
    export = tmp_path / "papers.csv"
    export.write_text("Title,DOI,Page,Highlighted Text,Note,Color\n"
                      ",10.1109/CVPR.2016.90,7,Deeper is better.,,green\n"
                      "Unknown Paper,,1,Something.,,\n")
    out = json.loads(ann.import_annotations(str(paper), str(export)))
    assert out["matched"] == 1 and out["unmatched"] == {"Unknown Paper": 1}
    assert {"key": "he2016", "page": "7", "quote": "Deeper is better.", "comment": "",
            "color": "green"} in rows(out["annotations"])


def test_markdown_note_from_annotations(paper, tmp_path):
    export = tmp_path / "note.md"
    export.write_text("# Annotations\n(24/11/2026)\n\n"
                      "“Residual nets win.” ([He et al., 2016, p. 5](zotero://open-pdf/library/items/PDF1?page=5)) "
                      "check this\n\n"
                      "\"Self-attention only.\" ([Vaswani et al., 2017, p. 2](zotero://select/library/items/X))\n"
                      "“Orphan.” ([Nobody, 1999, p. 1](zotero://x))\n")
    out = json.loads(ann.import_annotations(str(paper), str(export)))
    got = rows(out["annotations"])
    assert {"key": "he2016", "page": "5", "quote": "Residual nets win.", "comment": "check this", "color": ""} in got
    assert any(r["key"] == "vaswani2017" and r["page"] == "2" for r in got)
    assert out["matched"] == 2 and sum(out["unmatched"].values()) == 1


def test_bad_sources(paper, tmp_path):
    with pytest.raises(ValueError, match="no such file"):
        ann.import_annotations(str(paper), str(tmp_path / "missing.json"))
    (tmp_path / "x.pdf").write_text("x")
    with pytest.raises(ValueError, match="unknown export format"):
        ann.import_annotations(str(paper), str(tmp_path / "x.pdf"))
