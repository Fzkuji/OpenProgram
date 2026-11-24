"""The paper_review entry with a scripted reviewer: what it reads, the round it writes, and its checks."""

import json
import subprocess

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.review import workflow as wf

MAIN = r"""\documentclass{article}
\begin{document}
\section{Introduction}
We show that workflows keep changing with feedback. % a comment the reviewer never sees
\input{method}
\bibliography{references}
\end{document}
"""
METHOD = r"""\section{Method}
We count changes over 725 iterations.
"""
VENUE = "# Venue\n\n## Venue\nNeurIPS 2026\n\n## Requirements\nNine pages of body text.\n"


def review(score=8, weaknesses=(), previous=None):
    reply = {"summary": "论文研究工作流的演化。", "strengths": ["数据规模大"], "weaknesses": list(weaknesses),
             "questions": [], "score": score, "confidence": 4, "verdict": "可以接收"}
    if previous is not None:
        reply["previous"] = previous
    return reply


MAJOR = {"severity": "major", "where": "Method", "issue": "没有和最强基线比较", "fix": "补一组对比"}


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "0main.tex").write_text(MAIN)
    (repo / "method.tex").write_text(METHOD)
    (repo / "workflow" / "venue.md").write_text(VENUE)
    return repo


def run(paper, **kw):
    return json.loads(wf.paper_review(str(paper), **kw))


def test_reviewer_reads_the_flattened_paper_and_the_venue(paper):
    with script_roles({wf.reviewer: [review(weaknesses=[MAJOR])]}) as calls:
        out = run(paper)
    request = calls[wf.reviewer][0]
    assert "7 is a clear accept" in request.text                       # docstring
    assert "We count changes over 725 iterations" in request.text      # \input is inlined
    assert "a comment the reviewer never sees" not in request.text
    assert "NeurIPS 2026" in request.text and "Nine pages" in request.text
    assert out["status"] == "PASS_WITH_NOTES" and out["round"] == 1 and out["weaknesses"]["major"] == 1
    assert out["report"].startswith(str(paper / "drafts" / "paper_review"))
    text = open(out["report"], encoding="utf-8").read()
    assert "W1　主要　Method" in text and "8/10" in text
    record = json.load(open(out["run"], encoding="utf-8"))
    assert [s["stage"] for s in record["stages"]] == ["read", "review", "report"]
    assert record["roles"]["reviewer"]["model"] == "caller's model"
    assert record["stages"][1]["schema"] == "paper_review"
    assert (paper / "0main.tex").read_text() == MAIN                    # never edited


def test_the_next_round_answers_every_earlier_weakness(paper):
    with script_roles({wf.reviewer: [review(score=5, weaknesses=[MAJOR, dict(MAJOR, severity="fatal")])]}):
        first = run(paper)
    assert first["status"] == "FAIL"
    answer = [{"id": "W1", "status": "resolved", "note": "补了对比"}, {"id": "W2", "status": "open", "note": ""}]
    with script_roles({wf.reviewer: [review(previous=answer)]}) as calls:
        second = run(paper)
    assert "没有和最强基线比较" in calls[wf.reviewer][0].text          # the earlier weaknesses are shown
    assert second["round"] == 2 and second["status"] == "PASS_WITH_NOTES"     # W2 is still open
    text = open(second["report"], encoding="utf-8").read()
    assert "上一轮的问题" in text and "已解决" in text and "未解决" in text
    with pytest.raises(ValueError, match="every previous weakness"):
        wf.check_review(review(previous=answer[:1]), ["W1", "W2"])


def test_a_clean_review_passes_and_the_reviewer_model_is_recorded(paper, tmp_path):
    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / "0main.tex").write_text(MAIN.replace(r"\input{method}", "Revised text."))
    with script_roles({wf.reviewer: [review(score=9)]}) as calls:
        out = run(paper, draft_dir=str(draft), reviewer_model="openai:gpt-5", venue="ICLR 2027")
    request = calls[wf.reviewer][0]
    assert "Revised text." in request.text and "ICLR 2027" in request.text and "NeurIPS" not in request.text
    assert out["status"] == "PASS"
    record = json.load(open(out["run"], encoding="utf-8"))
    assert record["roles"]["reviewer"]["model"] == "openai:gpt-5"


def test_bad_replies_and_missing_papers_are_rejected(paper, tmp_path):
    with pytest.raises(ValueError):
        wf.check_review(review(score=11), [])
    with pytest.raises(ValueError):
        wf.check_review(review(weaknesses=[dict(MAJOR, severity="cosmetic")]), [])
    (tmp_path / "plain").mkdir()
    with pytest.raises(ValueError, match="not a Git repository"):
        run(tmp_path / "plain")
    (paper / "0main.tex").unlink()
    with pytest.raises(ValueError, match="does not exist"):
        run(paper)
