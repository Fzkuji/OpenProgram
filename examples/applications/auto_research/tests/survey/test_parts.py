"""Retrieval merging, section parsing, reply checks and rendering, without network."""

import json

import pytest

from auto_research.tasks.survey import fulltext, render, replies, search

HTML = """<html><body><h1 class="ltx_title">T</h1>
<section id="S1" class="ltx_section"><h2 class="ltx_title ltx_title_section">1 Introduction</h2>
<p>Seq2seq compresses the source into one vector <math alttext="c"><mi>c</mi></math>.</p>
<span class="ltx_note">footnote text <span>nested</span></span><p>Second paragraph.</p></section>
<section id="S2" class="ltx_section"><h2 class="ltx_title ltx_title_section">2 Method</h2>
<figure><p>caption dropped</p></figure><p>We score every position.</p>
<section id="S2.SS1" class="ltx_subsection"><h3 class="ltx_title">2.1 Scoring</h3><p>Additive scoring.</p></section></section>
<section id="S3" class="ltx_section"><h2 class="ltx_title">References</h2><p>[1] x</p></section>
</body></html>"""


def test_sections_keep_text_and_locators():
    found = fulltext.sections(HTML)
    assert [s["locator"] for s in found] == ["§1 Introduction", "§2 Method", "§2.1 Scoring"]
    assert "one vector c" in found[0]["text"] and "Second paragraph" in found[0]["text"]
    assert "footnote" not in found[0]["text"] and "caption dropped" not in found[1]["text"]


def test_pick_keeps_introduction_and_budget():
    found = [{"locator": f"§{i} {h}", "heading": h, "text": "x " * 2000}
             for i, h in enumerate(["Introduction", "Related", "Method", "Experiments", "Conclusion"], 1)]
    picked = fulltext.pick(found, 6000)
    assert [p["heading"] for p in picked] == ["Introduction", "Conclusion"]
    assert all(len(p["text"]) <= fulltext.PER_SECTION + 2 for p in picked)


def test_merge_joins_sources_by_arxiv_id_and_title():
    a = search.candidate("Attention Is All You Need", "abs", "2017-06-12", ["A Vaswani"], arxiv="1706.03762v7",
                         found_by="arxiv")
    b = search.candidate("Attention is all you need.", "", "2017", ["Ashish Vaswani"], "NeurIPS",
                         doi="10.48550/arXiv.1706.03762", citations=90000, found_by="semantic scholar")
    c = search.candidate("Attention is All you Need", "", "2017-12-04", [], "NeurIPS", doi="10.5555/x",
                         citations=100, found_by="openalex", openalex="W1")
    merged = search.merge([[a], [b], [c]])
    assert len(merged) == 1
    m = merged[0]
    assert m["id"] == "arXiv:1706.03762" and m["citations"] == 90000 and m["venue"] == "NeurIPS"
    assert m["found_by"] == ["arxiv", "semantic scholar", "openalex"] and m["openalex"] == "W1"


def test_merge_keeps_unknown_citations_unknown():
    a = search.candidate("Same title", arxiv="2401.00001", found_by="arxiv")
    b = search.candidate("Same title", arxiv="2401.00001", found_by="openalex")
    assert search.merge([[a], [b]])[0]["citations"] is None


def test_abstract_from_inverted_index():
    assert search.abstract_text({"we": [0], "attend": [1, 3], "and": [2]}) == "we attend and attend"


def test_screen_check_rejects_unknown_and_missing():
    ok = {"decisions": [{"id": "c1", "decision": "core", "line": "L1", "kind": "method", "reason": "r"}]}
    assert replies.check_screen(ok, ["c1"], ["L1"])["c1"]["line"] == "L1"
    with pytest.raises(ValueError):
        replies.check_screen({"decisions": [dict(ok["decisions"][0], id="c9")]}, ["c1"], ["L1"])
    with pytest.raises(ValueError):
        replies.check_screen({"decisions": [dict(ok["decisions"][0], line="none")]}, ["c1"], ["L1"])
    with pytest.raises(ValueError):
        replies.check_screen({"decisions": []}, ["c1"], ["L1"])


def note(pid, loc, builds=(), difference=""):
    return {"id": pid, "problem": "p", "mechanism": "m", "builds_on": list(builds), "difference": difference,
            "findings": [{"text": "f", "locator": f"{pid} {loc}"}], "limitations": [], "benchmarks": []}


