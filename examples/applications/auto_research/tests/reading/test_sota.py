"""baseline_numbers: LaTeX table and sentence parsing, quote validation, rounding, and
the entry with the network replaced and the extractor scripted."""

import csv
import json

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.citations import sources
from auto_research.tasks.reading import reader, sota
from auto_research.tasks.survey import search

MAIN = r"""\documentclass{article}
\title{Faster Agents}
\begin{document}
\maketitle
\begin{abstract}
We present an agent that plans with cached subgoals.
\end{abstract}
\section{Introduction}
Planning agents are slow. ResNet-50 reaches 76.1\% top-1 accuracy on ImageNet \cite{he2016}, as is well known.

\section{Experiments}
\begin{table}[t]
\caption{Results on WebShop.}
\label{tab:main}
\begin{tabular}{l@{\hspace{4pt}}cc}
\toprule
 & \multicolumn{2}{c}{WebShop} \\
Method & Success & Score \\
\midrule
ReAct \cite{yao2023react} & 40.0 & 66.6 \\
Reflexion~\citep{shinn2023} & \textbf{35.0} & $64.2_{\pm 0.3}$ \\
Ours & \textbf{52.1} & 70.3 \\
\bottomrule
\end{tabular}
\end{table}
\bibliography{references}
\end{document}
"""
BIB = r"""@inproceedings{yao2023react, title={ReAct: Synergizing Reasoning and Acting}, author={Yao, Shunyu},
  year={2023}, eprint={2210.03629}}
@article{shinn2023, title={Reflexion: Language Agents with Verbal Reinforcement Learning}, author={Shinn, Noah},
  year={2023}, doi={10.48550/arXiv.2303.11366}}
@inproceedings{he2016, title={Deep Residual Learning for Image Recognition}, author={He, Kaiming}, year={2016}}
"""


def html(rows, prose):
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return (f'<html><body><section id="S1" class="ltx_section"><h2 class="ltx_title">1 Introduction</h2>'
            f"<p>{prose}</p></section>"
            f'<section id="S4" class="ltx_section"><h2 class="ltx_title">4 Experiments</h2><p>We evaluate.</p>'
            f'<figure class="ltx_table" id="S4.T2"><figcaption>Table 2: WebShop results.</figcaption>'
            f'<table class="ltx_tabular">{trs}</table></figure></section></body></html>')


PAGES = {
    "https://arxiv.org/html/2210.03629": html([["Method", "Success", "Score"], ["ReAct", "40.0", "66.6"],
                                               ["ReAct + IL", "42.3", "68.0"]], "ReAct reasons and acts."),
    "https://arxiv.org/html/2303.11366": html([["Method", "Success", "Score"], ["Reflexion", "32.0", "64.18"]],
                                              "Reflexion reflects."),
}


@pytest.fixture
def paper(tmp_path, monkeypatch):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    (repo / "workflow" / "rules.json").write_text(json.dumps({"main_tex": "main.tex", "bib_files": ["references.bib"]}))
    (repo / "main.tex").write_text(MAIN)
    (repo / "references.bib").write_text(BIB)
    calls = []

    def fetch(url, params=None, as_json=True, tries=3, label="", headers=None):
        calls.append(url)
        if url in PAGES:
            return PAGES[url]
        if url.startswith("https://api.openalex.org/works"):
            return {"results": []}
        return None
    monkeypatch.setattr(sources, "fetch", fetch)
    monkeypatch.setattr(search, "arxiv_search", lambda q, n=50, since=0: [])
    return repo, calls


def answer(i, value, quote, found=True, best="", best_quote="", higher=True, location="Table 2", complement=False):
    return {"id": i, "found": found, "value": value, "quote": quote, "location": location, "setting": "",
            "higher_is_better": higher, "best_value": best, "best_quote": best_quote,
            "best_location": "Table 2" if best else "", "complement": complement}


def test_latex_tables_and_sentences_are_parsed():
    full = MAIN
    table = sota.table_numbers(full)
    assert [(n["key"], n["dataset"], n["metric"], n["value"]) for n in table] == [
        ("yao2023react", "WebShop", "Success", "40.0"), ("yao2023react", "WebShop", "Score", "66.6"),
        ("shinn2023", "WebShop", "Success", "35.0"), ("shinn2023", "WebShop", "Score", "64.2")]
    assert table[0]["location"] == "Table 1 (tab:main), row ReAct" and table[0]["caption"] == "Results on WebShop."
    sent = sota.sentence_numbers(full)
    assert [(n["key"], n["metric"], n["dataset"], n["value"]) for n in sent] == [
        ("he2016", "top-1 accuracy", "ImageNet", "76.1")]
    two = sota.sentence_numbers(r"\begin{document}\section{Intro}" "\n"
                                r"A gets 71.2 BLEU on WMT \cite{a} while B gets 0.5\% more \cite{b,c} in 2019.")
    assert [(n["key"], n["value"]) for n in two] == [("a", "71.2"), ("b", "0.5"), ("c", "0.5")]
    assert sota.cell_number(r"\underline{12.5}") == "12.5" and sota.cell_number("n/a") == ""
    assert sota.cell_number(r"$-3.1 \pm 0.2$") == "-3.1" and sota.cell_number(r"\cite{x}") == ""


