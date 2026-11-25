"""result_numbers on fixture repositories: numbers and derived claims from the paper,
sources with their Git history, the code mapping, a scripted mapper for the rest, every
verdict, and edits that apply_edit applies."""

import json
import subprocess

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.proofread import apply_edit
from auto_research.tasks.resultnumbers import mapper, result_numbers
from auto_research.tasks.resultnumbers import numbers as nb
from auto_research.tasks.resultnumbers import sources as src

MAIN = r"""\documentclass{article}
\begin{document}
\begin{abstract}
Our schedule reaches a validation loss of 0.0102, 26\% lower than the default schedule.
\end{abstract}
\section{Method}
We use a learning rate of 0.05 and train for 10 epochs.
\input{results}
\end{document}
"""
RESULTS_TEX = r"""\section{Results}
Our tuned schedule reaches a validation loss of 0.0102 compared with 0.0137 for the default schedule.
Prior work reports 0.0200 on a similar task \cite{other}.

\begin{table}[t]
\begin{tabular}{lc}
\toprule
Method & Test accuracy \\
\midrule
Ours & 91.2 \\
\bottomrule
\end{tabular}
\caption{Test accuracy.}
\label{tab:main}
\end{table}
"""
SPEC = "# Experiment loop\nCommand: python train.py\nMetric: val_loss\nDirection: lower\n"
RUNS = ("commit\tmetric\tstatus\tseconds\tdescription\n"
        "aaa1111\t0.013727\tkeep\t3\tbaseline\n"
        "bbb2222\t0.010215\tkeep\t3\ttuned\n"
        "ccc3333\t0.018371\tdiscard\t3\tlr 0.5\n")
EVIDENCE = ("id,claim,statement,source,script\n"
            "E1,C1,Loop runs,workflow/results.tsv,train.py\n"
            "E2,C1,Accuracy of the final model,results/metrics.json,eval.py\n")
METRICS = {"val": {"accuracy": 0.905}, "test": {"accuracy": 0.912}}


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                   capture_output=True)


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    (repo / "results").mkdir()
    git(repo, "init", "-q")
    (repo / "main.tex").write_text(MAIN)
    (repo / "results.tex").write_text(RESULTS_TEX)
    for name, text in (("experiment.md", SPEC), ("results.tsv", RUNS), ("evidence.csv", EVIDENCE)):
        (repo / "workflow" / name).write_text(text)
    (repo / "results" / "metrics.json").write_text(json.dumps(METRICS))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "paper")
    return repo


def run(repo, tmp_path, replies=()):
    with script_roles({mapper: list(replies)}) as calls:
        out = json.loads(result_numbers(str(repo), out_dir=str(tmp_path / "out")))
    record = json.loads(open(out["json"]).read())
    by = {i["value"] + "@" + i["location"].split()[0]: i for i in record["numbers"] + record["derived"]}
    return out, record, calls, by


def test_decimals_rounding_and_scale():
    assert src.same("0.0102", "0.010215") and not src.same("0.0102", "0.010277")
    assert not src.same("0.0102", "0")                       # rounding never erases the paper's digits
    assert src.same("91.2", "0.912") and src.same("26", "0.26", percent=True)
    assert src.same("85", "85.4") and not src.same("85", "85.6")
    assert src.written("0.010277", "0.0102") == "0.0103" and src.written("0.912", "90.5") == "91.2"


def test_numbers_settings_cites_and_derived(paper):
    full, numbers, derived, skipped = nb.read_paper(paper, "main.tex")
    values = [(n["value"], n["kind"], n["split"]) for n in numbers]
    assert ("0.0102", "sentence", "val") in values and ("0.0137", "sentence", "val") in values
    assert ("91.2", "table", "test") in values
    assert all(v != "0.05" and v != "0.0200" for v, _, _ in values)    # a setting and a cited number
    assert skipped == {"cited": 1, "setting": 1}
    assert [(d["kind"], d["value"], d["word"]) for d in derived] == [("relative", "26", "lower")]
    table = next(n for n in numbers if n["kind"] == "table")
    assert table["column"] == "Test accuracy" and table["method"] == "Ours"
    files = nb.paper_files(paper, "main.tex")
    at = nb.locate(files, table["raw"], table["start"], table["end"])
    assert at["file"] == "results.tex" and at["quote"][at["at"]:at["at"] + 4] == "91.2"
    text = (r"\section{R}" "\nIt improves accuracy by 2.1 points and is 3.5$\\times$ faster. "
            "Loss drops from 0.50 to 0.40 here. Ours outperforms the baseline clearly.\n")
    _, ds = nb.sentence_items(r"\begin{document}" + text, {"cited": 0, "setting": 0})
    assert [(d["kind"], d["value"]) for d in ds] == [("absolute", "2.1"), ("ratio", "3.5"), ("from_to", ""),
                                                     ("outperforms", "")]


def test_every_number_maps_in_code_and_the_mapper_only_gets_the_derived_claim(paper, tmp_path):
    reply = {"numbers": [], "derived": [{"id": "D1", "found": True, "a": "S2", "b": "S1",
                                         "quote": "26\\% lower than the default schedule"}]}
    out, record, calls, by = run(paper, tmp_path, [reply])
    assert out["status"] == "PASS", record
    assert {i["status"] for i in record["numbers"]} == {"match"}
    assert by["0.0102@§Abstract"]["how"] == "code" and by["91.2@Table"]["source"]["split"] == "test"
    assert by["26@§Abstract"]["recomputed"] == "26"
    assert '"id": "N' not in calls[mapper][0].text and '"id": "D1"' in calls[mapper][0].text
    assert (paper / "workflow" / "result_numbers.csv").read_text().count("\n") == 6


