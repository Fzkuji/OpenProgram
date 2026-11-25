"""The consensus_analysis entry with the analyst scripted: agreement, disagreement,
citations that do not hold, the runs bounds and the cap on data files."""

import json
import subprocess

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.consensus import workflow as wf

RESULTS = ("commit\tmetric\tstatus\tseconds\tdescription\n"
           "a1\t0.712\tkeep\t300\tbaseline lr 1e-3\n"
           "b2\t0.745\tkeep\t310\twarmup 500 steps\n"
           "c3\t0.701\tdiscard\t305\tno warmup, seed 2\n")
EVIDENCE = ("id,claim,statement,source,script\n"
            "E1,C1,Warmup raises accuracy from 71.2 to 74.5,analysis/main.csv,scripts/main.py\n")
QUESTION = "warmup 是否提高了准确率？"


def reply(verdict, numbers, answer="warmup 把准确率从 0.712 提到 0.745。", caveats=()):
    return {"answer": answer, "verdict": verdict, "caveats": list(caveats),
            "numbers": [{"value": v, "what": w, "cite": c} for v, w, c in numbers]}


BASE = ("0.712", "基线准确率", "workflow/results.tsv:1")
WARM = ("0.745", "warmup 后准确率", "workflow/results.tsv:2")


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "workflow" / "results.tsv").write_text(RESULTS)
    (repo / "workflow" / "evidence.csv").write_text(EVIDENCE)
    return repo


def run(paper, **kw):
    return json.loads(wf.consensus_analysis(str(paper), QUESTION, **kw))


def test_runs_that_agree_pass_with_the_evidence_they_share(paper):
    replies = [reply("yes", [BASE, WARM]),
               reply("yes", [BASE, WARM, ("74.5", "证据里的数", "workflow/evidence.csv:1")]),
               reply("no", [("0.701", "无 warmup 的一次", "workflow/results.tsv:3")], answer="没有。",
                     caveats=["c3 用的是另一个 seed"])]
    with script_roles({wf.analyst: replies}) as calls:
        out = run(paper)
    asks = calls[wf.analyst]
    assert len(asks) == 3
    assert all("exactly as shown" in a.text for a in asks)          # docstring
    assert all("row 2: commit=b2; metric=0.745" in a.text and QUESTION in a.text for a in asks)
    assert [wf.ANGLES[k] in asks[k].text for k in range(3)] == [True] * 3                  # one angle each
    assert wf.ANGLES[1] not in asks[0].text
    assert "E1,C1,The number" not in asks[0].text                                         # no template rows
    assert out["status"] == "PASS" and out["verdict"] == "yes" and out["agreement"] == pytest.approx(2 / 3, abs=1e-3)
    assert [a["verdict"] for a in out["answers"]] == ["yes", "yes", "no"]
    record = json.load(open(out["json"], encoding="utf-8"))
    assert [c["cite"] for c in record["agreed"]] == ["workflow/results.tsv:1", "workflow/results.tsv:2"]
    assert {c["cite"] for c in record["unconfirmed"]} == {"workflow/evidence.csv:1", "workflow/results.tsv:3"}
    assert record["dissent"] == [3]
    report = open(out["report"], encoding="utf-8").read()
    assert report.index("结论：warmup 把准确率") < report.index("一致程度")
    assert "`workflow/results.tsv:2`" in report and "第 3 次（否）：没有。" in report
    assert "c3 用的是另一个 seed" in report and "未经确认的说法" in report
    trail = json.load(open(out["run"], encoding="utf-8"))
    assert [s["stage"] for s in trail["stages"]] == ["gather", "analyse 1", "analyse 2", "analyse 3", "check",
                                                     "consensus"]
    assert out["report"].startswith(str(paper / "drafts" / "consensus_analysis"))
    assert (paper / "workflow" / "results.tsv").read_text() == RESULTS


