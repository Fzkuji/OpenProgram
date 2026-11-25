"""peer_feedback with OpenAlex and ORCID replaced by a fake fetch and the drafter scripted."""

import json
import subprocess
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.citations import sources
from auto_research.tasks.feedback import people
from auto_research.tasks.feedback import workflow as wf

TEX = r"""\documentclass{article}
\title{Sparse Memory Pruning for Language Agents}
\author{Alice Smith\thanks{Equal contribution.} \\ Uni A \and Bob Jones \\ Uni B}
\begin{document}
\maketitle
\begin{abstract}
Language agents keep growing memories. Sparse memory pruning drops rarely read memories and keeps agent accuracy at 73.5 on long tasks.
\end{abstract}
\section{Method}
We drop the lowest 20 percent of memories every 50 steps.
\end{document}
"""

BIB = "@article{ref1, title={Memory Networks}, author={Gina Ross}, year={2015}, doi={10.1/mem}}\n"
YEAR = __import__("datetime").date.today().year


def author(i, name, inst=None):
    return {"author": {"id": f"https://openalex.org/{i}", "display_name": name},
            "institutions": [{"id": f"https://openalex.org/{inst}"}] if inst else []}


def work(i, title, year, authors):
    return {"id": f"https://openalex.org/{i}", "title": title, "publication_year": year, "authorships": authors,
            "abstract_inverted_index": {"Agents": [0], "forget": [1]}}


TEAM = [author(f"T{n}", f"Team Member{n}") for n in range(1, 21)]
SEARCH_TITLE = [work("W1", "Forgetting in Agent Memory", YEAR - 1, [author("C1", "Carol King"), author("A1", "Alice Smith")]),
                work("W2", "Memory Budgets", YEAR - 2, [author("C2", "Dan Brown"), author("C3", "Eve Stone")]),
                work("W3", "A Giant Agent Benchmark", YEAR, TEAM)]
SEARCH_PHRASES = [work("W4", "Pruning Agent Memories", YEAR, [author("C1", "Carol King"), author("C4", "Frank Miller")])]


def profile(i, name, h=20, works=60, inst=None, orcid=None, recent=4):
    return {"id": f"https://openalex.org/{i}", "display_name": name, "display_name_alternatives": [],
            "orcid": orcid, "works_count": works, "cited_by_count": 100, "summary_stats": {"h_index": h},
            "last_known_institutions": [{"id": f"https://openalex.org/{inst}", "display_name": f"Institute {inst}",
                                         "type": "company", "lineage": [f"https://openalex.org/{inst}",
                                                                        "https://openalex.org/I0"]}]
            if inst else [],
            "counts_by_year": [{"year": YEAR - k, "works_count": recent} for k in range(5)]}


PROFILES = {"A1": profile("A1", "Alice Smith", inst="I1"), "C1": profile("C1", "Carol King", h=25,
                                                                         orcid="https://orcid.org/0000-0002-1825-0097"),
            "C2": profile("C2", "Dan Brown"), "C3": profile("C3", "Eve Stone", inst="I1"),
            "C4": profile("C4", "Frank Miller", inst="I2"), "C5": profile("C5", "Gina Ross"),
            "T1": profile("T1", "Team Member1", h=5, works=10, recent=1)}


class Fake:
    """Answers OpenAlex and ORCID requests by URL and parameters; records them."""

    def __init__(self, down=False, author_hits=True):
        self.calls, self.down, self.author_hits = [], down, author_hits

    def __call__(self, url, params=None, as_json=True, tries=3, label="", headers=None):
        params = params or {}
        self.calls.append((url, dict(params)))
        if self.down:
            sources.UNAVAILABLE.add(label)
            return None
        if url.startswith("https://pub.orcid.org/"):
            assert headers == {"Accept": "application/json"}
            return {"researcher-url": [{"url-name": "Home", "url": {"value": "https://carol.example.org"}}]}
        if url.endswith("/works/doi:10.1/mem"):
            return {"id": "https://openalex.org/W9", "title": "Memory Networks"}
        if url.endswith("/works"):
            f = params.get("filter", "")
            if f.startswith("openalex_id:W9"):
                return {"results": [work("W9", "Memory Networks", 2015, [author("C5", "Gina Ross")])]}
            if f.startswith("author.id:"):
                assert "A1" in f
                return {"results": [work("W10", "Joint Work", YEAR - 1, [author("A1", "Alice Smith"),
                                                                         author("C2", "Dan Brown")])],
                        "meta": {"next_cursor": None}}
            if params.get("search") == "Sparse Memory Pruning for Language Agents":
                return {"results": SEARCH_TITLE}
            if params.get("search"):
                return {"results": SEARCH_PHRASES}
        if url.endswith("/authors"):
            if params.get("search"):
                if not self.author_hits:
                    return {"results": []}
                if params["search"] == "Alice Smith":
                    return {"results": [profile("A1", "Alice Smith", inst="I1"), profile("A9", "Alicia Smyth")]}
                return {"results": [profile("A8", "Bob Jonas")]}
            ids = params["filter"].removeprefix("openalex_id:").split("|")
            return {"results": [PROFILES[i] for i in ids if i in PROFILES]}
        raise AssertionError(f"unexpected request {url} {params}")


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "main.tex").write_text(TEX)
    (repo / "refs.bib").write_text(BIB)
    (repo / "workflow" / "project.md").write_text("Kind: method\nClaim: Pruning keeps accuracy.\n")
    (repo / "workflow" / "feedback.csv").write_text(
        "name,openalex,status,date,notes\nGina Ross,C5,declined,2026-01-01,busy\nOld Friend,Z1,sent,2026-01-02,\n")
    return repo


