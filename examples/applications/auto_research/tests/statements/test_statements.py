"""paper_statements with the drafter scripted, on a small ICML paper repository."""

import json
import subprocess
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.statements import gather as ga
from auto_research.tasks.statements import workflow as wf

TEX = r"""\documentclass{article}
\usepackage{icml2025}
\title{Sparse Memory for Agents}
\author{Alice Zhang \and Bob Li}
\begin{document}
\maketitle
\section{Method}
We prune memory by usage.
\section*{Acknowledgments}
This work was supported by the National Science Foundation under Grant No. IIS-2112633.
\end{document}
"""
VENUE = """# Venue
Name: ICML 2025
Competing interests: The authors declare no competing interests.

## Requirements
- Back matter: author contributions (CRediT), competing interests and a funding statement.
"""
CODE = "https://github.com/alice/sparse-memory"


def commit(repo, who, files, message, trailer=""):
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", *files], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", f"user.name={who}", "-c", f"user.email={who.split()[0].lower()}@x.org",
                    "commit", "-q", "-m", message + trailer], check=True)


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    commit(repo, "Alice Zhang", {"train.py": "print(1)\n", "LICENSE": "MIT License\n\nPermission is hereby granted, "
                                 "free of charge, to any person\n"}, "code")
    commit(repo, "Alice Zhang", {"workflow/results.tsv": "commit\tmetric\n"}, "results")
    commit(repo, "Bob Li", {"main.tex": TEX, "workflow/venue.md": VENUE,
                            "workflow/rules.json": json.dumps({"main_tex": "main.tex", "code": {"repo": CODE}})},
           "paper")
    commit(repo, "Bob Li", {"data/items.jsonl": "{}\n"}, "data",
           "\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    return repo


GOOD = {
    "availability": rf"The code is available at \url{{{CODE}}} under the MIT License, and the data in data/.",
    "interests": "The authors declare no competing interests.",
    "funding": "This work was supported by the National Science Foundation under Grant No. IIS-2112633.",
    "ethics": "This work makes agent memory cheaper; we see no new risk beyond those of agents. "
              "[TODO: state whether the data involved people]",
}
CREDIT = {"contributions": [{"author": "Alice Zhang", "roles": ["Software", "Investigation", "Conceptualization"]},
                            {"author": "Bob Li", "roles": ["Writing – original draft", "Data curation"]}]}


def run(paper, replies, **kw):
    with script_roles({wf.drafter: replies}) as calls:
        out = json.loads(wf.paper_statements(str(paper), **kw))
    return out, calls


def test_facts_from_the_repository(paper):
    facts, notes = ga.gather(paper)
    assert notes == [] and facts["venue"] == "ICML" and facts["title"] == "Sparse Memory for Agents"
    assert set(facts["needs"]) == {"credit", "interests", "funding", "ethics"}
    assert facts["needs"]["ethics"]["heading"] == "Impact Statement"
    assert [c["url"] for c in facts["code"]] == [CODE]
    assert [x["id"] for x in facts["licenses"]] == ["MIT"]
    assert [g["id"] for g in facts["grants"]] == ["IIS-2112633"]
    assert [a["name"] for a in facts["authors"]] == ["Alice Zhang", "Bob Li"]
    hints = {h["author"]: h for h in facts["credit_hints"]}
    assert hints["Alice Zhang"]["areas"] == {"code": 1, "experiments": 1}
    assert hints["Alice Zhang"]["suggested_roles"] == ["Software", "Investigation", "Formal analysis"]
    assert hints["Bob Li"]["areas"] == {"paper": 1, "data": 1}
    assert facts["interests_declared"][0]["value"].startswith("The authors declare")
    assert "data/" in facts["data_files"]


def test_every_statement_drafted_checked_and_redrafted(paper):
    invented = {"latex": r"Code: \url{https://github.com/other/x} under the Apache License 2.0."}
    out, calls = run(paper, [invented, {"latex": GOOD["availability"]}, CREDIT, {"latex": GOOD["interests"]},
                             {"latex": GOOD["funding"]}, {"latex": GOOD["ethics"]}])
    assert out["kinds"] == ["availability", "credit", "interests", "funding", "ethics"]
    assert out["redrafts"] == 1 and out["fallbacks"] == [] and out["status"] == "PASS_WITH_NOTES"
    redraft = calls[wf.drafter][1].text
    assert "URL not in the facts: https://github.com/other/x" in redraft and "license not in the facts" in redraft
    tex = Path(out["statements"]).read_text()
    assert "\\section*{Impact Statement}" in tex and "\\section*{Author Contributions}" in tex
    assert "\\textbf{Alice Zhang}: Conceptualization, Investigation, Software [TODO: confirm Conceptualization" in tex
    assert "\\textbf{Bob Li}: Data curation, Writing -- original draft." in tex
    assert out["placeholders"] == 2
    report = Path(out["report"]).read_text()
    assert "被退回重写 1 次" in report and "IIS-2112633" in report and "运行 ai_disclosure" in report
    trail = json.loads(Path(out["run"]).read_text())
    assert [s["stage"] for s in trail["stages"]][:4] == ["gather", "draft_availability", "check_availability",
                                                         "draft_availability"]


def test_pass_without_placeholders(paper):
    out, _ = run(paper, [{"latex": GOOD["availability"]}, {"latex": GOOD["funding"]}], kinds="funding, availability")
    assert out["status"] == "PASS" and out["placeholders"] == 0 and out["kinds"] == ["availability", "funding"]


def test_redrafts_end_in_a_placeholder(paper):
    wrong = {"latex": "Supported by the National Science Foundation under Grant No. IIS-9999999."}
    out, calls = run(paper, [wrong] * 3, kinds="funding")
    assert out["fallbacks"] == ["funding"] and out["status"] == "PASS_WITH_NOTES" and len(calls[wf.drafter]) == 3
    tex = Path(out["statements"]).read_text()
    assert "[TODO: write the funding" in tex and "9999999" not in tex.split("TODO")[0]


def test_checks(paper):
    facts, _ = ga.gather(paper)
    corpus = wf.fact_corpus(facts)
    assert wf.problems(GOOD["funding"], "funding", facts, corpus) == []
    assert wf.problems("Funded by Carol Smith.", "funding", facts, corpus)[0] == "name not in the facts: Carol Smith"
    assert "grant IIS-2112633 is missing" in wf.problems("Funded.", "funding", facts, corpus)
    assert "number not in the facts: 42" in wf.problems("We used 42 GPUs.", "ethics", facts, corpus)
    assert wf.problems("All data are open; see \\url{" + CODE + "}.", "availability", facts, corpus) == []
    assert "code location missing" in wf.problems("Data in data/.", "availability", facts, corpus)[0]
    assert wf.latex_problems("50% of R&D_x {") == ["unbalanced braces", "unescaped LaTeX special characters: % & _"]
    assert "Markdown" in wf.latex_problems("see `data/` here")[0] and wf.latex_problems("``quoted''") == []
    assert wf.unescape(r"\\texttt{a\\_b}") == r"\texttt{a\_b}"
    facts["double_blind"] = True
    assert "double-blind" in wf.problems(GOOD["availability"], "availability", facts, corpus)[0]
    facts["interests_declared"] = []
    assert "write a [TODO" in wf.problems(GOOD["interests"], "interests", facts, corpus)[0]
    assert wf.problems("[TODO: declare interests]", "interests", facts, corpus) == []
    assert "approval" in wf.problems("The study was approved by the IRB.", "ethics", facts, corpus)[0]
    assert ga.spdx("CC BY 4.0 and Apache License, Version 2.0") == ["CC-BY-4.0", "Apache-2.0"]
    assert ga.grant_ids("Grant No. 62076123 and EP/X012345/1 in 2024") == ["62076123", "EP/X012345/1"]
    assert ga.https("git@github.com:alice/x.git") == "https://github.com/alice/x"
    with pytest.raises(ValueError, match="unknown statement kinds"):
        wf.paper_statements(str(paper), kinds="ai")


def test_no_authors_leaves_credit_to_the_author(paper):
    (paper / "main.tex").write_text(TEX.replace(r"\author{Alice Zhang \and Bob Li}", ""))
    out, calls = run(paper, [], kinds="credit")
    assert calls.get(wf.drafter) is None and out["status"] == "PASS_WITH_NOTES"
    tex = Path(out["statements"]).read_text()
    assert "[TODO: list the authors" in tex and "Alice Zhang (code 1, experiments 1)" in tex
