"""missing_baselines: benchmark parsing, candidate ranking, quote checks, the
direction-aware comparison, and the entry with every network call replaced and the
extractor scripted."""

import csv
import json
from datetime import date

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.citations import sources
from auto_research.tasks.lineage import benchmarks, candidates, workflow
from auto_research.tasks.reading import reader
from auto_research.tasks.survey import search

MAIN = r"""\documentclass{article}
\title{Sharper Probes}
\begin{document}
\maketitle
\section{Results}
\begin{table}[t]
\centering
\begin{tabular}{lcc}
\toprule
Method & CIFAR-100 accuracy & ImageNet top-1 error \\
\midrule
ResNet-50 \cite{he2016} & 80.0 & 23.9 \\
WideNet \cite{zag2016} & 81.2 & -- \\
Default schedule & 82.0 & 22.0 \\
Ours & \textbf{85.0} & 21.5 \\
\bottomrule
\end{tabular}
\caption{Linear probes on frozen ResNet-50 features.}
\label{tab:main}
\end{table}
\bibliography{references}
\end{document}
"""
BIB = r"""@inproceedings{he2016, title={Deep Residual Learning for Image Recognition}, author={He, Kaiming},
  year={2016}, eprint={1512.03385}}
@article{zag2016, title={Wide Residual Networks}, author={Zagoruyko, Sergey}, year={2016}, eprint={1605.07146}}
@article{cited2024, title={Probing Frozen Features Done Right}, author={Lee, Ann}, year={2024}, eprint={2402.00003}}
"""


def oa(wid, title, arxiv, cites, day="2024-03-01"):
    return {"id": f"https://openalex.org/{wid}", "doi": f"https://doi.org/10.48550/arxiv.{arxiv}", "title": title,
            "publication_date": day, "cited_by_count": cites, "type": "article",
            "locations": [{"landing_page_url": f"https://arxiv.org/abs/{arxiv}"}],
            "authorships": [{"author": {"display_name": "Kim Park"}}], "abstract_inverted_index": None,
            "primary_location": {"source": {"display_name": "arXiv"}}}


STRONG = oa("W10", "Better Probes for Everyone", "2401.00001", 300)
EXTRA = oa("W11", "Probing with a Billion Images", "2405.00002", 120)
CITED = oa("W12", "Probing Frozen Features Done Right", "2402.00003", 500)
WEAK = oa("W13", "A Small Probe", "2403.00004", 5)
NOTEXT = oa("W14", "Unrelated Speech Recognition", "2404.00005", 50)


def page(table_rows, prose, cap="Table 2: CIFAR-100 linear probe accuracy."):
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in table_rows)
    return (f'<html><body><section id="S1" class="ltx_section"><h2 class="ltx_title">1 Introduction</h2><p>{prose}</p>'
            f'</section><section id="S4" class="ltx_section"><h2 class="ltx_title">4 Experiments</h2><p>We evaluate.</p>'
            f'<figure class="ltx_table"><figcaption>{cap}</figcaption>'
            f'<table class="ltx_tabular">{trs}</table></figure></section></body></html>')


PAGES = {
    "https://arxiv.org/html/2401.00001": page([["Method", "CIFAR-100"], ["ResNet-50 (frozen, linear probe)", "80.0"],
                                               ["BetterProbe (frozen ResNet-50, linear probe)", "87.1"]],
                                              "We train linear probes on frozen ResNet-50 features."),
    "https://arxiv.org/html/2405.00002": page([["Method", "CIFAR-100"], ["BillionProbe (ViT-g, 1B extra images)", "90.3"]],
                                              "We pre-train on a billion extra images."),
    "https://arxiv.org/html/2403.00004": page([["Method", "CIFAR-100"], ["SmallProbe", "79.0"]], "A small probe."),
    "https://arxiv.org/html/2404.00005": page([["Method", "WER"], ["Ours", "3.1"]], "Speech.", "Table 1: WER on LibriSpeech."),
}


