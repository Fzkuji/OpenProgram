"""data_explore on a tiny CSV with the proposer, believer and analyst scripted; the
analysis scripts really run in a subprocess under the guard."""

import csv
import json
import math
import subprocess

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.discovery import explore as wf

SALES = ("region,price,units,promo\n"
         "north,10,50,yes\nnorth,12,44,no\nnorth,14,40,yes\nnorth,16,35,no\n"
         "south,10,30,yes\nsouth,12,29,no\nsouth,14,27,yes\nsouth,16,26,no\n"
         "east,10,41,yes\neast,12,,no\n")

# Pearson correlation of price and units, stdlib only.
CORR = """import csv, json, math
rows = [r for r in csv.DictReader(open("data/sales.csv")) if r["units"]]
x = [float(r["price"]) for r in rows]; y = [float(r["units"]) for r in rows]
mx, my = sum(x) / len(x), sum(y) / len(y)
r = sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
print("rows", len(rows))
print(json.dumps({"result": f"price and units correlate, r = {r:.3f}", "statistic": round(r, 4), "p": 0.04}))
"""
BROKEN = "import csv\nprint(undefined_name)\n"
MEANS = """import csv, json
rows = list(csv.DictReader(open("data/sales.csv")))
north = [float(r["units"]) for r in rows if r["region"] == "north"]
south = [float(r["units"]) for r in rows if r["region"] == "south"]
print(json.dumps({"result": f"north {sum(north)/4:.2f} vs south {sum(south)/4:.2f}", "statistic": 9.25, "p": 0.3}))
"""


def snoop(path):
    return f"import json\nprint(open({str(path)!r}).read())\nprint(json.dumps({{'result': 'x', 'p': 0.5}}))\n"


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "data").mkdir(parents=True)
    (repo / "workflow").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "data" / "sales.csv").write_text(SALES)
    (repo / "workflow" / "secret.md").write_text("private notes\n")
    return repo


def hyp(text):
    return {"hypothesis": text, "rationale": "值得一看"}


def prob(p):
    return {"probability": p, "reason": "依据"}


def script(code):
    return {"script": code, "explains": "检验"}


