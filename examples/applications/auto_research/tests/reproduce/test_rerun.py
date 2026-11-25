"""clean_rerun on a real tiny repository: clone, empty requirements, run, compare."""

import json
import tempfile
from pathlib import Path

import pytest

from auto_research.tasks.reproduce import clean_rerun
from auto_research.tasks.reproduce import rerun as rr

from .repo import commit


def results(repo, rows):
    text = "commit\tmetric\tstatus\tseconds\tdescription\n" + "".join("\t".join(r) + "\n" for r in rows)
    (repo / "workflow" / "results.tsv").write_text(text)


def test_the_best_kept_run_reproduces_from_a_clean_clone(paper):
    first = commit(paper, "0.5 run")
    (paper / "train.py").write_text("print('val_loss: 0.9')\n")
    second = commit(paper, "worse run")
    results(paper, [(first, "0.5", "keep", "3", "baseline"), (second, "0.9", "discard", "3", "worse")])
    (paper / "train.py").write_text("print('local edit')\n")            # uncommitted, must stay
    out = json.loads(clean_rerun(str(paper)))
    assert out["status"] == "PASS", out
    assert [(r["commit"], r["recorded"], r["rerun"], r["status"]) for r in out["runs"]] == \
        [(first, 0.5, 0.5, "reproduced")]
    assert out["environment"] == "venv"
    log = open(out["runs"][0]["log"], encoding="utf-8").read()
    assert "val_loss: 0.5" in log
    assert (paper / "train.py").read_text() == "print('local edit')\n"
    data = json.load(open(out["rerun"], encoding="utf-8"))
    assert data["dirty"] is True and data["environment"]["declared"] == ["requirements.txt"]
    report = open(out["report"], encoding="utf-8").read()
    assert "全部复现" in report and "未提交的改动" in report
    assert out["report"].startswith(str(paper / "drafts" / "clean_rerun"))
    assert not list(Path(tempfile.gettempdir()).glob("clean_rerun-*"))


def test_a_metric_outside_the_tolerance_fails_and_crashes_are_reported(paper):
    first = commit(paper, "0.5 run")
    (paper / "train.py").write_text("raise SystemExit(3)\n")
    broken = commit(paper, "broken")
    results(paper, [(first, "0.49", "keep", "3", "claimed"), (broken, "0.4", "keep", "3", "broken")])
    out = json.loads(clean_rerun(str(paper), runs="all"))
    assert out["status"] == "FAIL"
    assert [r["status"] for r in out["runs"]] == ["mismatch", "crash"]
    assert out["runs"][0]["diff"] == pytest.approx(0.01)
    # 3% relative covers 0.49 vs 0.5; naming one commit reruns only that one
    out = json.loads(clean_rerun(str(paper), runs=first[:8], tolerance="3%"))
    assert out["status"] == "PASS" and len(out["runs"]) == 1
    out = json.loads(clean_rerun(str(paper), runs=first[:8], tolerance="0.02"))
    assert out["status"] == "PASS"


def test_no_declared_environment_is_a_note(paper):
    (paper / "requirements.txt").unlink()
    first = commit(paper, "run")
    results(paper, [(first, "0.5", "keep", "1", "run")])
    out = json.loads(clean_rerun(str(paper)))
    assert out["status"] == "PASS_WITH_NOTES"
    assert "没有声明环境" in open(out["report"], encoding="utf-8").read()


def test_conda_without_conda_is_unsupported(paper, monkeypatch):
    (paper / "environment.yml").write_text("name: x\ndependencies: [python]\n")
    first = commit(paper, "run")
    results(paper, [(first, "0.5", "keep", "1", "run")])
    real = rr.shutil.which
    monkeypatch.setattr(rr.shutil, "which", lambda name: None if name == "conda" else real(name))
    out = json.loads(clean_rerun(str(paper)))
    assert out["status"] == "PASS_WITH_NOTES"
    assert "没有 conda" in open(out["report"], encoding="utf-8").read()


def test_bad_arguments(paper):
    first = commit(paper, "run")
    results(paper, [(first, "0.5", "discard", "1", "run")])
    with pytest.raises(ValueError, match="no kept run"):
        clean_rerun(str(paper))
    with pytest.raises(ValueError, match="not a run"):
        clean_rerun(str(paper), runs="deadbeef")
    with pytest.raises(ValueError, match="timeout"):
        clean_rerun(str(paper), runs=first, timeout="soon")
    with pytest.raises(ValueError, match="tolerance"):
        clean_rerun(str(paper), runs=first, tolerance="a lot")
