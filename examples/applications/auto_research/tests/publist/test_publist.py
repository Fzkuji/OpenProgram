"""publication_list with OpenAlex, ORCID, DBLP, Hugging Face and arXiv replaced by a fake
fetch and the curator scripted."""

import json
import subprocess
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.citations import sources
from auto_research.tasks.publist import merge, profiles
from auto_research.tasks.publist import workflow as wf

TEX = r"""\documentclass{article}
\title{Sparse Memory for Agents}
\author{Ada Lovelace \\ Uni A \and Bob Jones \\ Uni B}
\begin{document}
\maketitle
\begin{abstract}
We prune agent memories and keep accuracy.
\end{abstract}
\end{document}
"""
ORCID = "0000-0001-2345-6789"
ANUBIS = "<!doctype html><html><head><title>Making sure you&#39;re not a bot!</title></head></html>"
DBLP_ROWS = [
    {"publ": "https://dblp.org/rec/journals/corr/abs-2101-00001", "title": "Sparse Memory for Agents.", "year": "2021",
     "doi": "https://doi.org/10.48550/ARXIV.2101.00001", "venue": "CoRR",
     "types": "https://dblp.org/rdf/schema#Informal https://dblp.org/rdf/schema#Publication",
     "authors": "2:Bob Jones|1:Ada Lovelace 0001"},
    {"publ": "https://dblp.org/rec/conf/nips/Lovelace20", "title": "Graph Memory Networks.", "year": "2020",
     "doi": "https://doi.org/10.1/X", "venue": "NeurIPS",
     "types": "https://dblp.org/rdf/schema#Article https://dblp.org/rdf/schema#Publication",
     "authors": "1:Ada Lovelace 0001|2:Bob Jones"},
]


def sparql_rows(rows):
    return json.dumps({"results": {"bindings": [{k: {"type": "literal", "value": v} for k, v in row.items()}
                                                for row in rows]}})
