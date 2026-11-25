"""The whole entry with the network replaced and every role scripted."""

import json

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.survey import fulltext, presets, search
from auto_research.tasks.survey import workflow as wf

PAPERS = [  # title, date, line the screener gives, decision
    ("Neural Machine Translation by Jointly Learning to Align and Translate", "2014-09-01", "L1", "core"),
    ("Effective Approaches to Attention-based Neural Machine Translation", "2015-08-17", "L1", "core"),
    ("Show, Attend and Tell", "2015-02-10", "L1", "core"),
    ("Attention Is All You Need", "2017-06-12", "L2", "core"),
    ("Generating Long Sequences with Sparse Transformers", "2019-04-23", "L2", "core"),
    ("Attention Mechanisms: A Survey", "2021-01-01", "none", "survey"),
    ("Cooking with attention", "2020-01-01", "none", "exclude"),
]
SURVEY_HTML = "".join(f'<section id="S{i}"><h2 class="ltx_title">{i} {h}</h2><p>text {h}</p></section>'
                      for i, h in enumerate(["Introduction", "Soft attention", "Self-attention", "Efficient attention"], 1))
SCOPE = {"title": "注意力机制", "prerequisites": "RNN、seq2seq 与反向传播", "boundary": "序列模型中的注意力",
         "questions": ["注意力解决了什么问题", "代价是什么"],
         "lines": [{"name": "软对齐", "definition": "RNN 上的注意力", "boundary": "有循环",
                    "queries": ["neural machine translation attention", "soft alignment"]},
                   {"name": "自注意力", "definition": "去掉循环", "boundary": "无循环",
                    "queries": ["self-attention transformer", "sparse attention"]},
                   {"name": "注意力解释", "definition": "注意力权重能否解释模型", "boundary": "分析工作",
                    "queries": ["attention explanation", "attention interpretability"]}],
         "survey_queries": ["attention mechanism"]}


@pytest.fixture
def offline(monkeypatch):
    found = [search.candidate(t, f"Abstract of {t}.", d, ["Ann Author", "Bo Second"], "Conf", arxiv=f"{1400 + i}.0000{i}",
                              citations=100 - i, found_by="arxiv") for i, (t, d, _, _) in enumerate(PAPERS)]
    monkeypatch.setattr(search, "SEARCHES", {"arxiv": lambda q, n, s: [dict(c, found_by=list(c["found_by"])) for c in found]})
    monkeypatch.setattr(search, "openalex_search", lambda *a, **k: [])
    monkeypatch.setattr(search, "arxiv_search", lambda *a, **k: [])
    monkeypatch.setattr(search, "yearly_counts", lambda q, since: {2023: 10, 2024: 25, 2025: 40})
    monkeypatch.setattr(fulltext, "fetch", lambda url, **k: SURVEY_HTML)
    body = [{"locator": "§3 Method", "heading": "Method", "text": "We score each source position."}]
    monkeypatch.setattr(fulltext, "body", lambda arxiv, budget=12000: (body, f"https://arxiv.org/html/{arxiv}")
                        if arxiv != "1404.00004" else ([], ""))


def screen_reply():
    return {"decisions": [{"id": f"c{i}", "decision": dec, "line": line, "kind": "survey" if dec == "survey" else "method",
                           "reason": "r"} for i, (_, _, line, dec) in enumerate(PAPERS, 1)]}


def read_reply(ids, loc="§3 Method"):
    return {"notes": [{"id": p, "problem": f"problem of {p}", "mechanism": f"mechanism of {p}",
                       "builds_on": ["p1"] if p != "p1" else [], "difference": "" if p == "p1" else "differs",
                       "findings": [{"text": f"finding of {p}", "locator": f"{p} {loc if p != 'p5' else 'abstract'}"}],
                       "limitations": [{"text": f"limit of {p}", "locator": f"{p} abstract", "stated": True}],
                       "benchmarks": ["WMT14"]} for p in ids]}


