"""The scouting tasks with every request replaced by a fake fetch or post."""

import csv
import io
import json
from datetime import date

import pytest

from auto_research.tasks.citations import sources
from auto_research.tasks.scouting import after_release, personal_rank, topic_trends, venue_pick
from auto_research.tasks.scouting import published as ar
from auto_research.tasks.scouting import personal, trends, venues, yamlite
from auto_research.tasks.survey import search

ICLR_YML = """# ccfddl file
- title: ICLR
  description: International Conference on Learning Representations
  sub: AI
  rank:
    ccf: A
    core: A*
  dblp: iclr
  confs:
    - year: 2026
      id: iclr26
      link: https://iclr.cc/Conferences/2026
      timeline:
        - abstract_deadline: '2025-09-19 23:59:59'
          deadline: '2025-09-24 23:59:59'
      timezone: AoE
      date: April 23-25, 2026
      place: Rio de Janeiro, Brazil
    - year: 2027
      id: iclr27
      link: https://iclr.cc/Conferences/2027
      timeline:
        - abstract_deadline: '2026-09-18 23:59:59'
          deadline: '2026-09-25 23:59:59'  # main
          comment: "Main track: see #faq"
      timezone: AoE
      tags: [ml, "deep learning"]
"""


def fake(monkeypatch, handler):
    calls = []

    def fetch(url, params=None, as_json=True, tries=3, label="", headers=None):
        calls.append((url, dict(params or {})))
        return handler(url, params or {})

    monkeypatch.setattr(sources, "fetch", fetch)
    monkeypatch.setattr(search, "fetch", fetch)
    return calls


def repo(tmp_path, bib="", tex="", **workflow):
    paper = tmp_path / "paper"
    (paper / "workflow").mkdir(parents=True)
    if bib:
        (paper / "references.bib").write_text(bib)
    if tex:
        (paper / "main.tex").write_text(tex)
    for name, text in workflow.items():
        (paper / "workflow" / name.replace("_", ".", 1)).write_text(text)
    return paper


# YAML and deadlines

def test_tiny_yaml_parser_reads_ccfddl():
    data = yamlite.parse(ICLR_YML)
    conf = data[0]
    assert conf["title"] == "ICLR" and conf["rank"] == {"ccf": "A", "core": "A*"}
    assert conf["confs"][1]["timeline"][0]["deadline"] == "2026-09-25 23:59:59"
    assert conf["confs"][1]["timeline"][0]["comment"] == "Main track: see #faq"
    assert conf["confs"][1]["tags"] == ["ml", "deep learning"]
    assert conf["confs"][0]["link"] == "https://iclr.cc/Conferences/2026"


def test_next_deadline_after_today():
    data = yamlite.parse(ICLR_YML)
    d = venues.upcoming(data, date(2026, 1, 1))
    assert d["deadline"] == "2026-09-25 23:59:59" and d["timezone"] == "AoE" and d["year"] == 2027
    assert venues.upcoming(data, date(2025, 1, 1))["year"] == 2026
    assert venues.upcoming(data, date(2027, 1, 1)) is None
    assert venues.upcoming(yamlite.load(ICLR_YML), date(2026, 1, 1))["year"] == 2027


# venue_pick

BIB = r"""@inproceedings{a, title={Alpha Memory}, doi={10.1/a}, year={2024}}
@inproceedings{b, title={Beta Planning}, doi={10.1/b}, year={2023}}
@article{c, title={Gamma Preprint}, doi={10.1/c}, year={2024}}
@article{d, title={Delta Journal}, doi={10.1/d}, year={2015}}
"""
TEX = r"""\documentclass{article}
\begin{document}
\title{Memory Agents That Plan}
\maketitle
\begin{abstract}We study memory for planning agents.\end{abstract}
\end{document}
"""


def src(name, kind="conference"):
    return {"source": {"display_name": name, "type": kind}}


class Day(date):
    @classmethod
    def today(cls):
        return cls(2026, 3, 1)