def test_tree_search_runs_scripts_and_ranks_by_surprise(paper):
    replies = {
        wf.proposer: [hyp("Higher price goes with fewer units sold."),
                      hyp("North sells more units than south."),
                      hyp("The price effect is weaker in the south.")],
        wf.believer: [prob(0.2), prob(0.95), prob(0.5), prob(0.55), prob(0.4)],
        wf.analyst: [script(CORR), script(BROKEN), script("```python\n" + MEANS + "```"),
                     script(snoop(paper / "workflow" / "secret.md")), script(snoop(paper / "data" / "sales.csv"))],
    }
    with script_roles(replies) as calls:
        out = json.loads(wf.data_explore(str(paper), "data/sales.csv", budget="3"))

    schema_text = calls[wf.proposer][0].text
    assert "file data/sales.csv: 10 rows" in schema_text
    assert "price (int, 0 missing): range 10 to 16" in schema_text and "units (int, 1 missing)" in schema_text
    assert "region (str, 0 missing): 3 distinct" in schema_text and "falsifiable" in schema_text
    assert "(root: no hypothesis yet" in calls[wf.proposer][0].text
    assert "H1: Higher price goes with fewer units sold." in calls[wf.proposer][2].text      # the path to H1
    assert "Before any analysis" in calls[wf.believer][0].prompt
    assert "price and units correlate" in calls[wf.believer][1].text                       # posterior sees output
    assert "NameError" in calls[wf.analyst][2].text                                        # the retry sees the error
    assert "installed" in calls[wf.analyst][0].text.lower()

    ranked = out["ranked"]
    assert [h["id"] for h in ranked] == ["H1", "H2", "H3"]
    assert {h["id"]: h["parent"] for h in ranked} == {"H1": "", "H2": "", "H3": "H1"}       # UCB1 descends into H1
    h1 = ranked[0]
    kl = 0.95 * math.log(0.95 / 0.2) + 0.05 * math.log(0.05 / 0.8)
    assert h1["surprise"] == pytest.approx(kl, abs=1e-5)
    assert ranked[2]["status"] == "error" and ranked[2]["posterior"] == ranked[2]["prior"] == 0.4
    assert out["status"] == "PASS_WITH_NOTES" and out["candidates"] == ["H1"]

    rows = list(csv.DictReader(open(out["hypotheses"], encoding="utf-8")))
    assert list(rows[0]) == list(wf.COLUMNS)
    assert [r["id"] for r in rows] == ["H1", "H2", "H3"] and rows[2]["parent"] == "H1"
    assert json.loads(rows[0]["output"])["statistic"] == pytest.approx(-0.6, abs=0.2)
    assert rows[1]["statistic"] == "9.25" and rows[1]["p"] == "0.3"
    assert rows[2]["output"].startswith("error:") and "PermissionError" in rows[2]["output"]
    assert (paper / rows[0]["script"]).read_text() == CORR
    assert (paper / rows[1]["script"]).read_text() == MEANS                                 # fence stripped
    record = json.load(open(out["json"], encoding="utf-8"))
    h3 = next(h for h in record["hypotheses"] if h["id"] == "H3")
    assert h3["attempts"] == 2 and "read only their data files" in h3["error"]
    report = open(out["report"], encoding="utf-8").read()
    assert "## F?: Higher price goes with fewer units sold" in report and "Verdict: supported" in report
    assert "Run: drafts/data_explore/" in report
    assert not (paper / "workflow" / "findings.md").exists()
    trail = json.load(open(out["run"], encoding="utf-8"))
    stages = [s["stage"] for s in trail["stages"]]
    assert stages[:5] == ["schema", "propose H1", "prior H1", "analyse H1", "posterior H1"]
    assert "analyse H2 (retry 1)" in stages and stages[-1] == "rank"


def test_ids_continue_and_bad_arguments(paper):
    (paper / "workflow" / "hypotheses.csv").write_text(",".join(wf.COLUMNS) + "\nH7,,old,0.5,0.5,0,,,a.py,x\n")
    with script_roles({wf.proposer: [hyp("Promo raises units.")], wf.believer: [prob(0.5), prob(0.5)],
                       wf.analyst: [script(MEANS)]}):
        out = json.loads(wf.data_explore(str(paper), "data/sales.csv", budget="1", out_dir=str(paper / "o")))
    assert out["ranked"][0]["id"] == "H8" and out["ranked"][0]["surprise"] == 0
    assert out["candidates"] == [] and out["report"].startswith(str(paper / "o"))
    lines = (paper / "workflow" / "hypotheses.csv").read_text().splitlines()
    assert len(lines) == 3 and lines[1].startswith("H7,")
    for bad, data in (("0", "data/sales.csv"), ("41", "data/sales.csv"), ("2", ""), ("2", "../x.csv"),
                      ("2", "data/missing.csv"), ("2", "workflow/secret.md")):
        with pytest.raises(ValueError):
            wf.data_explore(str(paper), data, budget=bad)


def test_surprise_and_selection():
    assert wf.surprise(0.5, 0.5) == (0.0, 0.0)
    up, down = wf.surprise(0.3, 0.9), wf.surprise(0.9, 0.3)
    assert up[1] == 0.6 and down[1] == -0.6 and up[0] > 0 and down[0] > 0
    hi, lo = 1 - 1e-3, 1e-3
    assert wf.surprise(0.0, 1.0)[0] == pytest.approx(hi * math.log(hi / lo) + lo * math.log(lo / hi), rel=1e-5)
    assert wf.check_result({"result": "x", "p": 1.5})[0] is None
    assert wf.check_result(wf.parse_result('noise\n{"result": "y", "statistic": "2.5", "p": null}\n'))[0] == \
        {"result": "y", "statistic": 2.5, "p": None}