@pytest.fixture
def paper(tmp_path, monkeypatch):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    (repo / "workflow" / "rules.json").write_text(json.dumps({"main_tex": "main.tex", "bib_files": ["references.bib"]}))
    (repo / "main.tex").write_text(MAIN)
    (repo / "references.bib").write_text(BIB)
    (repo / "workflow" / "sota.csv").write_text(
        "key,metric,dataset,ours_cited,theirs,quote,location,status\n"
        "zag2016,CIFAR-100 accuracy,,81.2,81.15,WRN-28-10 | 81.15,Table 1,match\n")
    monkeypatch.setenv("ZOTERO_STORAGE", str(tmp_path / "no-zotero"))
    calls = []

    def fetch(url, params=None, as_json=True, tries=3, label="", headers=None):
        params = params or {}
        calls.append((url, dict(params)))
        if url in PAGES:
            return PAGES[url]
        if url.startswith("https://api.openalex.org/works/doi:10.48550/arxiv."):
            arxiv = url.rsplit("arxiv.", 1)[1]
            return {"id": {"1512.03385": "https://openalex.org/W1", "1605.07146": "https://openalex.org/W2"}.get(
                arxiv, "https://openalex.org/W9"), "title": "x"}
        if url == "https://api.openalex.org/works":
            f = params.get("filter", "")
            if f.startswith("title.search:CIFAR-100"):
                return {"results": [oa("W5", "Learning Multiple Layers of Features from Tiny Images", "0000.00000", 9),
                                    oa("W6", "CIFAR-100 a dataset of tiny images", "0000.00001", 8)]}
            if f.startswith("title.search:ImageNet"):
                return {"results": [oa("W8", "ImageNet classification with deep convolutional networks", "0000.00003", 99),
                                    oa("W7", "ImageNet: A large-scale hierarchical image database", "0000.00002", 9)]}
            if "cites:W6" in f:
                assert "from_publication_date:2023-01-01" in f
                return {"results": [STRONG, EXTRA, CITED, WEAK]}
            if "cites:" in f:
                return {"results": [NOTEXT]}
            return {"results": []}
        if "semanticscholar" in url:
            return {"data": []}
        return None
    monkeypatch.setattr(sources, "fetch", fetch)
    monkeypatch.setattr(search, "fetch", fetch)
    monkeypatch.setattr(workflow, "WORKERS", 1)
    return repo, calls


def result(b, value, quote, setting="", setting_quote="", comparable=True, found=True, complement=False):
    return {"benchmark": b, "found": found, "value": value, "quote": quote, "location": "Table 2", "method": "m",
            "setting": setting, "setting_quote": setting_quote, "comparable": comparable,
            "difference": "" if comparable else "extra data", "complement": complement}


def test_headers_directions_and_benchmarks(paper):
    repo, _ = paper
    assert benchmarks.split_header("ImageNet top-1 error") == ("ImageNet", "top-1 error")
    assert benchmarks.split_header("GLUE score") == ("GLUE", "score")
    assert benchmarks.split_header("Top-1 Acc") == ("", "Top-1 Acc")
    assert not benchmarks.direction("top-1 error") and benchmarks.direction("accuracy")
    assert benchmarks.direction("error", r"error $\uparrow$")
    assert not benchmarks.direction("val_loss score", "", {"Metric": "val_loss", "Direction": "lower"})
    _, _, full = reader.open_paper(str(repo))
    got = benchmarks.benchmarks(repo, full)
    cifar, imnet = got
    assert (cifar["dataset"], cifar["metric"], cifar["higher"]) == ("CIFAR-100", "accuracy", True)
    assert cifar["ours"]["value"] == "85.0" and cifar["keys"] == ["he2016", "zag2016"]
    assert cifar["strongest"]["value"] == "81.15"              # the value sota.csv verified wins over the cell
    assert (imnet["dataset"], imnet["higher"], imnet["ours"]["value"], imnet["strongest"]["value"]) == \
        ("ImageNet", False, "21.5", "23.9")


def test_since_count_and_ranking():
    assert workflow.since_date("2023") == date(2023, 1, 1)
    assert workflow.since_date("2023-11-02") == date(2023, 11, 2)
    assert workflow.since_date("").year == date.today().year - 3
    with pytest.raises(ValueError, match="since"):
        workflow.since_date("last year")
    today = date(2026, 1, 1)
    recs = [search.candidate("Old Famous", date="2016-01-01", citations=1000, arxiv="1601.00001"),
            search.candidate("New Hot", date="2025-07-01", citations=300, arxiv="2507.00001"),
            search.candidate("Probing Frozen Features Done Right.", date="2024-01-01", citations=900),
            search.candidate("Linear Probing: A Survey", date="2025-01-01", citations=900)]
    refs = [{"key": "x", "doi": "", "arxiv": "", "title": "Probing frozen features done right"}]
    kept, dropped = candidates.rank(recs, refs, 5, today)
    assert [r["title"] for r in kept] == ["New Hot", "Old Famous"] and dropped == 1