def test_venue_pick(tmp_path, monkeypatch):
    paper = repo(tmp_path, BIB, TEX)
    monkeypatch.setattr(venues, "date", Day)
    year = 2026
    works = {"10.1/a": ("W1", year, src("International Conference on Learning Representations")),
             "10.1/b": ("W2", year - 1, src("Neural Information Processing Systems")),
             "10.1/c": ("W3", year, src("arXiv (Cornell University)", "repository")),
             "10.1/d": ("W4", year - 10, src("Journal of Obscure Studies", "journal"))}
    similar = [src("Advances in Neural Information Processing Systems")] * 5 + \
              [src("International Conference on Machine Learning")] * 3 + [src("Some Workshop")] * 2

    def handler(url, params):
        if url.startswith("https://api.openalex.org/works/doi:"):
            w = works.get(url.split("doi:", 1)[1])
            return {"id": f"https://openalex.org/{w[0]}", "publication_year": w[1], "primary_location": w[2]} if w else None
        if url == "https://api.openalex.org/works" and "search" in params:
            seen["searches"].append(params["search"])           # the title, then the abstract's first sentence
            return {"results": [{"id": f"W{i}", "primary_location": p} for i, p in enumerate(similar)]}
        if url == "https://api.openalex.org/works" and params.get("filter", "").startswith("openalex:"):
            return {"results": [{"id": f"https://openalex.org/{k}", "locations": []}
                                for k in params["filter"].split(":", 1)[1].split("|")]}
        if url == "https://api.openalex.org/works" and params.get("filter", "").startswith("title.search:"):
            return {"results": []}
        if url.startswith("https://raw.githubusercontent.com/ccfddl/"):
            return ICLR_YML if url.endswith("AI/iclr.yml") else None
        raise AssertionError(url)

    seen = {"searches": []}
    fake(monkeypatch, handler)
    out = json.loads(venue_pick(str(paper), out_dir=str(tmp_path / "out")))
    record = json.loads(open(out["json"]).read())
    assert "Memory Agents That Plan" in seen["searches"][0] and len(seen["searches"]) == 2
    names = [c["name"] for c in record["candidates"]]
    assert names[0] == "NeurIPS" and {"ICLR", "ICML"} <= set(names)
    assert "arXiv (Cornell University)" not in names and 3 <= len(names) <= 5
    neurips = record["candidates"][0]
    assert neurips["family"] == "neurips" and neurips["page_limit"] == 9 and neurips["double_blind"] is True
    assert neurips["similar_share"] == 0.5 and len(neurips["openalex_names"]) == 2
    iclr = next(c for c in record["candidates"] if c["name"] == "ICLR")
    assert iclr["next_deadline"]["deadline"] == "2026-09-25 23:59:59"
    assert next(c for c in record["candidates"] if c["name"] == "ICML")["next_deadline"] is None
    obscure = next(c for c in record["candidates"] if c["name"] == "Journal of Obscure Studies")
    assert obscure["family"] == "" and obscure["reference_share"] < iclr["reference_share"]
    report = open(out["report"]).read()
    assert "NeurIPS" in report and "不改 workflow/venue.md" in report
    assert not (paper / "workflow" / "venue.md").exists()


# topic_trends

def test_trend_labels():
    assert trends.label(10, 2.0) == "冷门"
    assert trends.label(400, 0.3) == "升温"
    assert trends.label(5000, 0.3) == "升温"
    assert trends.label(5000, 0.05) == "拥挤"
    assert trends.label(300, 0.0) == "平稳"
    assert trends.label(300, None) == "平稳"


def test_topic_trends(tmp_path, monkeypatch):
    paper = repo(tmp_path, workflow_md="", watch_md="# Watch\nQueries: memory agents; old topic\n")
    last = date.today().year - 1
    totals = {y: 1_000_000 for y in range(2016, last + 2)}
    hot = {y: 100 * 2 ** (y - 2016) for y in range(2016, last + 2)}
    cold = {y: 20 for y in range(2016, last + 2)}

    def handler(url, params):
        f = params.get("filter", "")
        if params.get("group_by") == "publication_year":
            series = totals if "title_and_abstract" not in f else hot if "memory agents" in f else cold
            return {"group_by": [{"key": str(y), "count": c} for y, c in series.items()]}
        if params.get("group_by") == "topics.id":
            assert f"to_publication_date:{last}-12-31" in f
            return {"group_by": [{"key": "https://openalex.org/T1", "key_display_name": "Agents", "count": 9}]}
        raise AssertionError(url)

    fake(monkeypatch, handler)
    out = json.loads(topic_trends(str(paper), out_dir=str(tmp_path / "out")))
    assert out["labels"] == {"memory agents": "升温", "old topic": "冷门"}
    record = json.loads(open(out["json"]).read())
    q = record["queries"][0]
    assert q["growth"] == 1.0 and q["co_topics"][0]["topic"] == "Agents"
    assert q["series"][-1]["partial"] is True
    md = open(out["report"]).read()
    assert md.count("<svg") == 2 and "标签规则" in md
    with pytest.raises(ValueError):
        topic_trends(str(paper), since="20x")