def reply(work_id="W1", body=None):
    body = body or ("Dear Dr. King,\n\nWe are finishing a paper on sparse memory pruning for language agents. "
                    "Your paper \"Forgetting in Agent Memory\" studies how agents forget, which is what our "
                    "pruning rule relies on. Could you tell us whether our experiments support the claim? "
                    "Within two to three weeks would help; a short reply or a decline is fine.\n\n[Your name]")
    return {"subject": "Feedback on a draft about memory pruning", "work": work_id,
            "claim": "Pruning rarely read memories keeps accuracy. It saves memory.",
            "why": "It studies forgetting.", "ask": "Whether the experiments support the claim.",
            "time_frame": "two to three weeks", "body": body}


def run(paper, monkeypatch, replies, fake=None, **kw):
    fake = fake or Fake()
    monkeypatch.setattr(sources, "fetch", fake)
    with script_roles({wf.drafter: replies}) as calls:
        out = json.loads(wf.peer_feedback(str(paper), **kw))
    return out, calls, fake


def test_candidates_conflicts_drafts_and_tracking(paper, monkeypatch):
    draft_dir = paper / "workflow" / "feedback"
    draft_dir.mkdir()
    (draft_dir / "team-member.md").write_text("my own edits")
    bad = reply(body="Dear Carol, renowned expert (carol@uni.edu): your \"Forgetting in Agent Memory\" matters. "
                     "We reach 99.9 accuracy. Thanks, [Your name]")
    out, calls, fake = run(paper, monkeypatch, [bad], count="2", exclude="Frank Miller")
    assert out["status"] == "PASS_WITH_NOTES" and out["authors"] == ["A1"]
    record = json.loads(Path(out["json"]).read_text())
    ranked = [p["id"] for p in record["ranked"]]
    assert ranked[:2] == ["C1", "T1"]
    reasons = {p["id"]: " ".join(p["reasons"]) for p in record["excluded"]}
    assert reasons["A1"] == "an author of the paper"
    assert "co-authored with Alice Smith" in reasons["C2"] and "Joint Work" in reasons["C2"]
    assert "same current institution as Alice Smith: Institute I1" in reasons["C3"]
    assert "same company group as Alice Smith: Institute I2 (Alice Smith: Institute I1)" in reasons["C4"]
    assert "named in exclude" in reasons["C4"] and "declined earlier" in reasons["C5"]
    assert any("Bob Jones: no OpenAlex author" in n for n in record["notes"])
    # one drafter call: T1's draft already exists and is kept as the author left it
    assert len(calls[wf.drafter]) == 1
    ask = calls[wf.drafter][0].text
    assert "Forgetting in Agent Memory" in ask and "Pruning Agent Memories" in ask and "No flattery" in ask
    assert "similar to the paper" in ask
    assert (draft_dir / "team-member.md").read_text() == "my own edits"
    draft = (draft_dir / "carol-king.md").read_text()
    assert "carol@uni.edu" not in draft and "[address]" in draft
    assert "find it on https://carol.example.org" in draft and "https://orcid.org/0000-0002-1825-0097" in draft
    assert "flattery to remove: renowned" in draft and "numbers not in the paper: 99.9" in draft
    assert "no time frame" in draft
    rows = (paper / "workflow" / "feedback.csv").read_text().splitlines()
    assert rows[1] == "Gina Ross,C5,declined,2026-01-01,busy" and rows[2].startswith("Old Friend,Z1,sent")
    assert rows[3].startswith("Carol King,C1,drafted,") and rows[4].startswith("Team Member1,T1,drafted,")
    report = Path(out["report"]).read_text()
    assert "## 排除的人" in report and "named in exclude" in report and "没有发送任何邮件" in report
    phrases = [c[1]["search"] for c in fake.calls if c[1].get("search") and c[0].endswith("/works")]
    assert phrases[0] == "Sparse Memory Pruning for Language Agents" and len(phrases) == 2
    trail = json.loads(Path(out["run"]).read_text())
    assert [s["stage"] for s in trail["stages"]] == ["gather", "select", "draft", "track", "report"]


