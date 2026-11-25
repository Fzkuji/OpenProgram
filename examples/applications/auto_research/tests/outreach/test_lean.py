"""lean_check with `lake build` output faked through a monkeypatched subprocess, and
lean_draft with the formalizer scripted."""

import json
import subprocess
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.outreach import lean as ln

TEX = r"""\documentclass{article}
\newtheorem{thm}{Theorem}
\newtheorem{lem}{Lemma}
\begin{document}
\begin{definition}[Sparse]\label{def:sparse}
A vector is $k$-sparse when at most $k$ entries are nonzero.
\end{definition}
\begin{thm}[Main]\label{thm:main}
Every $k$-sparse vector in $\mathbb{R}^n$ has at most $k$ nonzero entries.
\end{thm}
\begin{lem}\label{lem:helper}
For all naturals $a$, $a + 0 = a$.
\end{lem}
\begin{proposition}\label{prop:broken}
Something that does not compile.
\end{proposition}
\begin{corollary}\label{cor:free}
Not formalized yet.
\end{corollary}
\begin{lemma}
An unlabeled lemma.
\end{lemma}
\end{document}
"""

MAIN_LEAN = """import Mathlib

-- paper: thm:main
theorem main_bound (n k : ℕ) (v : Fin n → ℝ) : True := by
  sorry

/-- The helper. -/
-- paper: lem:helper
theorem helper (a : ℕ) : a + 0 = a := by
  simp

-- paper: prop:broken, lem:ghost
theorem broken : 1 = 2 := by
  exact foo
"""

LOG = """⚠ [3/5] Built Paper.Main
warning: ././././Paper/Main.lean:4:8: declaration uses `sorry`
✖ [4/5] Building Paper.Main
error: ././././Paper/Main.lean:14:8: unknown identifier 'foo'
error: Lean exited with code 1
Some required builds logged failures:
- Paper.Main
"""


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow" / "lean" / "Paper").mkdir(parents=True)
    (repo / "workflow" / "lean" / ".lake" / "packages").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "main.tex").write_text(TEX)
    (repo / "workflow" / "lean" / "Paper" / "Main.lean").write_text(MAIN_LEAN)
    (repo / "workflow" / "lean" / ".lake" / "packages" / "Skip.lean").write_text("theorem skip : True := trivial\n")
    return repo


def fake_lake(monkeypatch, code=1, log=LOG):
    calls = []
    real = subprocess.run
    monkeypatch.setattr(ln, "which", lambda t: f"/usr/bin/{t}")

    def run(cmd, *args, **kw):
        if cmd[0] != "lake":                       # git and the rest run for real
            return real(cmd, *args, **kw)
        calls.append((cmd, kw.get("cwd"), kw.get("timeout")))
        return subprocess.CompletedProcess(cmd, code, stdout=log, stderr="")

    monkeypatch.setattr(ln.subprocess, "run", run)
    return calls


def test_build_output_maps_to_theorems(paper, monkeypatch):
    calls = fake_lake(monkeypatch)
    out = json.loads(ln.lean_check(str(paper)))
    assert calls == [(["lake", "build"], paper / "workflow" / "lean", ln.BUILD_TIMEOUT)]
    record = json.loads(Path(out["json"]).read_text())
    by = {t["label"]: t["status"] for t in record["theorems"]}
    assert by == {"thm:main": "sorry", "lem:helper": "proved", "prop:broken": "fails", "cor:free": "not_formalized"}
    assert out["status"] == "FAIL" and out["built"] is True
    assert out["counts"] == {"proved": 1, "sorry": 1, "fails": 1, "not_formalized": 1, "unchecked": 0}
    broken = next(t for t in record["theorems"] if t["label"] == "prop:broken")
    assert broken["messages"] == ["Paper/Main.lean:14: unknown identifier 'foo'"]
    assert "`-- paper: lem:ghost` names a label the paper does not have" in record["notes"]
    assert "1 theorem environment(s) have no \\label and cannot be mapped" in record["notes"]
    assert [d["name"] for d in record["declarations"]] == ["main_bound", "helper", "broken"]   # .lake skipped
    assert (Path(out["report"]).parent / "build.log").read_text() == LOG
    assert "编译失败" in Path(out["report"]).read_text()


def test_clean_build_with_everything_proved(paper, monkeypatch):
    lean = paper / "workflow" / "lean" / "Paper" / "Main.lean"
    lean.write_text("-- paper: thm:main, lem:helper, prop:broken, cor:free\ntheorem all : True := trivial\n")
    fake_lake(monkeypatch, code=0, log="✔ [5/5] Built Paper\nBuild completed successfully.\n")
    out = json.loads(ln.lean_check(str(paper)))
    assert out["counts"]["proved"] == 4
    assert out["status"] == "PASS_WITH_NOTES"            # only the unlabeled lemma remains a note


def test_failed_build_without_lean_errors(paper, monkeypatch):
    fake_lake(monkeypatch, code=1, log="error: could not resolve dependency mathlib\n")
    out = json.loads(ln.lean_check(str(paper)))
    record = json.loads(Path(out["json"]).read_text())
    assert out["status"] == "FAIL" and any("without a Lean error" in e for e in record["errors"])
    assert {t["status"] for t in record["theorems"] if t["declarations"]} == {"fails"}


