"""import_tracking from W&B and MLflow exports and an mlruns/ folder."""

import csv
import json

import pytest

from auto_research.tasks.reproduce import import_tracking

from .repo import commit

WANDB = """Name,ID,State,Tags,Runtime,Commit,val_loss,lr
lucky-1,abc123,finished,"keep, best",125,{commit},0.42,0.001
noisy-2,def456,finished,,98,,0.55,0.01
dead-3,ghi789,crashed,,3,,,0.1
"""
MLFLOW = """Start Time,Duration,Run ID,Name,Status,tags.mlflow.source.git.commit,metrics.val_loss,tags.keep
2026-11-01 10:00:00,1.5min,r1,base,FINISHED,{commit},0.47,true
2026-11-01 11:00:00,30s,r2,try,FINISHED,,,
"""


def tsv(paper):
    with open(paper / "workflow" / "results.tsv", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def test_wandb_export_appends_once(paper):
    sha = commit(paper, "run")
    (paper / "workflow" / "results.tsv").write_text(f"commit\tmetric\tstatus\tseconds\tdescription\n"
                                                    f"{sha}\t0.6\tkeep\t10\tby hand\n")
    (paper / "runs.csv").write_text(WANDB.format(commit=sha))
    out = json.loads(import_tracking(str(paper), "runs.csv"))
    assert out["kind"] == "wandb" and out["added"] == 3 and out["status"] == "PASS"
    rows = tsv(paper)
    assert rows[0]["description"] == "by hand" and rows[0]["source"] == ""
    assert [(r["source"], r["status"], r["metric"], r["seconds"]) for r in rows[1:]] == [
        ("wandb:abc123", "keep", "0.42", "125"), ("wandb:def456", "discard", "0.55", "98"),
        ("wandb:ghi789", "crash", "", "3")]
    assert rows[1]["commit"] == sha and rows[1]["description"] == "wandb run lucky-1 (abc123)"
    again = json.loads(import_tracking(str(paper), str(paper / "runs.csv")))
    assert again["added"] == 0 and again["skipped"] == 3 and len(tsv(paper)) == 4


def test_mlflow_csv_and_notes(paper):
    (paper / "export.csv").write_text(MLFLOW.format(commit="0" * 40))
    out = json.loads(import_tracking(str(paper), str(paper / "export.csv")))
    assert out["kind"] == "mlflow" and out["status"] == "PASS_WITH_NOTES"
    rows = tsv(paper)
    assert [(r["source"], r["status"], r["metric"], r["seconds"]) for r in rows] == [
        ("mlflow:r1", "keep", "0.47", "90"), ("mlflow:r2", "crash", "", "30")]
    assert any("不在仓库里" in n for n in out["notes"]) and any("没有 val_loss" in n for n in out["notes"])


def test_mlruns_folder(paper):
    sha = commit(paper, "run")
    run = paper / "mlruns" / "0" / "aa11"
    (run / "metrics").mkdir(parents=True)
    (run / "tags").mkdir()
    (run / "meta.yaml").write_text("run_id: aa11\nrun_name: sweep-a\nstatus: 3\nstart_time: 1000\n"
                                   "end_time: 61000\nlifecycle_stage: active\n")
    (run / "metrics" / "val_loss").write_text("1000 0.7 0\n2000 0.45 1\n")
    (run / "tags" / "mlflow.source.git.commit").write_text(sha)
    failed = paper / "mlruns" / "0" / "bb22"
    failed.mkdir()
    (failed / "meta.yaml").write_text("run_id: bb22\nstatus: 4\nlifecycle_stage: active\n")
    out = json.loads(import_tracking(str(paper), "mlruns"))
    rows = tsv(paper)
    assert [(r["source"], r["commit"], r["metric"], r["status"], r["seconds"]) for r in rows] == [
        ("mlflow:aa11", sha, "0.45", "discard", "60"), ("mlflow:bb22", "", "", "crash", "")]
    assert out["added"] == 2


def test_bad_sources(paper):
    with pytest.raises(ValueError, match="not a file"):
        import_tracking(str(paper), "nowhere.csv")
    with pytest.raises(ValueError, match="WANDB_API_KEY|wandb package"):
        import_tracking(str(paper), "wandb:me/project")