def test_second_run_keeps_drafts_and_author_status(paper, monkeypatch):
    run(paper, monkeypatch, [reply()], count="1")
    csv_path = paper / "workflow" / "feedback.csv"
    csv_path.write_text(csv_path.read_text().replace("Carol King,C1,drafted", "Carol King,C1,sent"))
    before = (paper / "workflow" / "feedback" / "carol-king.md").read_text()
    out, calls, _ = run(paper, monkeypatch, [], count="1")
    assert calls.get(wf.drafter) is None or not calls[wf.drafter]
    assert (paper / "workflow" / "feedback" / "carol-king.md").read_text() == before
    assert "Carol King,C1,sent" in csv_path.read_text() and csv_path.read_text().count("C1") == 1
    assert out["drafts"][0].endswith("carol-king.md")


def test_clean_draft_without_authors_checks_exclude_only(paper, monkeypatch):
    (paper / "main.tex").write_text(TEX.replace(r"\author{Alice Smith\thanks{Equal contribution.} \\ Uni A \and "
                                                r"Bob Jones \\ Uni B}", ""))
    out, _, fake = run(paper, monkeypatch, [reply()], count="1")
    record = json.loads(Path(out["json"]).read_text())
    assert out["authors"] == [] and any("`exclude` only" in n for n in record["notes"])
    assert not any("author.id:" in c[1].get("filter", "") for c in fake.calls)
    assert "A1" in [p["id"] for p in record["ranked"]]           # without authors nobody is a co-author
    draft = (paper / "workflow" / "feedback" / "carol-king.md").read_text()
    assert "发送前要改" not in draft and "Subject: Feedback on a draft" in draft
    assert "没有解析到 OpenAlex 作者" in Path(out["report"]).read_text()


def test_project_authors_and_unresolved_names(paper, monkeypatch):
    (paper / "workflow" / "project.md").write_text("Kind: method\nClaim: Pruning keeps accuracy.\nAuthors: A1, Zed Q\n")
    (paper / "main.tex").write_text(TEX.replace(r"\author{Alice Smith\thanks{Equal contribution.} \\ Uni A \and "
                                                r"Bob Jones \\ Uni B}", ""))
    out, _, _ = run(paper, monkeypatch, [reply()], count="1", fake=Fake(author_hits=False))
    record = json.loads(Path(out["json"]).read_text())
    assert out["authors"] == ["A1"] and any("Zed Q: no OpenAlex author" in n for n in record["notes"])


def test_cited_work_must_be_theirs(paper, monkeypatch):
    out, _, _ = run(paper, monkeypatch, [reply(work_id="W2"), reply(work_id="W2")], count="1")
    record = json.loads(Path(out["json"]).read_text())
    assert record["ranked"][0]["draft_state"] == "failed" and out["status"] == "FAIL"
    assert not (paper / "workflow" / "feedback" / "carol-king.md").exists()


def test_sources_down_is_not_a_crash(paper, monkeypatch):
    out, calls, _ = run(paper, monkeypatch, [], fake=Fake(down=True))
    assert out["status"] == "FAIL" and "OpenAlex" in out["unavailable_sources"]
    assert "OpenAlex" in Path(out["report"]).read_text()


def test_plain_helpers():
    assert people.latex_authors(r"\author{Jane A. Doe$^{1}$, John Roe\thanks{x} \\ MIT \and Anonymous}") == \
        ["Jane A. Doe", "John Roe"]
    assert people.same_name("J. Devlin", "Jacob Devlin") and not people.same_name("Jacob Devlin", "Jacob Devin")
    assert people.key_phrases("Sparse Memory Pruning", "Sparse memory pruning drops rarely read memories.")[0] == \
        "sparse memory pruning"
    assert people.exclusions("A123, https://openalex.org/A456, Jane Doe") == ({"A123", "A456"}, ["Jane Doe"])
    assert wf.best_page([{"name": "LinkedIn", "url": "https://www.linkedin.com/in/x"},
                         {"name": "Professional website", "url": "https://lab.example.org/~x"}]) == \
        "https://lab.example.org/~x"
    with pytest.raises(ValueError):
        people.parse_count("0")
    with pytest.raises(ValueError):
        wf.peer_feedback("/nonexistent/folder")