def test_without_lake_reads_the_source(paper, monkeypatch):
    monkeypatch.setattr(ln, "which", lambda t: None)
    out = json.loads(ln.lean_check(str(paper)))
    record = json.loads(Path(out["json"]).read_text())
    assert out["status"] == "PASS_WITH_NOTES" and out["built"] is False
    assert any("elan" in n for n in record["notes"])
    by = {t["label"]: t["status"] for t in record["theorems"]}
    assert by["thm:main"] == "sorry" and by["lem:helper"] == "unchecked"


def test_no_lean_folder(paper, monkeypatch):
    import shutil
    shutil.rmtree(paper / "workflow" / "lean")
    out = json.loads(ln.lean_check(str(paper)))
    assert out["status"] == "PASS_WITH_NOTES" and out["counts"]["not_formalized"] == 4


def test_parse_messages_formats():
    msgs = ln.parse_messages("A.lean:3:2: error: bad\nwarning: ./B/C.lean:10:0: declaration uses 'sorry'\n")
    assert msgs == [{"file": "A.lean", "line": 3, "col": 2, "level": "error", "message": "bad"},
                    {"file": "B/C.lean", "line": 10, "col": 0, "level": "warning", "message": "declaration uses 'sorry'"}]


REPLY = {"name": "thmMain", "imports": ["Mathlib"], "assumptions": ["Sparse vectors are over ℝ."],
         "code": "/-- k-sparse vectors. -/\ndef Sparse (n k : ℕ) (v : Fin n → ℝ) : Prop :=\n"
                 "  (Finset.univ.filter (fun i => v i ≠ 0)).card ≤ k\n\n"
                 "-- paper: wrong\n/-- The main theorem. -/\ntheorem thmMain (n k : ℕ) (v : Fin n → ℝ) (h : Sparse n k v) :\n"
                 "    (Finset.univ.filter (fun i => v i ≠ 0)).card ≤ k := by\n  sorry"}


def test_draft_writes_the_statement_and_the_lake_project(paper, monkeypatch):
    import shutil
    shutil.rmtree(paper / "workflow" / "lean")
    monkeypatch.setattr(ln, "which", lambda t: None)
    with script_roles({ln.formalizer: [REPLY]}) as calls:
        out = json.loads(ln.lean_draft(str(paper), "main"))
    req = calls[ln.formalizer][0]
    assert "Keep the paper's hypotheses and conclusion exactly" in req.text
    assert "at most $k$ entries are nonzero" in req.text and "thmMain" in req.text
    lean = paper / "workflow" / "lean"
    assert out["file"] == str(lean / "ThmMain.lean") and out["lakefile_created"] is True
    code = (lean / "ThmMain.lean").read_text()
    assert code.startswith("import Mathlib\n")
    assert "-- paper: thm:main\n/-- The main theorem. -/\ntheorem thmMain" in code and "paper: wrong" not in code
    toml = (lean / "lakefile.toml").read_text()
    assert 'roots = ["ThmMain"]' in toml and 'name = "mathlib"' in toml
    assert (lean / ".gitignore").read_text() == ".lake/\n"
    assert out["status"] == "PASS_WITH_NOTES"
    # The drafted file maps back: lean_check (without lake) sees thm:main formalized with sorry.
    record = json.loads(Path(json.loads(ln.lean_check(str(paper)))["json"]).read_text())
    assert {t["label"]: t["status"] for t in record["theorems"]}["thm:main"] == "sorry"
    # A second draft of another theorem adds its module; an existing file is not overwritten.
    with script_roles({ln.formalizer: [dict(REPLY, name="lemHelper", code="theorem lemHelper (a : ℕ) : a + 0 = a := sorry")]}):
        ln.lean_draft(str(paper), "lem:helper")
    assert 'roots = ["ThmMain", "LemHelper"]' in (lean / "lakefile.toml").read_text()
    with script_roles({ln.formalizer: [REPLY]}):
        again = json.loads(ln.lean_draft(str(paper), "thm:main"))
    assert again["file"] == "" and Path(again["draft"]).is_file()


def test_draft_rejects_proofs_axioms_and_unknown_labels(paper):
    with pytest.raises(ValueError, match="not proved by sorry"):
        ln.check_statement({"name": "t", "imports": [], "code": "theorem t : True := trivial", "assumptions": []}, "t")
    with pytest.raises(ValueError, match="axiom"):
        ln.check_statement({"name": "t", "imports": [], "assumptions": [],
                            "code": "axiom cheat : False\ntheorem t : True := sorry"}, "t")
    with pytest.raises(ValueError, match="labels: thm:main"):
        ln.lean_draft(str(paper), "thm:nope")
    assert ln.camel("thm:main-bound") == "ThmMainBound" and ln.camel("1x") == "S1x"


def test_a_project_without_mathlib_gets_a_core_lean_statement(tmp_path):
    lean_dir = tmp_path / "lean"
    assert ln.has_mathlib(lean_dir)                                   # a project this tool creates requires it
    lean_dir.mkdir()
    (lean_dir / "lakefile.toml").write_text('name = "probe"\n')
    assert not ln.has_mathlib(lean_dir)
    reply = {"name": "thmSumMathlib", "imports": ["Mathlib", "Std"], "assumptions": [],
             "code": "theorem thmSumMathlib (n : Nat) : n = n := by\n  sorry"}
    got = ln.check_statement(reply, "thmSum", mathlib=False)
    assert got["imports"] == ["Std"] and got["name"] == "thmSum" and "theorem thmSum (n" in got["code"]
    assert not ln.assemble(got, "thm:sum").startswith("import Mathlib")
