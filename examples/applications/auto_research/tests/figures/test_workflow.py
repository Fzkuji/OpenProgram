"""The paper_figures entry: what it reads in a script, what it runs, and its report."""

import json
import subprocess
import sys

import pytest

from auto_research.tasks.figures import workflow as wf

GOOD = """from pathlib import Path
COLOR = "#0F4D92"
# apply_publication_style() is called by the real helpers
Path("fig1.pdf").write_text("drawn")
"""


@pytest.fixture
def paper(tmp_path, monkeypatch):
    repo = tmp_path / "paper"
    (repo / "workflow" / "figures").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "workflow" / "rules.json").write_text(json.dumps({"figure_python": sys.executable}))
    monkeypatch.setattr(wf, "can_plot", lambda program: True)
    return repo


def script(paper, name, text):
    (paper / "workflow" / "figures" / name).write_text(text)


def run(paper, **kw):
    return json.loads(wf.paper_figures(str(paper), **kw))


def test_a_script_that_follows_the_rules_is_run_and_its_figure_named(paper):
    script(paper, "plot_main.py", GOOD)
    script(paper, "paperfig.py", "raise SystemExit('the helper module is not a plotting script')")
    out = run(paper)
    assert out["status"] == "PASS" and out["scripts"] == 1
    assert out["figures"] == ["workflow/figures/fig1.pdf"]
    report = open(out["report"]).read()
    assert "plot_main.py" in report and "fig1.pdf" in report and "figures4papers" in report
    record = json.loads(open(out["run"]).read())
    assert [s["stage"] for s in record["stages"]] == ["find", "read", "draw workflow/figures/plot_main.py", "report"]
    assert record["roles"] == {}


def test_the_source_is_checked_against_the_figure_rules():
    allowed = wf.palette()
    assert "#0F4D92" in allowed and "#FFFFFF" in allowed
    found = wf.read('import matplotlib.pyplot as plt\nplt.bar([1], [2], color="#123456")\n'
                    'plt.savefig("a.png")\nplt.savefig("b.png", dpi=100)\nplt.savefig("c.pdf")\nplt.show()\n',
                    "plot_a.py", allowed)
    assert sorted((f["level"], f["check"]) for f in found) == [
        ("FAIL", "dpi"), ("FAIL", "dpi"), ("WARN", "palette"), ("WARN", "show"), ("WARN", "style")]
    assert any("line 3" in f["message"] for f in found) and any("dpi=100" in f["message"] for f in found)
    assert any("#123456" in f["message"] for f in found)
    clean = wf.read('apply_publication_style()\nfig.savefig("a.png")\nc = "#b64342"\n', "plot_b.py", allowed)
    assert clean == []                                        # the style sets 300 dpi; palette colors in any case
    assert wf.read("def (", "plot_c.py", allowed)[0]["check"] == "syntax"


def test_a_failing_script_fails_and_a_silent_one_is_noted(paper):
    script(paper, "plot_bad.py", "# axes.spines.top axes.spines.right\nraise RuntimeError('no data file')\n")
    script(paper, "plot_silent.py", "# apply_publication_style(\nprint('nothing saved')\n")
    out = run(paper)
    assert out["status"] == "FAIL" and out["counts"] == {"FAIL": 1, "WARN": 1} and out["figures"] == []
    assert any("no data file" in p for p in out["problems"]) and any("wrote no figure" in p for p in out["problems"])


def test_scripts_run_in_the_draft_with_the_helpers_importable(paper, tmp_path):
    draft = tmp_path / "draft"
    (draft / "workflow" / "figures").mkdir(parents=True)
    (draft / "plots").mkdir()
    (draft / "workflow" / "figures" / "paperfig.py").write_text("NAME = 'from-draft.png'\n")
    (draft / "plots" / "plot_x.py").write_text(
        "# apply_publication_style(\nfrom pathlib import Path\nimport os, paperfig\n"
        "assert os.environ['MPLBACKEND'] == 'Agg'\nPath(paperfig.NAME).write_text('x')\n")
    (paper / "workflow" / "rules.json").write_text(json.dumps({"figure_python": sys.executable,
                                                               "figure_scripts": ["plots/plot_*.py"]}))
    out = run(paper, draft_dir=str(draft), out_dir=str(tmp_path / "reports"))
    assert out["status"] == "PASS" and out["figures"] == ["plots/from-draft.png"]
    assert out["report"].startswith(str(tmp_path / "reports")) and not (paper / "plots").exists()


def test_no_script_or_no_matplotlib_is_a_failed_check(paper, monkeypatch):
    out = run(paper)
    assert out["status"] == "FAIL" and "no plotting script matches" in out["problems"][0]
    script(paper, "plot_main.py", GOOD)
    monkeypatch.setattr(wf, "can_plot", lambda program: False)
    out = run(paper)
    assert out["status"] == "FAIL" and "figure_python" in out["problems"][0]
    assert wf.interpreter("") == "" and wf.interpreter("no-such-python") == ""
    assert not (paper / "workflow" / "figures" / "fig1.pdf").exists()        # nothing was run
    assert "没有重画" in open(out["report"]).read()
    with pytest.raises(ValueError):
        wf.paper_figures(str(paper / "missing"))


def test_without_figure_python_the_first_interpreter_with_matplotlib_draws(monkeypatch, tmp_path):
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "python3").write_text("")
    monkeypatch.setenv("PATH", f"{tmp_path / 'a'}:{tmp_path / 'b'}")
    monkeypatch.setattr(wf, "can_plot", lambda program: program == str(tmp_path / "b" / "python3"))
    assert wf.interpreter("") == str(tmp_path / "b" / "python3")


def test_the_editor_gets_the_skill_rules_as_text():
    rules = wf.figure_rules()
    assert set(rules) == {"api.md", "common-patterns.md", "design-theory.md"}
    assert "PALETTE" in rules["api.md"] and (wf.SKILL / "LICENSE").exists() and (wf.SKILL / "NOTICE.md").exists()
