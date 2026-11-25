"""method_lint on the fixture repositories with a scripted reviewer."""

import csv
import json

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.methods import lint as ml
from auto_research.tasks.methods import method_lint
from auto_research.tasks.methods.rules import lint

from .repo import CLEAN, DATA, LEAKY, make

NOTHING = {"verdicts": [], "findings": []}


def ids():
    """The reviewer's finding ids for train.py, by rule."""
    found = [f for f in lint({"train.py": LEAKY, "data.py": DATA})[0] if f["file"] == "train.py"]
    return {f["rule"]: f"F{i}" for i, f in enumerate(found, 1)}


def review():
    i = ids()
    return {"verdicts": [
        {"id": i["preprocess-before-split"], "verdict": "confirm", "quote": "X = scaler.fit_transform(X)",
         "reason": "在切分前拟合"},
        {"id": i["duplicates-unchecked"], "verdict": "dismiss", "quote": "df = load()", "reason": "数据已去重"},
        {"id": i["target-leakage"], "verdict": "dismiss", "quote": "X = df.drop(columns=['label'])",
         "reason": "引文不在文件里，不算数"}],
        "findings": [
        {"rule": "Refit On All", "severity": "medium", "finding": "最终模型在全部数据上重训，报告的数字不是留出集上的",
         "fix": "最终评估用测试集", "quote": "model.fit(X,   y)"},
        {"rule": "made-up", "severity": "high", "finding": "虚构的问题", "fix": "无", "quote": "torch.load('x')"}]}


def rows(paper):
    with open(paper / "workflow" / "method_lint.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_leaks_are_found_reviewed_and_kept_in_the_csv(tmp_path):
    paper = make(tmp_path)
    with script_roles({ml.reviewer: [review(), NOTHING]}) as calls:
        out = json.loads(method_lint(str(paper)))
    assert out["files"] == ["train.py", "data.py"]
    first = calls[ml.reviewer][0]
    assert "verbatim quote" in first.text and "   13| X = scaler.fit_transform(X)" in first.text
    assert "preprocess-before-split" in first.text and len(calls[ml.reviewer]) == 2
    assert out["status"] == "FAIL" and out["high"] >= 4
    table = {(r["rule"], r["line"]): r for r in rows(paper)}
    assert table[("preprocess-before-split", "13")]["status"] == "confirmed"
    assert table[("duplicates-unchecked", "15")]["status"] == "dismissed"
    assert table[("target-leakage", "11")]["status"] == "open"          # its quote is not in the file
    own = table[("review:refit-on-all", "23")]
    assert own["severity"] == "medium" and own["status"] == "confirmed"
    assert not any(r["rule"] == "review:made-up" for r in rows(paper))
    report = open(out["report"], encoding="utf-8").read()
    assert "实验方法检查" in report and "FAIL" in report and "规则说明" in report
    run = json.load(open(out["run"], encoding="utf-8"))
    assert [s["stage"] for s in run["stages"]] == ["rules", "review train.py", "review data.py", "merge"]


def test_a_rerun_keeps_the_authors_statuses(tmp_path):
    paper = make(tmp_path)
    with script_roles({ml.reviewer: [NOTHING, NOTHING]}):
        method_lint(str(paper))
    table = rows(paper)
    for r in table:
        if r["severity"] == "high":
            r["status"] = "fixed" if r["rule"] == "preprocess-before-split" else "ignore"
    with open(paper / "workflow" / "method_lint.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ml.COLUMNS)
        w.writeheader()
        w.writerows(table)
    (paper / "train.py").write_text("# moved down a line\n" + LEAKY)
    with script_roles({ml.reviewer: [NOTHING, NOTHING]}):
        out = json.loads(method_lint(str(paper)))
    again = rows(paper)
    assert out["status"] == "PASS_WITH_NOTES" and out["high"] == 0
    assert next(r for r in again if r["rule"] == "preprocess-before-split")["status"] == "fixed"
    assert next(r for r in again if r["rule"] == "preprocess-before-split")["line"] == "14"
    assert next(r for r in again if r["rule"] == "target-leakage")["status"] == "ignore"
    assert len(again) == len(table)


def test_the_clean_experiment_passes(tmp_path):
    paper = make(tmp_path, train=CLEAN)
    with script_roles({ml.reviewer: [NOTHING, NOTHING]}):
        out = json.loads(method_lint(str(paper)))
    assert out["status"] == "PASS" and rows(paper) == []


def test_files_come_from_edit_command_and_imports(tmp_path):
    paper = make(tmp_path)
    pkg = paper / "lib"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .util import helper\n")
    (pkg / "util.py").write_text("def helper():\n    return 1\n")
    (paper / "data.py").write_text("import lib\nimport numpy\n" + "x = 1\n")
    (paper / ".env").write_text("SECRET=1\n")
    values = {"Edit": "train.py, config.yaml", "Command": "python -m tools.run --seed 1"}
    (paper / "tools").mkdir()
    (paper / "tools" / "run.py").write_text("import data\n")
    files = ml.code_files(paper, values)
    assert files == ["train.py", "tools/run.py", "data.py", "lib/__init__.py", "lib/util.py"]
    assert ml.quote_line("a = 1\nb  =  2\n", "b = 2") == 2 and ml.quote_line("a = 1\n", "c") is None