def chapter(title, ids):
    return {"title": title, "paragraphs": [f"开头 [{ids[0]}]。", f"后来 [{', '.join(ids)}]。"],
            "table": {"caption": "打分函数", "columns": ["打分"], "rows": [{"paper": ids[0], "cells": ["加性"]}]}}


def script():
    return {
        wf.planner: [SCOPE, {"mapping": [{"heading": "S1: §2 Soft attention", "line": "L1"}], "gaps": []}],
        wf.screener: [screen_reply()],
        wf.reader: [read_reply(["p1", "p2", "p3"]), read_reply(["p4", "p5"])],
        wf.writer: [chapter("软对齐", ["p1", "p2", "p3"]), chapter("自注意力", ["p4", "p5"]),
                    chapter("软对齐与打分", ["p1", "p2", "p3"]),
                    {"paragraphs": ["领域的问题 [p1]。"], "edges": [{"from": "L1", "to": "L2", "label": "去掉循环"}]}],
        wf.auditor: [{"issues": [{"paragraph": "P2", "quote": "后来", "problem": "no_mechanism", "note": "没说为什么"}]},
                     {"issues": []}, {"issues": []}],
        wf.analyst: [{"summary": "活动转向自注意力 [p4]。", "rising": [{"line": "L2", "text": "增长 [p4]", "papers": ["p4"]}],
                      "new": [], "saturated": []},
                     {"cards": [{"title": "长序列", "problem": "O(n²) [p4]", "evidence": ["p4.1", "p5.1"],
                                 "active": ["p5"], "cost": "单卡", "first_step": "比较稀疏模式"}]}],
    }


def run(tmp_path, **kw):
    return json.loads(wf.research_survey("attention in sequence models", out_dir=str(tmp_path), min_papers="5",
                                         since="2014", recent_months="200", **kw))


def test_whole_survey(offline, tmp_path):
    with script_roles(script()) as calls:
        out = run(tmp_path)
    assert out["status"] == "PASS", out["notes"]
    assert out["core"] == 5 and out["chapters"] == 2

    screen = calls[wf.screener][0].text
    assert "Cooking with attention" in screen and "only a keyword in common" in screen
    read = calls[wf.reader][0].text
    assert "We score each source position." in read and '"<paper id> <locator>"' in read
    first_chapter = calls[wf.writer][0].text
    assert "never used alone" in first_chapter            # writing rules reach the writer
    assert "mechanism of p2" in first_chapter and "mechanism of p4" not in first_chapter
    revise = calls[wf.writer][2].text
    assert "没说为什么" in revise                           # the audit reaches the rewrite
    directions = calls[wf.analyst][1].text
    assert "limit of p4" in directions

    survey = json.load(open(tmp_path / next(p.name for p in tmp_path.iterdir()) / "survey.json", encoding="utf-8"))
    assert [c["title"] for c in survey["chapters"]] == ["软对齐与打分", "自注意力"]
    assert {p["pid"]: p.get("read_level") for p in survey["papers"]}["p5"] == "abstract"
    labels = {p["title"]: p["label"] for p in survey["papers"]}
    assert labels["Show, Attend and Tell"] == "Author et al. 2015a"      # same author and year, by date
    assert labels["Effective Approaches to Attention-based Neural Machine Translation"] == "Author et al. 2015b"
    assert survey["directions"]["cards"][0]["crowding"] == 1
    flow = survey["retrieval"]["flow"]
    assert flow["included"]["core"] == 5 and flow["unique"] >= flow["screened"] >= flow["excluded"] >= 1
    report = open(out["report"], encoding="utf-8").read()
    assert "筛选流程（PRISMA 2020）" in report and "覆盖度" in report

    page = open(out["page"], encoding="utf-8").read()
    assert "<h1>注意力机制</h1>" in page and 'data-p="p4"' in page and "去掉循环" in page
    assert "{body}" not in page and "{papers_json}" not in page
    record = json.load(open(out["run"], encoding="utf-8"))
    stages = [s["stage"] for s in record["stages"]]
    assert stages[:4] == ["scope", "retrieve", "screen", "snowball"]
    assert stages.count("audit") == 3 and "revise chapter" in stages and stages[-1] == "render"
    assert set(record["roles"]) == set(wf.ROLES)


