"""settings_check with a scripted extractor and a config with one mismatch."""

import csv
import json

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.reproduce import settings as st
from auto_research.tasks.reproduce import settings_check

TEX = r"""\documentclass{article}
\begin{document}
\section{Setup}
We train with AdamW at a learning rate of $3\times10^{-4}$
and a batch size of 64 for 10 epochs.
\input{appendix}
\bibliography{refs}
\end{document}
"""
APPENDIX = "Dropout is 0.1 and we use 4 attention heads.\n"
CONFIG = "model:\n  num_heads: 4\n  dropout: 0.2   # tried 0.1 first\ntrain:\n  batch_size: 64\n"
TRAIN = """import argparse
import torch
p = argparse.ArgumentParser()
p.add_argument("--lr", type=float, default=0.0003)
p.add_argument("--epochs", type=int, default=10)
opt = torch.optim.AdamW(params, lr=3e-4)
"""
SETTINGS = {"settings": [
    {"name": "learning_rate", "value": r"3\times10^{-4}", "unit": "", "quote": "learning rate of $3\\times10^{-4}$"},
    {"name": "Batch size", "value": "64", "unit": "", "quote": "a batch size of 64"},
    {"name": "num_epochs", "value": "10", "unit": "epochs", "quote": "for 10 epochs."},
    {"name": "optimizer", "value": "AdamW", "unit": "", "quote": "We train with AdamW"},
    {"name": "dropout", "value": "0.1", "unit": "", "quote": "Dropout is 0.1"},
    {"name": "attention_heads", "value": "4", "unit": "", "quote": "4 attention heads"},
    {"name": "label_smoothing", "value": "0.1", "unit": "", "quote": ""}]}


@pytest.fixture
def tex(paper):
    (paper / "main.tex").write_text(TEX)
    (paper / "appendix.tex").write_text(APPENDIX)
    (paper / "config.yaml").write_text(CONFIG)
    (paper / "train.py").write_text(TRAIN)
    venv = paper / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "other.py").write_text("dropout = 0.1\n")        # a virtualenv is not the paper's code
    return paper


def rows(paper):
    with open(paper / "workflow" / "settings.csv", encoding="utf-8") as f:
        return {r["setting"]: r for r in csv.DictReader(f)}


def test_settings_are_found_in_the_code_and_a_mismatch_fails(tex):
    with script_roles({st.extractor: [SETTINGS]}) as calls:
        out = json.loads(settings_check(str(tex)))
    request = calls[st.extractor][0]
    assert "a quote copied character for character" in request.text           # docstring
    assert "Dropout is 0.1" in request.text and "refs" not in request.text
    assert out["status"] == "FAIL" and out["mismatch"] == 1 and out["not_found"] == 1
    table = rows(tex)
    assert table["learning_rate"]["status"] == "match"
    assert "train.py:4" in table["learning_rate"]["code_location"]
    assert table["learning_rate"]["paper_location"].startswith("main.tex:4:")
    assert table["batch_size"]["status"] == "match" and "config.yaml:5" in table["batch_size"]["code_location"]
    assert table["num_epochs"]["status"] == "match" and table["num_epochs"]["value"] == "10 epochs"
    assert table["optimizer"]["status"] == "match"
    assert table["attention_heads"]["status"] == "match"
    assert table["dropout"]["status"] == "mismatch" and "config.yaml:3" in table["dropout"]["code_location"]
    assert table["dropout"]["paper_location"].startswith("appendix.tex:1:")
    assert table["label_smoothing"]["status"] == "not_found"
    assert ".venv" not in json.dumps(table)
    report = open(out["report"], encoding="utf-8").read()
    assert "不一致" in report and "dropout" in report
    run = json.load(open(out["run"], encoding="utf-8"))
    assert [s["stage"] for s in run["stages"]] == ["read", "extract", "search", "compare"]


def test_a_rerun_keeps_the_authors_edits(tex):
    with script_roles({st.extractor: [SETTINGS]}):
        settings_check(str(tex))
    table = rows(tex)
    table["label_smoothing"]["code_location"] = "train.py:9"
    table["dropout"]["status"] = "ignore"
    table["batch_size"]["value"] = "128"                                     # author corrects the reading
    table["warmup"] = {"setting": "warmup", "value": "500", "paper_location": "", "code_location": "",
                       "status": ""}
    (tex / "train.py").write_text(TRAIN + "\n\nlabel_smoothing = 0.1\n")
    with open(tex / "workflow" / "settings.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=st.COLUMNS)
        w.writeheader()
        w.writerows(table.values())
    with script_roles({st.extractor: [SETTINGS]}):
        out = json.loads(settings_check(str(tex)))
    table = rows(tex)
    assert table["label_smoothing"]["status"] == "match"
    assert table["label_smoothing"]["code_location"] == "train.py:9"
    assert table["dropout"]["status"] == "ignore"
    assert table["batch_size"]["value"] == "128" and table["batch_size"]["status"] == "mismatch"
    assert table["warmup"]["status"] == "not_found"
    assert out["status"] == "FAIL" and out["ignore"] == 1


def test_values_and_names_compare_loosely():
    assert st.same("1e-4", "0.0001") and st.same(r"$1\times10^{-4}$", "1e-4") and st.same("4k", "4000")
    assert st.same("10%", "0.1") and st.same("AdamW", "adamw") and not st.same("Adam", "adamw")
    assert not st.same("0.1", "0.2")
    assert st.canonical("number of training epochs") == "epochs"
    assert st.canonical("hidden dimension") == "hidden" and st.canonical("d_model", words=False) == "hidden"
    assert st.canonical("head_dim", words=False) == "head_dim"
    with pytest.raises(ValueError, match="without a name"):
        st.check_settings({"settings": [{"name": "", "value": "1"}]})


def test_a_config_value_overrides_the_code_default(tmp_path):
    row = {"setting": "batch_size", "value": "32", "paper_location": "", "code_location": "", "status": ""}
    found = [{"canon": "batch_size", "key": "--batch_size", "value": "32", "where": "train.py:6"},
             {"canon": "batch_size", "key": "batch_size", "value": "64", "where": "config.json:1"}]
    got = st.check_row(tmp_path, row, found)
    assert got["status"] == "mismatch" and got["code_location"] == "config.json:1 (batch_size=64)"
    found[1]["value"] = "32"
    assert st.check_row(tmp_path, row, found)["status"] == "match"
