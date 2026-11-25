"""meta_analysis with the screeners, extractor and assessor scripted and every network
call replaced."""

import csv
import json
import subprocess
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.citations import sources
from auto_research.tasks.metaanalysis import stats
from auto_research.tasks.metaanalysis import workflow as wf
from auto_research.tasks.metaanalysis import works as wk

Q = "Does mixup improve CIFAR-10 test accuracy over ERM for ResNet-18?"
META = {
    "2001.00001": ("Mixup for image classification", "We show that mixup improves CIFAR-10 accuracy of ResNet-18 "
                   "over empirical risk minimization."),
    "2001.00002": ("Mixing inputs regularizes networks", "Input mixing raises test accuracy on CIFAR-10 and "
                   "CIFAR-100 with residual networks."),
    "2001.00003": ("Interpolation training revisited", "We study interpolation-based training and report error "
                   "rates on CIFAR-10."),
    "2001.00004": ("Calibration of mixed training", "Mixup improves calibration; we report accuracy and expected "
                   "calibration error on CIFAR-10."),
    "2001.00005": ("A theory of mixup", "We prove generalization bounds for mixup with linear models."),
    "2001.00006": ("Residual learning at depth", "We train very deep residual networks on ImageNet."),
}
TEXT = {
    "2001.00001": "Table 1: CIFAR-10 test accuracy of ResNet-18 (mean ± std over 5 seeds)\n"
                  "Method | Accuracy\nERM | 95.0 ± 0.1\nmixup | 96.0 ± 0.2\n"
                  "All hyperparameters were chosen on a held-out validation split of 5,000 images. "
                  "Code: https://github.com/example/mixup",
    "2001.00002": "Table 2: CIFAR-10 accuracy, single run.\nBaseline | 95.3\nInput mixing | 95.5\n"
                  "We report the best test accuracy over training epochs.",
    "2001.00003": "Table 3: CIFAR-10 test error of PreAct ResNet-18.\nERM | 0.056\nInterpolation | 0.042\n",
    "2001.00004": "Table 4: CIFAR-10 accuracy (standard error).\nERM 93.0 (0.4)\nmixup 94.1 (0.3)\n",
    "2001.00005": "Theorem 1. The mixup risk upper-bounds the empirical risk.",
}
IDS = [f"arXiv:{k}" for k in META]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(wf, "WORKERS", 1)          # scripted replies are answered in order
    monkeypatch.setattr(wk.search, "arxiv_ids", lambda ids: [
        {"arxiv": i, "title": META[i][0], "abstract": META[i][1], "year": 2020} for i in ids if i in META])
    monkeypatch.setattr(wk.reader, "work_text", lambda a: (TEXT.get(a, ""), f"https://arxiv.org/html/{a}"))

    def no_network(*a, **k):
        raise AssertionError(f"network call in a test: {a}")
    monkeypatch.setattr(sources, "fetch", no_network)
    monkeypatch.setattr(wk.deep, "fetch_bytes", lambda *a, **k: None)        # downloads fail
    sources.UNAVAILABLE.clear()


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "proj"
    (repo / "workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def quote_of(i):
    return META[i.split(":")[1]][1].split(" ", 5)[-1][:60].rsplit(" ", 1)[0]


def screen_reply(include, bad=()):
    out = []
    for i in IDS:
        q = quote_of(i) if i not in bad else "words that are not in the abstract"
        out.append({"work": i, "decision": "include" if i in include else "exclude", "reason": "Fits the question.",
                    "quote": q})
    return {"decisions": out}


def arm(label, value, quote, loc="Table 1"):
    return {"label": label, "value": value, "quote": quote, "location": loc}


def nothing():
    return {"value": "", "quote": ""}


def effect(m, c, unit="percent", kind="none", ms=None, cs=None, runs=None, hib=True, indirect=False):
    return {"found": True, "reason": "", "setting": "CIFAR-10, ResNet-18", "indirect": indirect,
            "indirect_why": "PreAct variant" if indirect else "", "metric": "accuracy" if hib else "error",
            "higher_is_better": hib, "unit": unit, "method": m, "comparator": c, "spread_kind": kind,
            "method_spread": ms or nothing(), "comparator_spread": cs or nothing(), "runs": runs or nothing()}


EXTRACT = [
    effect(arm("mixup", "96.0", "mixup | 96.0 ± 0.2"), arm("ERM", "95.0", "ERM | 95.0 ± 0.1"), kind="sd",
           ms={"value": "0.2", "quote": "mixup | 96.0 ± 0.2"}, cs={"value": "0.1", "quote": "ERM | 95.0 ± 0.1"},
           runs={"value": "5", "quote": "mean ± std over 5 seeds"}),
    effect(arm("Input mixing", "95.5", "Input mixing | 95.5"), arm("Baseline", "95.3", "Baseline | 95.3")),
    effect(arm("Interpolation", "0.042", "Interpolation | 0.042"), arm("ERM", "0.056", "ERM | 0.056"),
           unit="fraction", hib=False, indirect=True),
    effect(arm("mixup", "94.1", "mixup 94.1 (0.3)"), arm("ERM", "93.0", "ERM 93.0 (0.4)"), kind="se",
           ms={"value": "0.3", "quote": "mixup 94.1 (0.3)"}, cs={"value": "0.4", "quote": "ERM 93.0 (0.4)"}),
    effect(arm("mixup", "97.2", "mixup reaches 97.2"), arm("ERM", "95.0", "ERM | 95.0")),      # invented quote
]


def bias(seeds="unclear", tuning="unclear", q_tuning=""):
    return {"items": [{"item": "seeds", "judgement": seeds, "quote": "", "note": ""},
                      {"item": "test_tuning", "judgement": tuning, "quote": q_tuning, "note": ""},
                      {"item": "contamination", "judgement": "low", "quote": "CIFAR-10", "note": "from scratch"},
                      {"item": "baseline_tuned", "judgement": "low", "quote": "a line nobody wrote", "note": ""}]}


BIAS = [bias(tuning="low", q_tuning="All hyperparameters were chosen on a held-out validation split"),
        bias(seeds="high", tuning="high", q_tuning="We report the best test accuracy over training epochs"),
        bias(), bias()]


def run(paper, script, **kw):
    with script_roles(script) as calls:
        out = json.loads(wf.meta_analysis(str(paper), Q, works=",".join(IDS), **kw))
    return out, calls


def full_script():
    a = screen_reply(IDS[:5])
    b = screen_reply(IDS[:3] + [IDS[4]], bad=[IDS[0]])
    b_again = {"decisions": [{"work": IDS[0], "decision": "include", "reason": "Mixup vs ERM.",
                              "quote": "mixup improves CIFAR-10 accuracy"}]}
    return {wf.screener_a: [a], wf.screener_b: [b, b_again], wf.extractor: list(EXTRACT), wf.assessor: list(BIAS)}


def test_full_run(paper):
    out, calls = run(paper, full_script())
    assert out["candidates"] == 6 and out["included"] == 5 and out["studies"] == 4
    # Screeners are blind: neither sees the other's decisions; B was asked again for the bad quote.
    a_req, b_req = calls[wf.screener_a][0].text, calls[wf.screener_b][0].text
    assert "strictly against" in a_req and "methodologist" not in a_req and "methodologist" in b_req
    assert "Fits the question" not in b_req and len(calls[wf.screener_b]) == 2
    assert IDS[0] in calls[wf.screener_b][1].text and IDS[1] not in calls[wf.screener_b][1].text
    folder = Path(out["report"]).parent
    assert folder.parent == paper / "drafts" / "meta_analysis"
    record = json.loads((folder / "analysis.json").read_text())
    sc = record["screening"]
    assert sc["disagreements"] == [IDS[3]] and sc["final"][IDS[5]] == "exclude"
    xs = [True, True, True, True, True, False]
    ys = [True, True, True, False, True, False]
    from auto_research.tasks.qualitative.agreement import kappa
    assert out["kappa"] == round(kappa(xs, ys), 3)
    studies = {s["id"]: s for s in record["studies"]}
    assert set(studies) == set(IDS[:4])
    assert [e["id"] for e in record["excluded"]] == [IDS[4]] and "找不到" in record["excluded"][0]["why"]
    s1, s2, s3, s4 = (studies[i] for i in IDS[:4])
    assert s1["y"] == pytest.approx(1.0) and s1["v"] == pytest.approx(0.01) and s1["n"] == 5
    assert s3["y"] == pytest.approx(1.4) and s3["scale"] == 100 and s3["variance"] == "imputed"
    assert s4["v"] == pytest.approx(0.25) and s2["variance"] == "imputed"
    # Risk of bias: quotes checked, seeds from the extraction, code release in code.
    r1, r2, r4 = s1["risk"]["items"], s2["risk"], s4["risk"]["items"]
    assert r1["seeds"]["judgement"] == "low" and "5 seeds" in r1["seeds"]["quote"]
    assert r1["code_released"]["judgement"] == "low" and "github.com/example" in r1["code_released"]["quote"]
    assert r1["baseline_tuned"]["judgement"] == "unclear"              # its quote is not in the paper
    assert r2["overall"] == "high" and s2["high_risk"] and r4["code_released"]["judgement"] == "high"
    # Pooling equals the stats module on the same effects.
    rows = [{"y": s["y"], "v": s["v"]} for s in record["studies"]]
    expect = stats.pool(rows)
    assert out["effect"] == pytest.approx(expect["mu"]) and out["i2"] == pytest.approx(expect["i2"])
    g = record["grade"]
    assert g["downgrades"]["indirectness"] == 0 and g["downgrades"]["publication_bias"] == 0
    assert out["status"] == ("PASS" if g["certainty"] in ("High", "Moderate") else "PASS_WITH_NOTES")
    report = (folder / "report.md").read_text()
    assert "## 结果摘要（Summary of findings）" in report and "分歧（请作者裁定）" in report
    assert "「mixup | 96.0 ± 0.2」" in report and "未做 Egger 检验" in report
    forest = (folder / "forest.html").read_text()
    assert forest.count('class="box"') + forest.count('class="imputed"') == 4 and "diamond" in forest
    assert "prefers-color-scheme: dark" in forest and "<script" not in forest
    rows = list(csv.DictReader((folder / "studies.csv").open()))
    assert [r["id"] for r in rows] == IDS[:4] and rows[0]["method_quote"] == "mixup | 96.0 ± 0.2"
    screen = list(csv.DictReader((folder / "screening.csv").open()))
    assert len(screen) == 12 and {r["final"] for r in screen if r["id"] == IDS[3]} == {"include"}
    run_json = json.loads(Path(out["run"]).read_text())
    assert set(run_json["roles"]) == {"screener_a", "screener_b", "screener_c", "extractor", "assessor"}


def test_nothing_extractable_fails(paper):
    not_found = {k: v for k, v in effect(arm("", "", ""), arm("", "", "")).items()}
    not_found.update(found=False, reason="Only a theorem.")
    script = {wf.screener_a: [screen_reply([IDS[4]])], wf.screener_b: [screen_reply([IDS[4]])],
              wf.extractor: [not_found]}
    out, _ = run(paper, script)
    assert out["status"] == "FAIL" and out["studies"] == 0 and out["kappa"] == 1
    report = Path(out["report"]).read_text()
    assert "无法合并" in report and "Only a theorem." in report and not out["forest"]


def test_three_screeners_on_named_models(paper):
    script = {wf.screener_a: [screen_reply([])], wf.screener_b: [screen_reply([])],
              wf.screener_c: [screen_reply([IDS[5]])], wf.extractor: [effect(arm("", "", ""), arm("", "", ""))
                                                                       | {"found": False}]}
    out, calls = run(paper, script, screeners="m-one, m-two, m-three")
    assert out["included"] == 1 and len(calls[wf.screener_c]) == 1 and "sceptic" in calls[wf.screener_c][0].text
    record = json.loads((Path(out["report"]).parent / "analysis.json").read_text())
    assert record["models"] == ["m-one", "m-two", "m-three"] and len(record["screening"]["pairs"]) == 3


def test_inputs(paper, tmp_path):
    with pytest.raises(ValueError, match="question is empty"):
        wf.meta_analysis(str(paper), "  ")
    with pytest.raises(ValueError, match="screeners"):
        wf.meta_analysis(str(paper), Q, works=IDS[0], screeners="5")
    with pytest.raises(ValueError, match="not a Git repository"):
        wf.meta_analysis(str(tmp_path), Q)
    with pytest.raises(ValueError, match="no candidate works"):
        wf.meta_analysis(str(paper), Q)
    (paper / "refs.bib").write_text("@article{a, title={Mixup for image classification}, eprint={2001.00001}}\n"
                                    "@article{b, title={Same paper}, doi={10.48550/arXiv.2001.00001}}\n"
                                    "@article{c, title={A theory of mixup}, eprint={2001.00005}}\n")
    found, origin = wk.candidates(paper, "")
    assert origin == "project" and [c["id"] for c in found] == ["arXiv:2001.00001", "arXiv:2001.00005"]
    found, origin = wk.candidates(paper, "refs.bib")
    assert origin == "refs.bib" and found[0]["key"] == "a"
    with pytest.raises(ValueError, match="not an arXiv id"):
        wk.candidates(paper, "mixup paper")


def test_extraction_checks():
    text = "Table 1\nERM | 95.0 ± 0.1\nmixup | 96.0 ± 0.2\n10 runs"
    s = wf.check_effect(effect(arm("m", "96.0", "mixup | 96.0 ± 0.2"), arm("e", "95.0", "ERM | 95.0 ± 0.1"),
                               kind="sd", ms={"value": "0.2", "quote": "mixup | 96.0 ± 0.2"},
                               cs={"value": "0.3", "quote": "ERM | 95.0 ± 0.3"},
                               runs={"value": "10", "quote": "10 runs"}), text)
    assert s["method_spread"] == 0.2 and s["comparator_spread"] is None and s["spread_dropped"] == ["comparator"]
    assert s["n"] == 10 and s["decimals"] == 1
    bad = wf.check_effect(effect(arm("m", "96", "mixup | 96.0 ± 0.2"), arm("e", "95.0", "ERM | 95.0 ± 0.1")), text)
    assert "excluded" in bad                       # "96" is not a number of its own in "96.0"
    assert wf.number_in("4.2", "error 4.2%") and not wf.number_in("4.2", "error 14.25")



def test_failed_assessor_keeps_the_study(paper):
    script = {wf.screener_a: [screen_reply([IDS[0]])], wf.screener_b: [screen_reply([IDS[0]])],
              wf.extractor: [{"found": "maybe"}, EXTRACT[0]],
              wf.assessor: [ValueError("Structured output is not valid JSON")]}
    out, calls = run(paper, script)
    assert out["studies"] == 1 and out["status"] == "PASS_WITH_NOTES"
    assert len(calls[wf.extractor]) == 2 and len(calls[wf.assessor]) >= 2       # each asked once more
    record = json.loads((Path(out["report"]).parent / "analysis.json").read_text())
    items = record["studies"][0]["risk"]["items"]
    assert items["test_tuning"]["judgement"] == "unclear" and items["seeds"]["judgement"] == "low"
    assert any("risk-of-bias assessment could not run" in n for n in record["notes"])


def test_the_same_numbers_count_once():
    a = {"id": "x", "method_arm": {"raw": "96.62"}, "comparator_arm": {"raw": "95.50"}}
    b = {"id": "y", "method_arm": {"raw": "96.62"}, "comparator_arm": {"raw": "95.50"}}
    c = {"id": "z", "method_arm": {"raw": "96.62"}, "comparator_arm": {"raw": "95.10"}}
    kept, twins = wf.distinct([a, b, c])
    assert [s["id"] for s in kept] == ["x", "z"] and twins == [(b, a)]