def test_rerun_makes_numbers_stale_and_the_derived_claim_wrong(paper, tmp_path):
    with open(paper / "workflow" / "results.tsv", "a") as f:
        f.write("ddd4444\t0.010277\tkeep\t3\ttuned\n")                 # the tuned run again, after a change
    git(paper, "commit", "-qam", "rerun")
    (paper / "results.tex").write_text(RESULTS_TEX.replace(
        "for the default schedule.", "for the default schedule.\nOn the validation split it reaches 91.2 accuracy."))
    reply = {"numbers": [],                     # the validation sentence with the test value is mapped in code
             "derived": [{"id": "D1", "found": True, "a": "S4", "b": "S1", "quote": "26\\% lower"}]}
    out, record, calls, by = run(paper, tmp_path, [reply])
    assert out["status"] == "FAIL"
    stale = by["0.0102@§Abstract"]
    assert stale["status"] == "stale" and stale["replacement"] == "0.0103" and stale["found_in"] == "earlier row bbb2222"
    assert by["0.0137@§Results"]["status"] == "match"
    wrong = by["91.2@§Results"]
    assert wrong["how"] == "code" and wrong["status"] == "misattributed" and "split test" in wrong["found_in"] and wrong["replacement"] == "90.5"
    assert by["26@§Abstract"]["status"] == "derived_wrong" and by["26@§Abstract"]["recomputed"] == "25"
    edits = json.loads(open(out["edits"]).read())["edits"]
    assert len(edits) == 3 and {e["file"] for e in edits} == {"main.tex", "results.tex"}   # one sentence, one edit
    assert by["0.0102@§Abstract"]["edit"] == by["26@§Abstract"]["edit"]
    for e in edits:
        assert apply_edit(str(paper), e)
    assert "0.0103, 25\\% lower" in (paper / "main.tex").read_text()
    assert "reaches 90.5 accuracy" in (paper / "results.tex").read_text()
    assert "过期" in open(out["report"]).read() and "张冠李戴" in open(out["report"]).read()


def test_a_value_overwritten_in_place_is_stale_through_git_history(paper, tmp_path):
    (paper / "results" / "metrics.json").write_text(json.dumps({"val": {"accuracy": 0.905}, "test": {"accuracy": 0.9}}))
    git(paper, "commit", "-qam", "re-evaluate")
    reply = {"numbers": [], "derived": [{"id": "D1", "found": True, "a": "S2", "b": "S1", "quote": "26\\% lower"}]}
    out, record, calls, by = run(paper, tmp_path, [reply])
    n = by["91.2@Table"]
    assert n["status"] == "stale" and n["found_in"].startswith("revision ") and n["replacement"] == "90.0"
    assert n["source_newer"] is True


def test_a_mapper_answer_under_another_split_goes_to_the_split_the_text_names(paper):
    from auto_research.tasks.resultnumbers import workflow as wf
    entries, _, _ = src.collect(paper, "val_loss")
    current = [e for e in entries if e["current"]]
    test = next(e for e in current if e["split"] == "test")
    n = {"id": "N1", "value": "91.2", "percent": False, "split": "val", "metric": "accuracy", "key": None}
    text = "On the validation split it reaches 91.2 accuracy."
    reply = {"numbers": [{"id": "N1", "kind": "result", "source": test["sid"], "evidence": "", "commit": "",
                          "run": "", "metric": "", "split": "", "quote": "reaches 91.2 accuracy"}], "derived": []}
    assert wf.apply_mapping(reply, [n], [], current, text, text) == 0
    wf.verdict(n, entries)
    assert n["key"][4] == "val" and n["status"] == "misattributed" and n["replacement"] == "90.5"


def test_bad_mapper_answers_are_dropped_and_leave_numbers_unsourced(paper, tmp_path):
    (paper / "results.tex").write_text(RESULTS_TEX + "\nThe probe needs 0.333 GB of memory.\n")
    reply = {"numbers": [{"id": "N5", "kind": "result", "source": "S99", "evidence": "", "commit": "", "run": "",
                          "metric": "", "split": "", "quote": "needs 0.333 GB"}],
             "derived": [{"id": "D1", "found": True, "a": "S2", "b": "S1", "quote": "not in the paper at all"}]}
    out, record, calls, by = run(paper, tmp_path, [reply])
    assert out["status"] == "PASS_WITH_NOTES" and record["dropped_mappings"] == 2
    assert by["0.333@§Results"]["status"] == "unsourced" and by["26@§Abstract"]["status"] == "unsourced"
    with pytest.raises(ValueError, match="not shown"):
        run(paper, tmp_path, [{"numbers": [{"id": "N42", "kind": "unsourced", "source": "", "evidence": "",
                                            "commit": "", "run": "", "metric": "", "split": "", "quote": ""}],
                               "derived": []}])


def test_ignored_rows_are_kept_and_not_judged(paper, tmp_path):
    (paper / "workflow" / "result_numbers.csv").write_text(
        "id,kind,location,value,status\nN9,derived:relative,§Abstract ¶1,26,ignore\n")
    out, record, calls, by = run(paper, tmp_path)
    assert out["status"] == "PASS" and not record["derived"] and record["ignored"] == 1
    assert ",ignore," in (paper / "workflow" / "result_numbers.csv").read_text()
    assert calls[mapper] == []


def test_not_a_repository(tmp_path):
    with pytest.raises(ValueError):
        result_numbers(str(tmp_path / "missing"))