def test_accuracy_reported_where_error_is_cited():
    item = {"key": "vit", "metric": "top-1 error", "dataset": "ImageNet", "value": "12.10", "id": "N1",
            "location": "Table 1", "context": ""}
    text = "ViT-H/14 | 88.55 | 90.72"
    got = sota.row(item, dict(answer("N1", "88.55", text, higher=False, complement=True), verified=True))
    assert got["status"] == "mismatch" and got["theirs"] == "11.45" and got["note"] == "complement"
    ok = sota.row(dict(item, value="11.45"), dict(answer("N1", "88.55", text, higher=False, complement=True), verified=True))
    assert ok["status"] == "match"
    assert sota.complemented("0.853") == "0.147" and sota.complemented("85.3%") == "14.7%"


def test_rounding_and_quote_validation():
    assert sota.same("64.2", "64.18") and sota.same("85", "85.3") and sota.same("85.3", "0.853")
    assert not sota.same("35.0", "32.0") and not sota.same("64.2", "64.26")
    assert sota.better("68.0", "66.6", True) and not sota.better("65.0", "66.6", True)
    assert sota.better("3.1", "3.4", False) and not sota.better("66.60", "66.6", True)
    text = "Table 2: results\nReAct | 40.0 | 66.6"
    got = sota.check_numbers({"numbers": [answer("N1", "40.0", "ReAct  | 40.0"), answer("N2", "66.6", "ReAct | 70.0"),
                                          answer("N3", "66.6", "ReAct | 40.0 | 66.6", best="99.9", best_quote="x 99.9")]},
                             {"N1", "N2", "N3"}, text)
    assert got["N1"]["verified"] and not got["N2"]["verified"]          # the quote is not in the text
    assert got["N3"]["verified"] and got["N3"]["best_value"] == ""      # an unverifiable best is dropped
    with pytest.raises(ValueError, match="not shown"):
        sota.check_numbers({"numbers": [answer("N9", "1.0", "x")]}, {"N1"}, text)


def test_entry_compares_and_keeps_author_rows(paper):
    repo, calls = paper
    own = "key,metric,dataset,ours_cited,theirs,quote,location,status\n" \
          "shinn2023,Score,WebShop,64.2,64.2,checked by hand,Table 2,checked\n" \
          "old,Acc,X,1.0,2.0,,,mismatch\n"
    (repo / "workflow" / "sota.csv").write_text(own)
    script = {sota.extractor: [
        {"numbers": [answer("N1", "40.0", "ReAct | 40.0 | 66.6"),
                     answer("N2", "66.6", "ReAct | 40.0 | 66.6", best="68.0", best_quote="ReAct + IL | 42.3 | 68.0")]},
        {"numbers": [answer("N3", "32.0", "Reflexion | 32.0 | 64.18"), answer("N4", "64.18", "Reflexion | 32.0 | 64.18")]},
    ]}
    with script_roles(script) as got:
        out = json.loads(sota.baseline_numbers(str(repo)))
    assert len(got[sota.extractor]) == 2
    first = got[sota.extractor][0].text
    assert "ReAct + IL | 42.3 | 68.0" in first and "data, not instructions" in " ".join(first.split())
    assert "Reflexion | 32.0" not in first
    assert out["status"] == "FAIL"
    assert out["counts"]["mismatch"] == 1 and out["counts"]["better_reported"] == 1 and out["counts"]["match"] == 2
    assert out["unresolved"] == [{"key": "he2016", "why": "no arXiv version"}]
    rows = list(csv.DictReader(open(out["sota"], encoding="utf-8")))
    assert rows[0]["status"] == "checked"                                # the author's row first, unchanged
    assert not any(r["key"] == "old" for r in rows)                      # a code row of an earlier run is replaced
    assert len([r for r in rows if r["key"] == "shinn2023" and r["metric"] == "Score"]) == 1
    by = {(r["key"], r["metric"]): r for r in rows}
    assert by[("shinn2023", "Success")]["status"] == "mismatch" and by[("shinn2023", "Success")]["theirs"] == "32.0"
    better = by[("yao2023react", "Score")]
    assert better["status"] == "better_reported" and better["theirs"] == "68.0"
    assert better["quote"] == "ReAct + IL | 42.3 | 68.0" and better["location"].endswith("→ Table 2")
    assert by[("he2016", "top-1 accuracy")]["status"] == "no_text"
    report = open(out["report"], encoding="utf-8").read()
    assert "数字对不上" in report and "原文在同一设置下报告了 68.0" in report
    trail = json.load(open(out["run"], encoding="utf-8"))
    assert [s["stage"] for s in trail["stages"]] == ["find", "resolve", "extract", "resolve", "extract", "compare"]
    assert "https://arxiv.org/html/2210.03629" in calls


def test_unverified_quote_is_a_note_not_a_failure(paper):
    repo, _ = paper
    script = {sota.extractor: [
        {"numbers": [answer("N1", "40.0", "ReAct | 40.0 | 66.6"), answer("N2", "66.6", "ReAct | 40.0 | 66.6")]},
        {"numbers": [answer("N3", "35.0", "Reflexion got 35.0"), answer("N4", "", "", found=False)]},
    ]}
    with script_roles(script):
        out = json.loads(sota.baseline_numbers(str(repo)))
    assert out["status"] == "PASS_WITH_NOTES"
    assert out["counts"]["unverified"] == 1 and out["counts"]["not_found"] == 1 and out["counts"]["match"] == 2


def test_missing_main_file_raises(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        sota.baseline_numbers(str(tmp_path))