def test_quotes_and_comparison():
    text = "Table 2\nBetterProbe | 87.1\nWe use frozen ResNet-50 features."
    got = workflow.check({"results": [result("B1", "87.1", "BetterProbe | 87.1", "frozen ResNet-50",
                                             "frozen ResNet-50 features"),
                                      result("B2", "87.1", "BetterProbe | 88.0")]}, {"B1", "B2"}, text)
    assert got["B1"]["verified"] and got["B1"]["comparable"] and not got["B2"]["verified"]
    unsure = workflow.check({"results": [result("B1", "87.1", "BetterProbe | 87.1", "x", "not in the text")]},
                            {"B1"}, text)
    assert unsure["B1"]["verified"] and not unsure["B1"]["comparable"]     # an unquoted setting is not comparable
    with pytest.raises(ValueError, match="not shown"):
        workflow.check({"results": [result("B9", "1", "x")]}, {"B1"}, text)
    bench = {"id": "B1", "dataset": "ImageNet", "metric": "top-1 error", "higher": False,
             "ours": {"value": "11.6", "name": "Ours"}, "strongest": {"value": "11.45", "name": "ViT-H/14"}}
    work = {"title": "W", "arxiv": "2401.1", "id": "arXiv:2401.1", "year": 2024}
    acc = dict(result("B1", "89.5", "q 89.5"), verified=True, setting_verified=True, complement=True)
    row = workflow.compare(bench, work, acc)
    assert row["value"] == "10.5" and row["status"] == "problem" and row["stronger_than_ours"] == "yes"
    same = workflow.compare(bench, work, dict(acc, value="88.45"))       # 11.55 rounds to our 11.6
    assert same["stronger_than_ours"] == "no" and same["stronger_than_baselines"] == "no" and same["status"] == "weaker"
    other = workflow.compare(bench, work, dict(acc, comparable=False))
    assert other["status"] == "note" and other["comparable"] == "no"
    no_ours = workflow.compare(dict(bench, ours=None), work, acc)
    assert no_ours["status"] == "note" and no_ours["stronger_than_ours"] == "-"


def test_entry_finds_a_stronger_uncited_work_and_keeps_author_status(paper):
    repo, calls = paper
    script = {workflow.extractor: [
        {"results": [result("B1", "87.1", "BetterProbe (frozen ResNet-50, linear probe) | 87.1",
                            "frozen ResNet-50 linear probe", "We train linear probes on frozen ResNet-50 features.")]},
        {"results": [result("B1", "90.3", "BillionProbe (ViT-g, 1B extra images) | 90.3", "ViT-g with 1B extra images",
                            "We pre-train on a billion extra images.", comparable=False)]},
        {"results": [result("B1", "79.0", "SmallProbe | 79.0", "small", "A small probe.")]},
    ]}
    with script_roles(script) as got:
        out = json.loads(workflow.missing_baselines(str(repo), since="2023", count="2"))
    assert len(got[workflow.extractor]) == 3
    first = got[workflow.extractor][0].text
    assert "BetterProbe" in first and "data, not instructions" in " ".join(first.split())
    assert '"dataset": "CIFAR-100"' in first and '"dataset": "ImageNet"' not in first
    assert out["status"] == "FAIL" and out["counts"] == {"problem": 1, "note": 1, "weaker": 1}
    rows = list(csv.DictReader(open(out["csv"], encoding="utf-8")))
    by = {r["id"]: r for r in rows}
    assert "arXiv:2402.00003" not in by                                  # in the .bib: never read
    assert by["arXiv:2401.00001"]["status"] == "problem" and by["arXiv:2401.00001"]["comparable"] == "yes"
    assert by["arXiv:2405.00002"]["status"] == "note" and by["arXiv:2405.00002"]["stronger_than_ours"] == "yes"
    assert by["arXiv:2403.00004"]["status"] == "weaker"
    bib = open(out["bib"], encoding="utf-8").read()
    assert "2401.00001" in bib and "2405.00002" in bib and "2403.00004" not in bib
    report = open(out["report"], encoding="utf-8").read()
    assert "设置可比，比我们的数字强" in report and "比我们强但设置不同" in report
    assert any(u == "https://arxiv.org/html/2404.00005" for u, _ in calls)     # read, but never names a dataset
    record = json.load(open(out["json"], encoding="utf-8"))
    assert [b["dataset_paper"]["id"].rsplit("/", 1)[1] for b in record["benchmarks"]] == ["W6", "W7"]
    stages = [s["stage"] for s in json.load(open(out["run"], encoding="utf-8"))["stages"]]
    assert stages[0] == "benchmarks" and stages.count("extract") == 3 and stages[-1] == "compare"

    text = open(out["csv"], encoding="utf-8").read().replace(",problem\n", ",cited\n")
    open(out["csv"], "w", encoding="utf-8").write(text)
    with script_roles(script):
        again = json.loads(workflow.missing_baselines(str(repo), since="2023", count="2"))
    assert again["status"] == "PASS_WITH_NOTES"
    rows = list(csv.DictReader(open(again["csv"], encoding="utf-8")))
    assert [r["status"] for r in rows if r["id"] == "arXiv:2401.00001"] == ["cited"]


def test_no_benchmark_raises(tmp_path):
    repo = tmp_path / "p"
    (repo / "workflow").mkdir(parents=True)
    (repo / "workflow" / "rules.json").write_text(json.dumps({"main_tex": "main.tex", "bib_files": []}))
    (repo / "main.tex").write_text("\\begin{document}\\section{A} No tables.\\end{document}")
    with pytest.raises(ValueError, match="no results table"):
        workflow.missing_baselines(str(repo))
