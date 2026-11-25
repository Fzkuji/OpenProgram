"""lean_prove with Lean replaced by a small fake (it reads the scratch file and answers
like `lake env lean`) and the prover scripted; one test runs real Lean when lake works."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.outreach import lean as ln
from auto_research.tasks.outreach import prove as pv

TEX = r"""\documentclass{article}
\newtheorem{thm}{Theorem}
\begin{document}
\begin{thm}\label{thm:easy}Easy.\end{thm}
\begin{thm}\label{thm:hard}Hard.\end{thm}
\end{document}
"""

BASIC = """namespace Probe

-- paper: thm:easy
theorem easy (n : Nat) : n + 0 = n := by
  sorry

/-- A helper. -/
def double (n : Nat) : Nat := n + n

-- paper: thm:hard
theorem hard (n : Nat) :
    double n = 2 * n := by
  intro
  sorry

-- a note about the next one
theorem done_already : True := trivial

end Probe
"""

# What the fake Lean accepts as a proof of each declaration (a substring of its body).
SOLVES = {"easy": "omega", "hard": "unfold double"}


def fake_lean(axioms=None, broken=()):
    """run_lean stand-in: per declaration, sorry -> warning, a body without the accepted
    tactic -> `unsolved goals`; `#print axioms` lines answered without a header."""
    axioms = axioms or {}

    def run(lean_dir, path):
        text = Path(path).read_text()
        out, code = [], 0
        matches = list(ln.DECL.finditer(text))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block = text[m.start():end]
            line = text.count("\n", 0, m.start()) + 1
            body = block.split(":=", 1)[1] if ":=" in block else ""
            name = m.group(2)
            if re.search(r"\bsorry\b", pv.strip_comments(body)):
                out.append(f"{path}:{line}:8: warning: declaration uses `sorry`")
            elif name in SOLVES and SOLVES[name] not in body:
                out.append(f"{path}:{line + 1}:2: error: unsolved goals\nn : Nat\n⊢ goal of {name}")
                code = 1
            if name in broken:
                out.append(f"{path}:{line}:0: error: broken on purpose")
                code = 1
        for name in re.findall(r"^#print axioms (\S+)$", text, re.MULTILINE):
            short = name.split(".")[-1]
            listed = axioms.get(short, "propext")
            out.append(f"'{name}' depends on axioms: [{listed}]" if listed else f"'{name}' does not depend on any axioms")
        return code, "\n".join(out) + "\n"
    return run


@pytest.fixture
def paper(tmp_path, monkeypatch):
    repo = tmp_path / "paper"
    (repo / "workflow" / "lean" / "Probe").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "main.tex").write_text(TEX)
    (repo / "workflow" / "lean" / "lakefile.toml").write_text('name = "probe"\n')
    (repo / "workflow" / "lean" / "Probe" / "Basic.lean").write_text(BASIC)
    monkeypatch.setattr(ln, "which", lambda t: f"/usr/bin/{t}")
    monkeypatch.setattr(pv, "build", lambda lean_dir: (0, "Build completed successfully.\n"))
    monkeypatch.setattr(pv, "run_lean", fake_lean())
    return repo


def basic(paper):
    return (paper / "workflow" / "lean" / "Probe" / "Basic.lean").read_text()


def test_automation_then_prover_with_lean_errors(paper):
    replies = [{"kind": "tactic", "proof": "simp", "idea": "simp"},
               {"kind": "tactic", "proof": "by\n  unfold double\n  omega", "idea": "unfold"}]
    with script_roles({pv.prover: replies}) as calls:
        out = json.loads(pv.lean_prove(str(paper)))
    assert out["status"] == "PASS" and out["proved"] == 2 and out["remaining"] == 0
    record = json.loads(Path(out["json"]).read_text())
    rows = {r["name"]: r for r in record["theorems"]}
    assert rows["easy"]["by"] == "automation: omega" and rows["easy"]["attempts"] == 0
    assert rows["hard"]["by"] == "prover, attempt 2" and rows["hard"]["attempts"] == 2
    reqs = calls[pv.prover]
    assert len(reqs) == 2                                   # easy never reached the prover
    assert "goal of hard" in reqs[0].text                   # the goal from the automation pass
    assert "double n = 2 * n" in reqs[0].text and "def double" in reqs[0].text
    assert "unsolved goals" in reqs[1].text and "simp" in reqs[1].text   # attempt 1 and Lean's error
    text = basic(paper)
    assert "theorem easy (n : Nat) : n + 0 = n := by\n  omega\n\n/-- A helper. -/" in text
    assert "theorem hard (n : Nat) :\n    double n = 2 * n := by\n  unfold double\n  omega\n\n-- a note" in text
    assert "sorry" not in text and text.endswith("end Probe\n")
    bak = Path(out["report"]).parent / "backup" / "Probe" / "Basic.lean.bak"
    assert bak.read_text() == BASIC
    report = Path(out["report"]).read_text()
    assert "已证明 2 个" in report and "unfold double" in report


def test_failed_attempts_and_rejected_proofs_leave_the_file(paper, monkeypatch):
    monkeypatch.setattr(pv, "run_lean", fake_lean(axioms={"easy": "propext, cheat"}))
    replies = [{"kind": "tactic", "proof": "sorry", "idea": ""},
               {"kind": "tactic", "proof": "exact?", "idea": ""},
               {"kind": "term", "proof": "trivial\naxiom cheat : False", "idea": ""},
               {"kind": "tactic", "proof": "simp", "idea": ""}]
    with script_roles({pv.prover: replies}) as calls:
        out = json.loads(pv.lean_prove(str(paper), theorem="thm:hard", attempts="4"))
    assert out["status"] == "PASS_WITH_NOTES" and out["proved"] == 0 and out["written"] == []
    record = json.loads(Path(out["json"]).read_text())
    assert [r["name"] for r in record["theorems"]] == ["hard"]
    row = record["theorems"][0]
    assert row["attempts"] == 4 and "unsolved goals" in row["error"]
    later = calls[pv.prover][3].text
    assert "the proof uses sorry" in later and "search tactic exact?" in later and "declares something" in later
    assert basic(paper) == BASIC
    assert "最后的错误" in Path(out["report"]).read_text()


def test_non_standard_axioms_are_refused(paper, monkeypatch):
    monkeypatch.setattr(pv, "run_lean", fake_lean(axioms={"easy": "propext, Lean.ofReduceBool"}))
    with script_roles({pv.prover: [{"kind": "tactic", "proof": "omega", "idea": ""}]}):
        out = json.loads(pv.lean_prove(str(paper), theorem="easy", attempts="1"))
    row = json.loads(Path(out["json"]).read_text())["theorems"][0]
    assert not row["proved"] and "non-standard axioms: Lean.ofReduceBool" in row["error"]
    assert basic(paper) == BASIC


def test_a_new_error_after_writing_restores_the_files(paper, monkeypatch):
    builds = iter([(0, ""), (1, "error: Probe/Basic.lean:20:2: unknown constant\n")])
    monkeypatch.setattr(pv, "build", lambda lean_dir: next(builds))
    out = json.loads(pv.lean_prove(str(paper), theorem="easy"))
    assert out["status"] == "FAIL" and out["proved"] == 0 and out["written"] == []
    record = json.loads(Path(out["json"]).read_text())
    assert record["restored"] and any("unknown constant" in n for n in record["notes"])
    assert basic(paper) == BASIC


def test_existing_errors_elsewhere_do_not_block(paper, monkeypatch):
    monkeypatch.setattr(pv, "run_lean", fake_lean(broken=("done_already",)))
    out = json.loads(pv.lean_prove(str(paper), theorem="easy"))
    assert out["proved"] == 1 and "  omega" in basic(paper)


def test_nothing_to_do(paper, monkeypatch):
    with pytest.raises(ValueError, match="declarations: easy, hard, done_already"):
        pv.lean_prove(str(paper), theorem="nope")
    out = json.loads(pv.lean_prove(str(paper), theorem="done_already"))
    assert out["status"] == "PASS_WITH_NOTES" and out["proved"] == 0
    monkeypatch.setattr(ln, "which", lambda t: None)
    out = json.loads(pv.lean_prove(str(paper)))
    assert out["status"] == "PASS_WITH_NOTES"
    assert "elan" in json.loads(Path(out["json"]).read_text())["notes"][0]


def test_locate_splice_and_reply_helpers():
    spot = pv.locate(BASIC, "hard")
    assert spot["namespace"] == "Probe" and spot["labels"] == ["thm:hard"] and spot["line"] == 11
    assert spot["statement"] == "theorem hard (n : Nat) : double n = 2 * n"
    assert spot["code"].endswith("intro\n  sorry")
    assert pv.top_level_assign("theorem t (h : let x := 1; x = 1) : True := trivial", 9) == 41
    new, shown = pv.splice(BASIC, "easy", "term", "Nat.add_zero n")
    assert ":=\n  Nat.add_zero n\n\n/-- A helper." in new and shown == "  Nat.add_zero n"
    assert pv.check_reply({"kind": "term", "proof": "```lean\nby simp\n```"})["kind"] == "tactic"
    assert pv.format_block("induction n with\n  | zero => rfl\n  | succ k ih => omega") == \
        "induction n with\n  | zero => rfl\n  | succ k ih => omega"
    assert pv.check_reply({"kind": "tactic", "proof": "by simp\n  omega"})["proof"] == "  simp\n  omega"
    assert pv.check_reply({"kind": "tactic", "proof": "  intro n\n  omega"})["proof"] == "  intro n\n  omega"
    assert pv.parse_attempts("x") == 4 and pv.parse_attempts("99") == 10
    log = "a.lean:3:2: error: unsolved goals\n⊢ x\n'Probe.t'' depends on axioms: [propext,\n Quot.sound]\n"
    assert pv.messages_in(log)[0]["message"] == "unsolved goals\n⊢ x"
    assert pv.axioms_of(log, "Probe.t'") == ["propext", "Quot.sound"]


def lake_works() -> bool:
    if not shutil.which("lake"):
        return False
    try:
        return subprocess.run(["lake", "--version"], capture_output=True, timeout=60).returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not lake_works(), reason="needs a working lake")
def test_real_lean(tmp_path, monkeypatch):
    repo = tmp_path / "paper"
    lean_dir = repo / "workflow" / "lean"
    (lean_dir / "Probe").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "main.tex").write_text(TEX)
    (lean_dir / "lakefile.toml").write_text('name = "probe"\ndefaultTargets = ["Probe"]\n\n[[lean_lib]]\nname = "Probe"\n')
    (lean_dir / "Probe.lean").write_text("import Probe.Basic\n")
    (lean_dir / "Probe" / "Basic.lean").write_text(BASIC)
    with script_roles({pv.prover: [{"kind": "tactic", "proof": "unfold double\nomega", "idea": ""}]}):
        out = json.loads(pv.lean_prove(str(repo), attempts="1"))
    assert out["status"] == "PASS", Path(out["report"]).read_text()
    assert "sorry" not in (lean_dir / "Probe" / "Basic.lean").read_text()