def test_runs_that_disagree_fail(paper):
    replies = [reply("yes", [WARM]), reply("no", [BASE]), reply("mixed", []), reply("cannot_tell", [])]
    with script_roles({wf.analyst: replies}):
        out = run(paper, runs="4")
    assert out["status"] == "FAIL" and out["agreement"] == 0.25
    assert out["verdict"] == "cannot_tell"                     # a tie goes to the cautious verdict
    report = open(out["report"], encoding="utf-8").read()
    assert "现有数据回答不了这个问题" in report
    # half the runs agree: notes, not a pass; two-thirds on cannot_tell is no pass either
    with script_roles({wf.analyst: [reply("yes", [WARM]), reply("no", [BASE])]}):
        assert run(paper, runs="2")["status"] == "PASS_WITH_NOTES"
    with script_roles({wf.analyst: [reply("cannot_tell", []), reply("cannot_tell", []), reply("yes", [])]}):
        assert run(paper)["status"] == "PASS_WITH_NOTES"


def test_citations_that_do_not_hold_are_dropped_and_counted(paper):
    bad = [("0.745", "行号不存在", "workflow/results.tsv:9"),
           ("0.80", "行里没有这个数", "workflow/results.tsv:2"),
           ("0.7", "文件不存在", "other.csv:1"),
           ("0.75", "四舍五入可以", "workflow/results.tsv:2"),
           ("1", "格式不对", "results.tsv")]
    with script_roles({wf.analyst: [reply("yes", bad), reply("yes", [WARM])]}):
        out = run(paper, runs="2")
    assert out["answers"][0]["dropped"] == 4
    record = json.load(open(out["json"], encoding="utf-8"))
    first = record["answers"][0]
    assert [n["cite"] for n in first["numbers"]] == ["workflow/results.tsv:2"]
    assert {i["why"] for i in first["invalid"]} == {"no such row", "the number is not in that row"}
    assert record["dropped"] == 4 and [c["cite"] for c in record["agreed"]] == ["workflow/results.tsv:2"]
    assert "共 4 条" in open(out["report"], encoding="utf-8").read()
    with pytest.raises(ValueError, match="unknown verdict"):
        wf.check_answer({"answer": "x", "verdict": "maybe", "numbers": []}, {})


def test_runs_must_be_two_to_seven_and_data_must_exist(paper, tmp_path):
    for bad in ("1", "8", "x"):
        with pytest.raises(ValueError, match="runs"):
            run(paper, runs=bad)
    with pytest.raises(ValueError, match="outside"):
        run(paper, data="../elsewhere.csv")
    with pytest.raises(ValueError, match="does not exist"):
        run(paper, data="missing.csv")
    (paper / "notes.txt").write_text("1")
    with pytest.raises(ValueError, match="not CSV"):
        run(paper, data="notes.txt")
    empty = tmp_path / "empty"
    subprocess.run(["git", "init", "-q", str(empty)], check=True)
    with pytest.raises(ValueError, match="no data"):
        run(empty)


def test_data_files_are_capped_and_the_cut_is_said(paper):
    (paper / "analysis").mkdir()
    (paper / "analysis" / "big.csv").write_text("step,loss\n" + "".join(f"{i},{i / 1000}\n" for i in range(1, 301)))
    (paper / "analysis" / "runs.json").write_text(json.dumps([{"seed": 1, "acc": 0.74}, {"seed": 2, "acc": 0.75}]))
    files = {f["name"]: f for f in wf.gather(paper, "analysis/big.csv, analysis/runs.json")}
    big = files["analysis/big.csv"]
    assert len(big["rows"]) == wf.MAX_ROWS and big["total"] == 300 and big["cut"]
    assert "[cut: 200 of 300 rows shown" in wf.show(big)
    assert files["analysis/runs.json"]["rows"][1] == {"row": 2, "text": '{"seed": 2, "acc": 0.75}'}
    wide = wf.cap("wide.csv", [{"row": i, "text": "x" * 1000} for i in range(1, 100)])
    assert wide["cut"] and len(wide["rows"]) * 1000 <= wf.MAX_CHARS
    late = ("0.25", "被截掉的行", "analysis/big.csv:250")
    with script_roles({wf.analyst: [reply("yes", [late, ("0.75", "第二个种子", "analysis/runs.json:2")])] * 2}) as calls:
        out = run(paper, runs="2", data="analysis/big.csv,analysis/runs.json")
    assert "analysis/runs.json" in calls[wf.analyst][0].text
    assert [a["dropped"] for a in out["answers"]] == [1, 1]          # rows past the cut cannot be cited
    assert "太长" in open(out["report"], encoding="utf-8").read()