# after_release

def test_stance_rules():
    assert ar.stance("However, their method fails to generalize to long contexts.") == "contrasting"
    assert ar.stance("We could not reproduce the gains reported by [3].") == "contrasting"
    assert ar.stance("Our findings are consistent with [3].") == "supporting"
    assert ar.stance("This confirms the observation of [3].") == "supporting"
    assert ar.stance("We obtain similar numbers to [3].", ["result"]) == "supporting"
    assert ar.stance("We obtain similar numbers to [3].", ["background"]) == "mentioning"
    assert ar.stance("Memory agents [3] store episodes.", ["background"]) == "mentioning"
    assert ar.stance("Results are not consistent with [3].") == "contrasting"
    assert ar.needs("There is a typo in Eq. 3", "alphaxiv", "mentioning") == "erratum"
    assert ar.needs("Nice work!", "alphaxiv", "mentioning", answered=True) == ""
    assert ar.needs("Why this choice?", "openreview", "mentioning") == "reply"
    assert ar.needs("Memory agents [3]", "context", "mentioning") == ""
    assert ar.needs("It stores an incorrect fact [3]", "context", "mentioning") == ""
    assert ar.needs("However, [3] has a bug in its loss", "context", "contrasting") == "erratum"


def test_identify():
    assert ar.identify("https://openalex.org/W123456") == {"openalex": "W123456"}
    assert ar.identify("https://doi.org/10.1000/XYZ") == {"doi": "10.1000/xyz"}
    assert ar.identify("arXiv:2401.00001v2")["arxiv"] == "2401.00001"
    assert ar.forum_id("https://openreview.net/forum?id=AbCdEf123") == "AbCdEf123"


def test_after_release(tmp_path, monkeypatch):
    paper = repo(tmp_path, project_md="# Project\nKind: method\nClaim: x\nPublished: arXiv:2401.00001\n"
                                      "OpenReview: https://openreview.net/forum?id=Forum12345\n")

    def handler(url, params):
        if url == "https://api.openalex.org/works/doi:10.48550/arxiv.2401.00001":
            return {"id": "https://openalex.org/W9", "doi": "https://doi.org/10.48550/arxiv.2401.00001",
                    "title": "Our Paper", "locations": []}
        if url == "https://api.openalex.org/works":
            assert params["filter"] == "cites:W9"
            return {"results": [
                {"id": "https://openalex.org/W20", "doi": "https://doi.org/10.1/x", "title": "Citer With Context",
                 "publication_date": "2026-05-01", "locations": []},
                {"id": "https://openalex.org/W21", "doi": None, "title": "Silent Citer",
                 "publication_date": "2026-06-01", "locations": []}]}
        if url.endswith("/paper/ARXIV:2401.00001/citations"):
            return {"data": [{"contexts": ["However, [9] fails to scale beyond 8 agents.",
                                           "Our results are consistent with [9]."], "intents": ["result"],
                              "citingPaper": {"paperId": "p1", "title": "Citer With Context", "year": 2026,
                                              "externalIds": {"DOI": "10.1/x"}, "url": "https://s2/p1"}}]}
        if url == "https://api.alphaxiv.org/papers/v3/2401.00001":
            return {"groupId": "g-1"}
        if url == "https://api.alphaxiv.org/papers/v3/legacy/g-1/comments":
            return [{"id": "c1", "title": "Typo", "body": "There is a typo in Eq. 3", "isAuthor": False,
                     "date": "2026-07-01T00:00:00Z", "authorResponded": False, "responses": []},
                    {"id": "c2", "title": None, "body": "Thanks for the reply", "isAuthor": True, "responses": []}]
        if url == "https://api2.openreview.net/notes":
            return {"notes": [{"id": "Forum12345", "invitations": ["X/-/Submission"], "content": {}},
                              {"id": "n1", "invitations": ["X/-/Public_Comment"], "signatures": ["~Someone1"],
                               "content": {"comment": {"value": "How does this compare to RAG?"}}, "cdate": 1767225600000},
                              {"id": "n2", "invitations": ["X/-/Official_Comment"], "signatures": ["X/Authors"],
                               "replyto": "n3", "content": {"comment": {"value": "Answer"}}}]}
        raise AssertionError(url)

    fake(monkeypatch, handler)
    out = json.loads(after_release(str(paper), out_dir=str(tmp_path / "out")))
    assert out["new"] == 5 and out["counts"] == {"supporting": 1, "contrasting": 1, "mentioning": 3}
    record = json.loads(open(out["json"]).read())
    assert [r["needs"] for r in record["new"]][:3] == ["erratum", "reply", "reply"]
    assert record["new"][0]["kind"] == "alphaxiv"
    assert {r["kind"] for r in record["new"]} == {"context", "citing", "alphaxiv", "openreview"}
    assert not any(r["title"] == "Citer With Context" and r["kind"] == "citing" for r in record["new"])
    state = paper / "workflow" / "after_release.csv"
    rows = list(csv.DictReader(io.StringIO(state.read_text())))
    assert list(rows[0])[:5] == ["id", "kind", "stance", "quote", "handled"]
    # the author handles one; a second run adds nothing and keeps the note
    rows[0]["handled"] = "replied"
    with state.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    again = json.loads(after_release(str(paper), out_dir=str(tmp_path / "out")))
    assert again["new"] == 0 and again["needs_action"] == out["needs_action"] - (1 if rows[0]["needs"] else 0)
    assert "replied" in state.read_text()