ARXIV_ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2101.00001v2</id>
<title>Sparse Memory for Agents</title><summary>We prune memories of agents.</summary></entry></feed>"""


def person(aid, name, works, inst, orcid=""):
    return {"id": f"https://openalex.org/{aid}", "display_name": name, "display_name_alternatives": [],
            "orcid": f"https://orcid.org/{orcid}" if orcid else None, "works_count": works, "cited_by_count": works,
            "summary_stats": {}, "last_known_institutions": [{"id": "https://openalex.org/I1", "display_name": inst}],
            "counts_by_year": []}


def ship(aid, name, inst=""):
    return {"author": {"id": f"https://openalex.org/{aid}", "display_name": name},
            "institutions": [{"display_name": inst}] if inst else []}


def oa(wid, title, year, kind, venue, authors, doi="", abstract="", pdf=""):
    words = abstract.split()
    return {"id": f"https://openalex.org/{wid}", "title": title, "publication_year": year, "type": kind,
            "doi": f"https://doi.org/{doi}" if doi else None,
            "primary_location": {"source": {"display_name": venue}, "landing_page_url": ""},
            "best_oa_location": {"pdf_url": pdf} if pdf else None, "locations": [], "authorships": authors,
            "abstract_inverted_index": {w: [i] for i, w in enumerate(words)} if words else None}


ADA, BOB = ship("A1", "Ada Lovelace", "Uni A"), ship("B1", "Bob Jones")
WORKS = [
    oa("W1", "Sparse Memory for Agents", 2021, "preprint", "arXiv", [ADA, BOB], "10.48550/arxiv.2101.00001",
       "We prune memories and keep 73.5 accuracy."),
    oa("W2", "Sparse Memory for Agents.", 2022, "conference-paper", "Neural Information Processing Systems",
       [ADA, BOB], "10.1/neurips22", pdf="https://x.org/sparse.pdf"),
    oa("W3", "Graph Memory Networks", 2020, "article", "Neural Information Processing Systems", [ADA, BOB], "10.1/x",
       "Graphs store memories. Code at github.com/ada/graphmem."),
    oa("W4", "Cardiac Imaging in Mice", 2019, "article", "Journal of Cardiology",
       [ship("A1", "Ada Lovelace", "Big Hospital"), ship("Z1", "Zed Heart")], "10.1/heart", "Mouse hearts imaged."),
    oa("W5", "Memory Retrieval by Hashing", 2018, "article", "Journal of Hashing", [ADA, BOB], "10.1/hash"),
    oa("W6", "Erratum: Sparse Memory for Agents", 2022, "erratum", "Neural Information Processing Systems", [ADA]),
]
ORCID_WORKS = {"group": [
    {"external-ids": {"external-id": [{"external-id-type": "doi", "external-id-value": "10.1/x"}]},
     "work-summary": [{"put-code": 1, "title": {"title": {"value": "Graph Memory Networks"}}, "type": "journal-article",
                       "publication-date": {"year": {"value": "2020"}}, "journal-title": {"value": "NeurIPS"}}]},
    {"external-ids": {"external-id": [{"external-id-type": "doi", "external-id-value": "10.1/orcidonly"}]},
     "work-summary": [{"put-code": 2, "title": {"title": {"value": "Learning to Forget"}}, "type": "journal-article",
                       "publication-date": {"year": {"value": "2023"}},
                       "journal-title": {"value": "Journal of Forgetting"}}]},
    {"external-ids": {"external-id": []},
     "work-summary": [{"put-code": 3, "title": {"title": {"value": "Protein Folding Notes"}}, "type": "journal-article",
                       "publication-date": {"year": {"value": "2017"}},
                       "journal-title": {"value": "Biochem Letters"}}]},
]}
BY_DOI = oa("W9", "Learning to Forget", 2023, "article", "Journal of Forgetting",
            [ADA, ship("E1", "Eve Moss")], "10.1/orcidonly", "Agents forget on purpose.")


class Fake:
    def __init__(self, works=WORKS, orcid=ORCID_WORKS, dblp="blocked", down=False):
        self.works, self.orcid, self.dblp, self.down = works, orcid, dblp, down
        self.calls = []

    def __call__(self, url, params=None, as_json=True, tries=3, label="", headers=None):
        params = params or {}
        self.calls.append((url, dict(params)))
        if self.down:
            if label:
                sources.UNAVAILABLE.add(label)
            return None
        if url == "https://api.openalex.org/authors":
            if params.get("search") == "Ada Lovelace":
                return {"results": [person("A2", "Ada Lovelace", 50, "Big Hospital"),
                                    person("A1", "Ada Lovelace", 6, "Uni A", ORCID)]}
            if params.get("filter") == f"orcid:{ORCID}":
                return {"results": [person("A1", "Ada Lovelace", 6, "Uni A", ORCID)]}
            return {"results": []}
        if url == "https://api.openalex.org/works":
            f = params.get("filter", "")
            if f.startswith("doi:"):
                return {"results": [BY_DOI] if "10.1/orcidonly" in f else []}
            if f == "author.id:A2":
                return {"results": [oa("W8", "Heart Valves", 2020, "article", "Cardiology",
                                       [ship("A2", "Ada Lovelace"), ship("Z2", "Carl Heart")])]}
            if f == "author.id:A1":
                return {"results": self.works, "meta": {"next_cursor": None}}
            raise AssertionError(f"unexpected OpenAlex request {params}")
        if "pub.orcid.org" in url:
            if url.endswith("/works"):
                return self.orcid
            return {"name": {"given-names": {"value": "Ada"}, "family-name": {"value": "Lovelace"}}}
        if "dblp.org" in url:
            if self.dblp == "blocked":
                return ANUBIS
            query = params.get("query", "")
            if "dblp:creatorName" in query:
                return sparql_rows([{"p": "https://dblp.org/pid/12/345", "name": "Ada Lovelace 0001"}])
            if "<https://dblp.org/pid/12/345>" in query and "authoredBy" in query:
                return sparql_rows(DBLP_ROWS)
            if "<https://dblp.org/pid/12/345>" in query:
                return sparql_rows([{"name": "Ada Lovelace 0001"}])
            return sparql_rows([])
        if "huggingface.co/api/papers/2101.00001" in url:
            return {"githubRepo": "https://github.com/ada/sparse"}
        if "huggingface.co" in url:
            return None
        if "export.arxiv.org" in url:
            return ARXIV_ATOM
        raise AssertionError(f"unexpected request {url}")


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    (repo / "docs").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "main.tex").write_text(TEX)
    (repo / "docs" / "index.html").write_text("<html></html>")
    (repo / "workflow" / "project.md").write_text("Kind: method\nClaim: Pruning keeps accuracy.\n")
    return repo


def review(i, summary="", namesake=False, reason=""):
    return {"id": f"P{i}", "summary": summary, "namesake": namesake, "field": "machine learning", "reason": reason}


def run(paper, monkeypatch, replies, fake=None, **kw):
    fake = fake or Fake()
    monkeypatch.setattr(sources, "fetch", fake)
    with script_roles({wf.curator: replies}) as calls:
        out = json.loads(wf.publication_list(str(paper), **kw))
    return out, calls, fake


def test_merge_links_namesakes_and_outputs(paper, monkeypatch):
    # entries by year: 2023 Learning to Forget, 2022 Sparse Memory, 2020 Graph Memory, 2019 Cardiac,
    # 2018 Hashing, 2017 Protein Folding
    reply = {"works": [review(1, "Agents forget on purpose."), review(2, "Pruning keeps 99.9 accuracy."),
                       review(3, "Graphs store memories."),
                       review(4, "Mouse hearts imaged.", True, "Cardiology, far from machine learning."),
                       review(5, "", True, "Hashing."), review(6, "", True, "Biochemistry.")]}
    out, calls, fake = run(paper, monkeypatch, [reply], author="Ada Lovelace")
    assert out["status"] == "PASS_WITH_NOTES" and out["author"] == "Ada Lovelace"
    assert out["entries"] == 5 and out["uncertain"] == 1 and out["merged"] == 1
    request = calls[wf.curator][0].text
    assert "summarize each work only from its abstract" in request and "when unsure, answer false" in request and "Bob Jones (" in request
    record = json.loads(Path(out["json"]).read_text())
    p = record["person"]
    assert p["openalex"] == ["A1"] and p["basis"] == "overlap" and p["orcid"] == ORCID
    assert p["shared"]["coauthors"] == ["Bob Jones"] and p["alternatives"][0]["id"] == "A2"
    assert record["problems"] == [] and [s["reason"] for s in record["skipped"]] == ["type erratum"]
    titles = [e["title"] for e in record["entries"]]
    assert titles == ["Learning to Forget", "Sparse Memory for Agents", "Graph Memory Networks",
                      "Memory Retrieval by Hashing", "Protein Folding Notes"]
    forget, sparse, graph, hashing, protein = record["entries"]
    assert forget["authors"] == ["Ada Lovelace", "Eve Moss"] and forget["summary"] == "Agents forget on purpose."
    assert sparse["arxiv"] == "2101.00001" and sparse["doi"] == "10.1/neurips22" and sparse["year"] == 2022
    assert sparse["venue"] == "Neural Information Processing Systems" and sparse["pdf"] == "https://x.org/sparse.pdf"
    assert sparse["code"] == "https://github.com/ada/sparse" and sparse["project"] == "index.html"
    assert sparse["summary"] == "" and sparse["merged_versions"]
    assert graph["code"] == "https://github.com/ada/graphmem" and graph["sources"] == ["openalex", "orcid"]
    assert hashing["namesake"] == "" and protein["namesake"] == "flagged, kept: on ORCID"
    assert [d["why"] for d in record["dropped_summaries"]] == ["numbers not in the abstract: 99.9"]
    assert {o["title"]: o["why"] for o in record["overruled"]} == {
        "Memory Retrieval by Hashing": "shares coauthors Bob Jones", "Protein Folding Notes": "listed on the author's ORCID"}
    assert [e["title"] for e in record["excluded"]] == ["Cardiac Imaging in Mice"]
    assert any("bot check" in n for n in record["notes"])
    page = Path(out["page"]).read_text()
    assert page.index('id="y2023"') < page.index('id="y2022"') < page.index('id="y2017"')
    assert "<script" not in page and "Cardiac" not in page and "<b>Ada Lovelace</b>, Bob Jones" in page
    assert 'href="https://arxiv.org/abs/2101.00001"' in page and 'href="https://doi.org/10.1/neurips22"' in page
    assert "also arXiv 2021" in page
    bib = Path(out["bib"]).read_text()
    assert bib.count("\n@") + bib.startswith("@") == 5
    assert "@inproceedings{lovelace2022sparse," in bib and "eprint = {2101.00001}" in bib
    assert "@article{lovelace2023learning," in bib
    report = Path(out["report"]).read_text()
    assert "需要你确认" in report and "Cardiac Imaging in Mice" in report and "DBLP" in report
    assert "workflow/publication_list.json" in report and "cp " not in report
    assert not (paper / "docs" / "publications.html").exists()
    assert any("dblp.org" in u for u, _ in fake.calls)


def test_orcid_input_with_dblp_passes(paper, monkeypatch):
    works = [WORKS[2]]
    orcid = {"group": [ORCID_WORKS["group"][1]]}
    # entries: 2023 Learning to Forget, 2021 Sparse Memory (DBLP CoRR only), 2020 Graph Memory
    reply = {"works": [review(1, "Agents forget on purpose."), review(2, "Memories of agents are pruned."),
                       review(3, "Graphs store memories.")]}
    out, calls, fake = run(paper, monkeypatch, [reply], fake=Fake(works=works, orcid=orcid, dblp="open"),
                           author=f"https://orcid.org/{ORCID}")
    assert out["status"] == "PASS", out
    record = json.loads(Path(out["json"]).read_text())
    p = record["person"]
    assert p["kind"] == "orcid" and p["dblp"] == "12/345" and p["sources"] == ["OpenAlex", "ORCID", "DBLP"]
    sparse = record["entries"][1]
    assert sparse["preprint"] and sparse["arxiv"] == "2101.00001" and sparse["abstract"] == "We prune memories of agents."
    assert sparse["summary"] == "Memories of agents are pruned."
    graph = record["entries"][2]
    assert graph["sources"] == ["openalex", "dblp"] and graph["venue"] == "Neural Information Processing Systems"
    assert record["counts"] == {"openalex": 1, "orcid": 1, "dblp": 2, "filled_by_doi": 1}
    assert "@misc{lovelace2021sparse," in Path(out["bib"]).read_text()


def test_first_author_is_the_default(paper, monkeypatch):
    reply = {"works": [review(i) for i in range(1, 7)]}
    out, _, _ = run(paper, monkeypatch, [reply])
    record = json.loads(Path(out["json"]).read_text())
    assert record["person"]["as_given"] == "Ada Lovelace" and record["person"]["origin"].startswith("first author")


def test_no_author_raises(paper):
    (paper / "main.tex").write_text(TEX.replace(r"\author{Ada Lovelace \\ Uni A \and Bob Jones \\ Uni B}", ""))
    with pytest.raises(ValueError, match="author is empty"):
        wf.publication_list(str(paper))


def test_sources_down_is_not_a_crash(paper, monkeypatch):
    out, calls, _ = run(paper, monkeypatch, [], fake=Fake(down=True), author="Ada Lovelace")
    assert out["status"] == "FAIL" and out["entries"] == 0 and "OpenAlex" in out["unavailable"]
    assert not calls[wf.curator]
    assert "没有找到" in Path(out["report"]).read_text()


def test_malformed_review_raises(paper, monkeypatch):
    bad = {"works": [review(1)] * 6}                     # the schema passes, the code check does not
    with pytest.raises(ValueError, match="curator reviewed an unknown or repeated work"):
        run(paper, monkeypatch, [bad, bad], author="Ada Lovelace")


def test_author_kinds():
    assert profiles.kind_of("Kaiming He") == ("name", "Kaiming He")
    assert profiles.kind_of("https://orcid.org/0000-0002-0137-889x") == ("orcid", "0000-0002-0137-889X")
    assert profiles.kind_of("a5100700361") == ("openalex", "A5100700361")
    assert profiles.kind_of("https://dblp.org/pid/34/10307.html") == ("dblp", "34/10307")


def rec(rid, title, year, venue="", doi="", arxiv="", kind="article"):
    return profiles.record("openalex", rid, title, year, venue, kind, doi, arxiv)


def test_merge_rules():
    records = [rec("a", "A nanowire WO3 humidity sensor integrated with a micro-heater on chip", 2006, "Sensors B"),
               rec("b", "A nanowire WO 3 humidity sensor integrated with a micro-heater on chip", 2007, "Sensors B"),
               rec("c", "Instrumentation, Measurement, and Fabrication Technology-Fabrication of a Micromachined "
                        "Tunable Capacitor Using CMOS", 2006, "JJAP"),
               rec("d", "Fabrication of a Micromachined Tunable Capacitor Using CMOS", 2006, "Japanese J. Appl. Phys."),
               rec("e", "Introduction", 2010, "Book"), rec("f", "Introduction", 2011, "Other book"),
               rec("g", "Mask R-CNN", 2017, "ICCV", kind="conference-paper"),
               rec("h", "Mask R-CNN", 2018, "TPAMI"),
               rec("i", "Correction to: Mask R-CNN", 2018, "TPAMI")]
    entries, skipped = merge.build(records)
    assert [s["rid"] for s in skipped] == ["i"]
    sets = sorted(sorted(e["records"]) for e in entries)
    assert sets == [["a", "b"], ["c", "d"], ["e"], ["f"], ["g", "h"]]
    assert merge.coverage(records, entries, skipped) == []
    mask = next(e for e in entries if e["title"] == "Mask R-CNN")
    assert mask["venue"] == "ICCV" and mask["year"] == 2017 and [v["venue"] for v in mask["versions"]] == ["ICCV", "TPAMI"]


def test_the_author_includes_a_flagged_work_and_later_runs_keep_it(paper, monkeypatch):
    reply = {"works": [review(1), review(2), review(3),
                       review(4, "", True, "Cardiology, far from machine learning."),
                       review(5, "", True, "Hashing."), review(6, "", True, "Biochemistry.")]}
    out, _, _ = run(paper, monkeypatch, [reply], author="Ada Lovelace")
    folder = Path(out["json"]).parent
    asked = json.loads((folder / "questions.json").read_text())["questions"]
    assert [q["title"] for q in asked] == ["Cardiac Imaging in Mice"]
    assert [o["label"] for o in asked[0]["options"]] == ["收录", "排除"]
    assert wf.answer(paper, folder, asked[0]["id"], "include") == ["workflow/publication_list.json"]
    record = json.loads((folder / "publications.json").read_text())
    assert "Cardiac Imaging in Mice" in [e["title"] for e in record["entries"]]
    assert [e["id"] for e in record["entries"]] == [f"P{i}" for i in range(1, 7)]
    assert "Cardiac" in (folder / "publications.html").read_text()
    assert wf.choices(paper)["include"] == [asked[0]["id"]]
    again, _, _ = run(paper, monkeypatch, [reply], author="Ada Lovelace")
    record = json.loads(Path(again["json"]).read_text())
    assert record["excluded"] == [] and "Cardiac Imaging in Mice" in [e["title"] for e in record["entries"]]
    assert any(o["why"] == "the author confirmed it" for o in record["overruled"])
