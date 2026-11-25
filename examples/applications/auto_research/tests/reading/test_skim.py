"""skim_test with the skimmer and the judge scripted."""

import json

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.reading import skim

MAIN = r"""\documentclass{article}
\title{Cached Subgoals for \textbf{Faster} Agents}
\begin{document}
\maketitle
\begin{abstract}
We present an agent that plans with cached subgoals.
\end{abstract}
\section{Introduction}
Planning agents spend most of their time re-deriving subgoals they have seen before.
Caching subgoals across episodes halves the planning time of web agents without lowering success \cite{x}.
We test this on two benchmarks.

Our second paragraph starts here with a plain sentence about the setting. It continues with details.

\section{Method}
\begin{figure}[t]
\includegraphics{fig.pdf}
\caption{The subgoal cache, keyed by page state.}
\label{fig:cache}
\end{figure}
The cache stores each subgoal with the page state it was derived from. Lookups are exact.
\bibliography{references}
\end{document}
"""
PROJECT = "# Project\nKind: method\nClaim: Caching subgoals across episodes halves the planning time of web agents.\n"
CLAIMS = "# Claims\nApproved: yes\n\nC1: The cache keeps the success rate of web agents.\nRQ: RQ1\n"
UNDERSTANDING = {"goal": "让智能体规划更快。", "method": "缓存子目标。", "main_result": "不清楚", "main_claim": "缓存能加速。"}


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    (repo / "workflow" / "rules.json").write_text(json.dumps({"main_tex": "main.tex", "bib_files": []}))
    (repo / "main.tex").write_text(MAIN)
    (repo / "workflow" / "project.md").write_text(PROJECT)
    (repo / "workflow" / "claims.md").write_text(CLAIMS)
    return repo


def verdicts(main, c1):
    return {"claims": [{"id": "main", "verdict": main, "reason": "理由。"}, {"id": "C1", "verdict": c1, "reason": "理由。"}]}


def test_skimmer_sees_only_the_skim_and_missed_claims_are_located(paper):
    with script_roles({skim.skimmer: [UNDERSTANDING], skim.judge: [verdicts("missing", "partly")]}) as calls:
        out = json.loads(skim.skim_test(str(paper)))
    seen = calls[skim.skimmer][0].text
    assert "Cached Subgoals for Faster Agents" in seen and "plans with cached subgoals" in seen
    assert "Figure 1: The subgoal cache, keyed by page state." in seen
    assert "[Introduction] Planning agents spend most of their time" in seen
    assert "[Introduction] Our second paragraph starts here" in seen
    assert "[Method] The cache stores each subgoal" in seen
    assert "halves the planning time" not in seen and "Lookups are exact" not in seen     # mid-paragraph
    assert "It continues with details" not in seen
    judged = calls[skim.judge][0].text
    assert "main: Caching subgoals across episodes" in judged and "C1: The cache keeps" in judged
    assert "缓存能加速" in judged and "Answer every claim exactly once" in judged
    assert out["status"] == "FAIL" and out["missed"] == ["main", "C1"]
    record = json.load(open(out["json"], encoding="utf-8"))
    main = record["claims"][0]
    assert main["main"] and main["carriers"][0]["section"] == "Introduction"
    assert main["carriers"][0]["sentence"].startswith("Caching subgoals across episodes halves")
    assert main["carriers"][0]["skimmed"] is False
    report = open(out["report"], encoding="utf-8").read()
    assert "藏在段中" in report and "主要主张" in report
    trail = json.load(open(out["run"], encoding="utf-8"))
    assert [s["stage"] for s in trail["stages"]] == ["skim", "read", "judge", "locate", "report"]


def test_statuses(paper):
    with script_roles({skim.skimmer: [UNDERSTANDING], skim.judge: [verdicts("covered", "covered")]}):
        assert json.loads(skim.skim_test(str(paper)))["status"] == "PASS"
    with script_roles({skim.skimmer: [UNDERSTANDING], skim.judge: [verdicts("partly", "missing")]}):
        assert json.loads(skim.skim_test(str(paper)))["status"] == "PASS_WITH_NOTES"
    (paper / "workflow" / "project.md").write_text("# Project\nKind: method\nClaim:\n")
    with script_roles({skim.skimmer: [UNDERSTANDING], skim.judge: [{"claims": [
            {"id": "C1", "verdict": "missing", "reason": "没有。"}]}]}):
        assert json.loads(skim.skim_test(str(paper)))["status"] == "FAIL"      # C1 is the main claim now


def test_bad_replies_and_no_claims(paper):
    with pytest.raises(ValueError, match="unanswered"):
        skim.check_coverage({"claims": [{"id": "main", "verdict": "covered", "reason": "x"}]}, ["main", "C1"])
    with pytest.raises(ValueError, match="unknown verdict"):
        skim.check_coverage({"claims": [{"id": "main", "verdict": "maybe", "reason": "x"}]}, ["main"])
    with pytest.raises(ValueError, match="goal"):
        skim.check_understanding({"goal": "", "method": "m", "main_result": "r", "main_claim": "c"})
    (paper / "workflow" / "project.md").unlink()
    (paper / "workflow" / "claims.md").unlink()
    with pytest.raises(ValueError, match="no claim to test"):
        skim.skim_test(str(paper))