def test_read_check_keeps_each_paper_in_its_own_sections():
    papers = {"p1": ["abstract", "§1 Intro"], "p2": ["abstract"]}
    out = replies.check_read({"notes": [note("p1", "§1 Intro"), note("p2", "abstract", ["p1"], "d")]}, papers, ["p1", "p2"])
    assert out["p1"]["findings"][0]["locator"] == "§1 Intro"
    with pytest.raises(ValueError):          # p2 points into p1's sections
        replies.check_read({"notes": [note("p1", "abstract"), note("p2", "abstract") | {
            "findings": [{"text": "f", "locator": "p1 §1 Intro"}]}]}, papers, ["p1", "p2"])
    with pytest.raises(ValueError):          # builds on earlier work without saying how it differs
        replies.check_read({"notes": [note("p1", "abstract"), note("p2", "abstract", ["p1"])]}, papers, ["p1", "p2"])


def test_chapter_check_needs_cites_from_the_line():
    good = {"title": "t", "paragraphs": ["a [p1].", "b [p1, p2]."], "table": {"caption": "", "columns": [], "rows": []}}
    assert replies.check_chapter(good, ["p1", "p2"])
    with pytest.raises(ValueError):
        replies.check_chapter(dict(good, paragraphs=["no cite here", "b [p1]."]), ["p1"])
    with pytest.raises(ValueError):
        replies.check_chapter(dict(good, paragraphs=["a [p7].", "b [p1]."]), ["p1"])
    bad_table = dict(good, table={"caption": "", "columns": ["score"], "rows": [{"paper": "p1", "cells": ["a", "b"]}]})
    with pytest.raises(ValueError):
        replies.check_chapter(bad_table, ["p1", "p2"])


def test_direction_needs_two_papers():
    card = {"title": "t", "problem": "x [p1]", "evidence": ["p1.1", "p1.2"], "active": [], "cost": "c", "first_step": "f"}
    with pytest.raises(ValueError):
        replies.check_directions({"cards": [card]}, ["p1.1", "p1.2", "p2.1"], ["p1", "p2"])
    assert replies.check_directions({"cards": [dict(card, evidence=["p1.1", "p2.1"])]}, ["p1.1", "p1.2", "p2.1"], ["p1", "p2"])


def test_prose_escapes_and_links():
    papers = {"p1": {"label": "Bahdanau et al. 2014"}, "p2": {"label": "Luong et al. 2015"}}
    out = render.prose("a <b> score [p1, p2] and [p9].", papers)
    assert "&lt;b&gt;" in out and 'data-p="p1"' in out and "Luong et al. 2015" in out
    assert "p9" not in out


def test_related_work_cites_and_bib():
    survey = {"lines": [{"id": "L1", "name": "Soft alignment"}],
              "papers": [{"pid": "p1", "label": "Bahdanau et al. 2014", "title": "Neural Machine Translation by Jointly Learning",
                          "authors": ["Dzmitry Bahdanau", "Kyunghyun Cho"], "year": 2014, "arxiv": "1409.0473", "venue": "arXiv",
                          "doi": ""}],
              "related_work": {"paragraphs": [{"line": "L1", "text": "Soft alignment 100% learns weights [p1]. We differ."}]}}
    tex, bib = render.related_work(survey)
    assert "\\cite{bahdanau2014neural}" in tex and "100\\%" in tex
    assert "@article{bahdanau2014neural" in bib and "eprint = {1409.0473}" in bib


def test_presets_fit_the_scope_of_a_survey():
    from auto_research.tasks.survey import presets
    assert list(presets.PRESETS) == ["llm_knowledge"]
    knowledge = presets.PRESETS["llm_knowledge"]
    assert [t.name for t in knowledge.tracks] == ["Memory architecture", "Memory lifecycle", "Modular experts",
                                                   "Knowledge adaptation"]
    assert knowledge.since == 2025
    for p in presets.PRESETS.values():
        lines = presets.lines(p)
        assert 3 <= len(lines) <= 8 and len({l["name"] for l in lines}) == len(lines)
        for l in lines:
            assert len(l["name"]) <= 40 and len(l["definition"]) <= 400 and len(l["boundary"]) <= 300
            assert 2 <= len(l["queries"]) <= 5 and all(0 < len(q) <= 120 for q in l["queries"])
    assert presets.get("") is None


def test_a_preset_scope_takes_no_lines_from_the_reply():
    from auto_research.tasks.survey import presets
    fixed = presets.lines(presets.PRESETS["llm_knowledge"])
    assert "lines" not in replies.scope_schema(with_lines=False)["properties"]
    scope = {"title": "t", "prerequisites": "p", "boundary": "b", "questions": ["q1", "q2"], "survey_queries": ["s"]}
    assert replies.check_scope(scope, fixed)["lines"] == fixed
    with pytest.raises(ValueError):
        replies.check_scope({**scope, "lines": []}, fixed)
    gaps = replies.gaps_schema(["S1: x"], ["L1"], new=False)
    assert "new" not in json.dumps(gaps)