def test_too_few_papers_fail(offline, tmp_path):
    with script_roles(script()):
        out = json.loads(wf.research_survey("attention", out_dir=str(tmp_path), min_papers="70", since="2014",
                                            recent_months="200"))
    assert out["status"] == "FAIL" and "fewer than min_papers=70" in out["notes"][0]


def test_related_work_needs_our_work():
    with pytest.raises(ValueError):
        wf.research_survey("attention", purpose="related_work")
    with pytest.raises(ValueError):
        wf.research_survey("attention", purpose="review")
    with pytest.raises(ValueError):
        wf.research_survey("attention", min_papers="many")


def test_related_work_writes_latex(offline, tmp_path):
    roles = script()
    roles[wf.writer].append({"paragraphs": [{"line": "L1", "text": "Soft alignment [p1, p2]. Ours differs."}]})
    with script_roles(roles):
        out = run(tmp_path, purpose="related_work", our_work="A survey workflow.")
    tex = open(out["related_work"], encoding="utf-8").read()
    assert "\\cite{" in tex and "\\paragraph{软对齐}" in tex
    assert "@article{" in open(out["bib"], encoding="utf-8").read()


def test_a_preset_fixes_the_lines_and_the_first_year(offline, tmp_path, monkeypatch):
    searched = []
    found = search.SEARCHES["arxiv"]
    monkeypatch.setattr(search, "SEARCHES", {"arxiv": lambda q, n, s: searched.append((q, s)) or found(q, n, s)})
    roles = script()
    roles[wf.planner][0] = {k: v for k, v in SCOPE.items() if k != "lines"}
    with script_roles(roles) as calls:
        out = json.loads(wf.research_survey("LLM knowledge", out_dir=str(tmp_path), min_papers="5",
                                            recent_months="200", preset="llm_knowledge"))
    assert out["status"] == "PASS", out["notes"]
    tracks = presets.PRESETS["llm_knowledge"].tracks
    scope = calls[wf.planner][0].text
    assert all(t.name in scope and t.definition in scope for t in tracks)
    assert "Our research lines are fixed" in calls[wf.planner][1].text
    assert [q for q, _ in searched] == [q for t in tracks for q in t.queries]      # in priority order
    assert {s for _, s in searched} == {2025}
    survey = json.load(open(tmp_path / next(p.name for p in tmp_path.iterdir()) / "survey.json", encoding="utf-8"))
    assert survey["preset"] == "llm_knowledge" and survey["since"] == 2025
    assert [l["name"] for l in survey["lines"]] == [t.name for t in tracks]
    record = json.load(open(out["run"], encoding="utf-8"))
    assert record["args"]["preset"] == "llm_knowledge"
    assert [s["stage"] for s in record["stages"]][:2] == ["scope", "preset"]


def test_a_preset_admits_no_new_line_and_unknown_presets_raise(offline, tmp_path):
    roles = script()
    new_line = {"mapping": [], "gaps": [{"heading": "S1: §2 Soft attention", "line": "new", "name": "x",
                                         "definition": "y", "queries": ["q"]}]}
    roles[wf.planner] = [{k: v for k, v in SCOPE.items() if k != "lines"}, new_line, new_line]   # one retry
    with script_roles(roles), pytest.raises(ValueError):
        wf.research_survey("LLM knowledge", out_dir=str(tmp_path), preset="llm_knowledge")
    with pytest.raises(ValueError, match="preset must be one of llm_knowledge"):
        wf.research_survey("LLM knowledge", preset="llm")