def test_after_release_needs_a_work(tmp_path):
    with pytest.raises(ValueError):
        after_release(str(repo(tmp_path)))


# personal_rank

def test_logistic_regression_on_toy_vectors():
    X = [[1, 0.1], [0.9, 0.2], [1.1, -0.1], [0.8, 0], [-1, 0.1], [-0.9, -0.2], [-1.2, 0], [-0.8, 0.1]]
    y = [1, 1, 1, 1, 0, 0, 0, 0]
    model = personal.fit(X, y)
    assert personal.predict(model, [1, 0]) > 0.8
    assert personal.predict(model, [-1, 0]) < 0.2
    # unbalanced labels still separate
    model = personal.fit(X[:5] + X[4:] * 3, [1, 1, 1, 1, 0] + [0] * 12)
    assert personal.predict(model, [1, 0]) > 0.5


def test_labels_from_watch_rows():
    assert personal.label({"handled": "cited in related work"}) == 1
    assert personal.label({"handled": "无关"}) == 0
    assert personal.label({"handled": "not relevant, skip"}) == 0
    assert personal.label({"handled": "", "relevance": "0"}) == 0
    assert personal.label({"handled": "cited", "relevance": "no"}) == 0
    assert personal.label({"handled": ""}) is None


WATCH_HEADER = ["id", "title", "url", "date", "claim", "verdict", "reason", "handled", "note"]


def watch_csv(rows):
    f = io.StringIO()
    w = csv.DictWriter(f, fieldnames=WATCH_HEADER)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in WATCH_HEADER})
    return f.getvalue()


def test_personal_rank_needs_labels(tmp_path):
    rows = [{"id": f"arXiv:2401.0000{i}", "title": f"Memory {i}", "handled": "cited"} for i in range(3)]
    paper = repo(tmp_path, watch_csv=watch_csv(rows))
    out = json.loads(personal_rank(str(paper), out_dir=str(tmp_path / "out")))
    assert out["need_more"] == {"relevant": 2, "not_relevant": 5}
    assert "还需要 2 个相关" in open(out["report"]).read()


def test_personal_rank(tmp_path, monkeypatch):
    pos = [{"id": f"arXiv:2401.{i:05d}", "title": f"Memory agents planning {i}", "handled": "cited"} for i in range(6)]
    neg = [{"id": f"arXiv:2402.{i:05d}", "title": f"Protein folding chemistry {i}", "handled": "无关"} for i in range(6)]
    todo = [{"id": "arXiv:2403.00001", "title": "Agents with memory for planning", "note": "mine"},
            {"id": "arXiv:2403.00002", "title": "Protein chemistry folding study"},
            {"id": "t:no-ids", "title": "Long term memory for agents planning"}]
    paper = repo(tmp_path, watch_csv=watch_csv(pos + neg + todo), watch_md="# Watch\nQueries: x\nCategories: cs.CL\n")
    today = date.today().isoformat()
    feed = f"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/2411.99999v1</id><title>Planning agents with episodic memory</title>
<summary>memory agents</summary><published>{today}T00:00:00Z</published><arxiv:primary_category term="cs.CL"/></entry>
<entry><id>http://arxiv.org/abs/2401.00001v1</id><title>Old one</title><summary>x</summary>
<published>2001-01-01T00:00:00Z</published></entry></feed>"""

    def handler(url, params):
        assert url == "https://export.arxiv.org/api/query" and params["search_query"] == "cat:cs.CL"
        return feed

    vec = {}
    for r in pos:
        vec["ARXIV:" + r["id"][6:]] = [1.0, 0.1]
    for r in neg:
        vec["ARXIV:" + r["id"][6:]] = [-1.0, 0.1]
    vec["ARXIV:2403.00001"] = [0.9, 0.0]
    vec["ARXIV:2403.00002"] = [-0.9, 0.0]

    def post(url, params, body, label="", headers=None):
        assert url == personal.S2_BATCH and "embedding.specter_v2" in params["fields"]
        return [{"embedding": {"vector": vec[i]}} if i in vec else None for i in body["ids"]]

    fake(monkeypatch, handler)
    monkeypatch.setattr(personal, "post", post)
    monkeypatch.setattr(search, "ARXIV_GAP", 0)
    out = json.loads(personal_rank(str(paper), out_dir=str(tmp_path / "out")))
    record = json.loads(open(out["json"]).read())
    by_key = {p["key"]: p for p in record["ranked"]}
    assert by_key["arXiv:2403.00001"]["score"] > 0.8 > by_key["arXiv:2403.00002"]["score"]
    assert by_key["arXiv:2403.00001"]["method"] == "specter2"
    assert by_key["t:no-ids"]["method"] == "tfidf" and by_key["arXiv:2411.99999"]["method"] == "tfidf"
    assert by_key["t:no-ids"]["score"] > by_key["arXiv:2403.00002"]["score"]
    assert "arXiv:2401.00001" not in by_key
    rows = list(csv.DictReader(io.StringIO((paper / "workflow" / "watch.csv").read_text())))
    assert list(rows[0]) == WATCH_HEADER + ["score"]
    scored = {r["id"]: r for r in rows}
    assert float(scored["arXiv:2403.00001"]["score"]) > 0.8 and scored["arXiv:2403.00001"]["note"] == "mine"
    assert scored["arXiv:2401.00000"]["score"] == "" and scored["arXiv:2401.00000"]["handled"] == "cited"
    assert "个性化排序" in open(out["report"]).read()


def test_watch_rows_typed_by_hand_still_get_a_semantic_scholar_id():
    assert personal.s2_id("arXiv:2102.12452v2") == "ARXIV:2102.12452"
    assert personal.s2_id("2102.12452v3") == "ARXIV:2102.12452"
    assert personal.s2_id("hep-th/9901001") == "ARXIV:hep-th/9901001"
    assert personal.s2_id("10.1162/coli_a_00422") == "DOI:10.1162/coli_a_00422"
    assert personal.s2_id("W123", "https://arxiv.org/pdf/2207.04153v1.pdf") == "ARXIV:2207.04153"
    assert personal.s2_id("", "https://doi.org/10.1/x") == "DOI:10.1/x"
    assert personal.s2_id("t:some title", "https://example.org") == ""


def test_a_rate_limited_post_is_tried_again(monkeypatch):
    import io
    import urllib.error

    from auto_research.tasks.scouting import common

    calls = []

    def urlopen(request, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, None)
        return io.BytesIO(b"[1, 2]")

    monkeypatch.setattr(common.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(common, "RETRY_WAIT", 0)
    assert common.post("https://example.org/x", {}, {"ids": []}, label="S") == [1, 2]
    assert len(calls) == 2


def test_vectors_are_centered_before_they_are_compared():
    out = personal.centered({0: [1.0, 3.0], 1: [3.0, 3.0]})
    assert out == {0: [-1.0, 0.0], 1: [1.0, 0.0]}
    assert personal.centered({0: [1.0]}) == {0: [1.0]}
