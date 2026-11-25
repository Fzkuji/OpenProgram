"""The paper_review entry with scripted reviewers and area chair: what they read, the round it writes,
its checks, and the acceptance estimate."""

import json
import math
import subprocess
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.review import anchors as openreview
from auto_research.tasks.review import predictor as score_predictor
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


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """No test reaches the network or ~/.cache: OpenReview answers nothing unless a test serves
    it, and the score model has no saved model and collects nothing."""
    calls = []
    monkeypatch.setattr(openreview, "fetch", lambda url, params: calls.append(params))
    monkeypatch.setattr(score_predictor, "CACHE", tmp_path / "model_cache")
    monkeypatch.setattr(score_predictor, "collect", lambda **kw: [])
    return calls


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
    kw.setdefault("reviewers", "1")
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
    assert [s["stage"] for s in record["stages"]] == ["read", "calibrate", "review", "report"]
    assert list(record["roles"]) == ["reviewer"] and record["roles"]["reviewer"]["model"] == "caller's model"
    assert "your reviewer persona" not in request.text and out["reviewers"] == 1
    assert out["acceptance_estimate"]["family"] == "ICLR/NeurIPS/ICML"           # NeurIPS in venue.md
    assert "粗略估计，不是预测" in text
    assert record["stages"][2]["schema"] == "paper_review"
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


def meta(weaknesses=(), previous=None, recommendation="weak_accept", disagreements=(), changes=()):
    reply = {"summary": "三位评审都认可数据规模。", "strengths": ["数据规模大"], "weaknesses": list(weaknesses),
             "disagreements": list(disagreements), "key_changes": list(changes),
             "recommendation": recommendation, "verdict": "补上基线后可以接收"}
    if previous is not None:
        reply["previous"] = previous
    return reply


MINOR = {"severity": "minor", "where": "Introduction", "issue": "动机不够清楚", "fix": "加一段直观解释"}


def test_a_panel_of_three_reviews_with_personas_and_an_area_chair(paper):
    reviews = [review(score=8, weaknesses=[MAJOR]), review(score=6, weaknesses=[MINOR]), review(score=7)]
    consolidated = [dict(MAJOR, raised_by=["R1.W1"]), dict(MINOR, raised_by=["R2.W1"])]
    split = {"point": "实验是否充分", "sides": [{"reviewers": ["R1"], "position": "缺基线"},
                                              {"reviewers": ["R3"], "position": "已经足够"}],
             "weaknesses": ["R1.W1"], "supported": 1, "evidence": "论文只比较了一个基线"}
    changes = [{"change": "补最强基线", "why": "R1 的主要问题", "weaknesses": ["R1.W1"]}]
    with script_roles({wf.reviewer: reviews,
                       wf.area_chair: [meta(consolidated, disagreements=[split], changes=changes)]}) as calls:
        out = json.loads(wf.paper_review(str(paper)))                      # three reviewers by default
    texts = [c.text for c in calls[wf.reviewer]]
    for i, persona in enumerate(wf.PERSONAS[:3]):
        assert persona in texts[i] and all(persona not in t for j, t in enumerate(texts) if j != i)
    assert all("7 is a clear accept" in t for t in texts)                 # same instructions for all
    chair = calls[wf.area_chair][0].text
    assert "R2.W1" in chair and "动机不够清楚" in chair and "725 iterations" in chair
    assert out["reviewers"] == 3 and out["score"] == 7.0 and out["weaknesses"] == {"fatal": 0, "major": 1, "minor": 1}
    assert out["status"] == "PASS_WITH_NOTES"
    est = out["acceptance_estimate"]
    assert est["scores"] == [8, 6, 7] and est["spread"] == 0.82 and est["band"] in ("borderline", "likely_accept")
    record = json.load(open(out["review"], encoding="utf-8"))
    assert [w["id"] for w in record["weaknesses"]] == ["W1", "W2"] and record["weaknesses"][0]["raised_by"] == ["R1.W1"]
    assert [r["id"] for r in record["reviews"]] == ["R1", "R2", "R3"]
    assert record["meta_review"]["recommendation"] == "weak_accept" and record["acceptance_estimate"] == est
    run_record = json.load(open(out["run"], encoding="utf-8"))
    assert [s["stage"] for s in run_record["stages"]] == ["read", "calibrate", "review R1", "review R2",
                                                         "review R3", "area chair", "report"]
    assert set(run_record["roles"]) == {"reviewer", "area_chair"}
    text = open(out["report"], encoding="utf-8").read()
    for part in ("领域主席建议", "评审分歧", "论文证据支持第 1 方", "最可能改变结论的修改", "各位评审", "来自：R1.W1"):
        assert part in text

    # The next round tracks the meta-review's consolidated weaknesses.
    answer = [{"id": "W1", "status": "resolved", "note": "补了"}, {"id": "W2", "status": "partly", "note": ""}]
    with script_roles({wf.reviewer: [review(previous=answer)] * 2,
                       wf.area_chair: [meta(previous=answer, recommendation="accept")]}) as calls:
        second = run(paper, reviewers="2", venue="ACL 2027")
    assert "没有和最强基线比较" in calls[wf.reviewer][0].text and "没有和最强基线比较" in calls[wf.area_chair][0].text
    assert second["round"] == 2 and second["status"] == "PASS_WITH_NOTES"        # W2 only partly
    assert second["acceptance_estimate"]["family"] == "ACL ARR"


def test_the_area_chair_may_cite_only_weaknesses_the_reviewers_raised():
    reviews = [wf.label_review(wf.check_review(review(weaknesses=[MAJOR]), []), "R1", "p1"),
               wf.label_review(wf.check_review(review(), []), "R2", "p2")]
    ok = wf.check_meta(meta([dict(MAJOR, raised_by=["R1.W1"])]), reviews, [])
    assert ok["weaknesses"][0]["id"] == "W1"
    with pytest.raises(ValueError, match="no reviewer raised"):
        wf.check_meta(meta([dict(MAJOR, raised_by=["R2.W1"])]), reviews, [])
    with pytest.raises(ValueError, match="no reviewer raised"):
        wf.check_meta(meta([dict(MAJOR, raised_by=[])]), reviews, [])
    with pytest.raises(ValueError, match="no reviewer raised"):
        wf.check_meta(meta(changes=[{"change": "x", "why": "y", "weaknesses": ["R1.W9"]}]), reviews, [])
    with pytest.raises(ValueError, match="two sides"):
        wf.check_meta(meta(disagreements=[{"point": "p", "sides": [{"reviewers": ["R7"], "position": "a"},
                                                                   {"reviewers": ["R1"], "position": "b"}],
                                           "weaknesses": [], "supported": 0, "evidence": "e"}]), reviews, [])
    with pytest.raises(ValueError, match="every previous weakness"):
        wf.check_meta(meta(), reviews, ["W1"])
    with pytest.raises(ValueError, match="recommendation"):
        wf.check_meta(meta(recommendation="maybe"), reviews, [])
    assert wf.status_of(wf.panel(reviews, wf.check_meta(meta(recommendation="reject"), reviews, []))) == "FAIL"


@pytest.mark.parametrize("venue, family", [
    ("ICLR 2027", "ICLR/NeurIPS/ICML"), ("NeurIPS 2026 main track", "ICLR/NeurIPS/ICML"),
    ("icml", "ICLR/NeurIPS/ICML"), ("ACL 2026 (ARR February cycle)", "ACL ARR"), ("EMNLP 2026", "ACL ARR"),
    ("NAACL", "ACL ARR"), ("CVPR 2026", "CVPR/ICCV/ECCV"), ("ECCV", "CVPR/ICCV/ECCV"),
    ("CVPR 2026, unlike NeurIPS", "CVPR/ICCV/ECCV"), ("Nature Machine Intelligence", None), ("", None),
    ("ORACLE workshop", None)])
def test_the_venue_family_is_read_from_the_venue_name(venue, family):
    scale = wf.venue_scale(venue)
    assert (scale and scale["family"]) == family


def test_the_acceptance_estimate_maps_scores_onto_the_venue_scale():
    high = wf.acceptance_estimate([8, 8, 8], "NeurIPS 2026")
    assert high["mean_on_scale"] == 8 and high["spread"] == 0 and high["band"] == "likely_accept"
    low = wf.acceptance_estimate([4, 5], "ICLR")
    assert low["band"] == "likely_reject" and low["chance"] < 0.3
    assert wf.acceptance_estimate([6], "ICML")["band"] == "borderline"            # ~6 is the borderline
    arr = wf.acceptance_estimate([7], "ACL")
    assert arr["scale"] == [1, 5] and arr["mean_on_scale"] == 3.67 and arr["borderline"] == 3.5
    cv = wf.acceptance_estimate([7], "CVPR")
    assert cv["scale"] == [1, 6] and cv["mean_on_scale"] == 4.33 and cv["band"] == "likely_accept"
    agree, split = wf.acceptance_estimate([7, 7], "ICLR"), wf.acceptance_estimate([4, 10], "ICLR")
    assert agree["chance"] > split["chance"] > 0.5                          # disagreement flattens the curve
    assert "不是预测" in agree["note"]
    assert wf.acceptance_estimate([8], "a journal") is None


def test_an_unknown_venue_scale_gives_no_estimate_and_bad_panel_sizes_are_refused(paper):
    with script_roles({wf.reviewer: [review()]}):
        out = run(paper, venue="Journal of Workflows")
    assert out["acceptance_estimate"] is None
    assert "没有估计" in open(out["report"], encoding="utf-8").read()
    for bad in ("0", "6", "two"):
        with pytest.raises(ValueError, match="reviewers"):
            run(paper, reviewers=bad)



# ---- OpenReview anchors -----------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"
ICLR25 = "ICLR.cc/2025/Conference"


def saved(name, group=ICLR25):
    """A saved API answer, re-addressed to another venue-year when asked."""
    return json.loads((FIXTURES / name).read_text().replace(ICLR25, group))


def serve(calls, groups=("ICLR",)):
    """A fake fetch: the saved answers for the named conferences' venue-years, nothing elsewhere."""
    def fetch(url, params):
        calls.append(params)
        if not params["group"].startswith(groups):
            return {"notes": [], "count": 0}
        return saved("openreview_forums.json" if params["source"] == "forum" else "openreview_replies.json",
                     params["group"])
    return fetch


def test_anchors_are_parsed_from_saved_api_answers():
    found = openreview.parse_group(saved("openreview_forums.json"), saved("openreview_replies.json"), ICLR25,
                                   (1, 10), (1, 10))
    assert [a["forum"] for a in found] == ["acc1", "rej1", "rej2"]     # withdrawn and review-less left out
    acc, rej, combined = found
    assert acc["decision"] == "accept" and acc["decision_from"] == "venueid" and acc["ratings"] == [8, 6]
    assert acc["mean_rating"] == 7 and acc["venue"] == "ICLR 2025 Poster"
    assert acc["link"] == "https://openreview.net/forum?id=acc1"
    assert acc["reviews"][1]["confidence"] == 3                        # "3: You are fairly confident ..."
    assert len(acc["reviews"][0]["summary"]) <= openreview.TEXT + 1 and acc["reviews"][0]["summary"].endswith("…")
    assert rej["decision"] == "reject" and rej["mean_rating"] == 4
    assert combined["decision"] == "reject" and combined["decision_from"] == "decision"
    assert combined["title"] == "Self-Improving Workflow Search" and combined["ratings"] == [4]
    assert combined["reviews"][0]["strengths"] == "Both in one field."
    six = openreview.parse_group(saved("openreview_forums.json"), saved("openreview_replies.json"), ICLR25,
                                 (1, 6), (1, 10))
    assert six[1]["mean_on_family"] == 6.4 and openreview.parse_group({}, {}, ICLR25, (1, 10), (1, 10)) == []
    shown = openreview.examples(found)
    assert [e["decision"] for e in shown] == ["accept", "reject", "reject"] and shown[0]["rating"] == "8 on 1-10"


def test_the_query_comes_from_the_title_and_abstract():
    source = (r"\title{Evolving \emph{Agent} Workflows}" "\n" r"\begin{document}\begin{abstract}"
              r"Workflows change. We study workflow feedback and feedback loops \cite{x}.\end{abstract}")
    title, abstract = openreview.title_and_abstract(source)
    assert title == "Evolving Agent Workflows" and "cite" not in abstract
    assert openreview.query_terms(title, abstract).startswith("evolving agent workflows feedback")
    assert openreview.conferences("NeurIPS 2026", "ICLR/NeurIPS/ICML") == ["NeurIPS", "ICLR"]
    assert openreview.conferences("ICLR 2027", "ICLR/NeurIPS/ICML") == ["ICLR"]
    assert openreview.conferences("ACL 2026", "ACL ARR") == []


def test_anchors_are_cached_by_query(tmp_path):
    calls = []
    first = openreview.collect("workflow agents", "ICLR 2026", "ICLR/NeurIPS/ICML", (1, 10), tmp_path,
                               year=2025)
    assert first["unavailable"] == ["OpenReview (ICLR.cc/2026/Conference)"] and first["anchors"] == []
    assert not list(tmp_path.glob("*.json"))                             # a failure is not cached
    openreview.fetch = serve(calls)                                      # restored by the offline fixture
    first = openreview.collect("workflow agents", "ICLR 2026", "ICLR/NeurIPS/ICML", (1, 10), tmp_path,
                               year=2025)
    assert first["groups"] == ["ICLR.cc/2026/Conference", "ICLR.cc/2025/Conference"]   # the latest two years
    assert len(first["anchors"]) == 6 and first["cached"] is False and len(calls) == 4
    assert first["anchors"][0]["scale"] == [0, 10] and first["anchors"][3]["scale"] == [1, 10]
    again = openreview.collect("workflow agents", "ICLR 2026", "ICLR/NeurIPS/ICML", (1, 10), tmp_path,
                               year=2025)
    assert again["cached"] is True and len(calls) == 4 and again["anchors"] == first["anchors"]
    assert len(list(tmp_path.glob("*.json"))) == 1
    other = openreview.collect("other words", "ICLR 2026", "ICLR/NeurIPS/ICML", (1, 10), tmp_path, year=2025)
    assert other["cached"] is False and len(calls) == 8


def test_a_malformed_answer_gives_no_anchors(tmp_path):
    openreview.fetch = lambda url, params: {"notes": ["not a note"]}
    found = openreview.collect("q", "ICLR", "ICLR/NeurIPS/ICML", (1, 10), tmp_path, year=2025)
    assert found["anchors"] == [] and found["unavailable"][0].startswith("OpenReview (")


def test_the_logistic_fit_recovers_a_known_curve():
    xs, ys = [], []
    for x in range(2, 11):
        p = 1 / (1 + math.exp(-(-6 + 1.0 * x)))
        ones = round(40 * p)
        xs += [float(x)] * 40
        ys += [1] * ones + [0] * (40 - ones)
    a, b = openreview.fit_logistic(xs, ys)
    assert abs(a + 6) < 0.4 and abs(b - 1) < 0.08
    a, b = openreview.fit_logistic([3, 4, 5, 6, 7, 8, 9, 10], [0, 0, 0, 0, 1, 1, 1, 1])   # separable: finite
    assert b > 0 and 6 < -a / b < 7 and math.isfinite(a)


def anchor(mean, decision):
    return {"mean_on_family": mean, "decision": decision}


def test_the_estimate_is_fitted_to_anchors_from_eight_samples():
    seven = [anchor(m, d) for m, d in [(3, "reject"), (4, "reject"), (5, "reject"), (5.5, "accept"),
                                       (6, "reject"), (7, "accept"), (8, "accept")]]
    table = wf.acceptance_estimate([7, 7], "ICLR 2026", seven)
    assert table["method"] == "venue_table" and table["samples"] == 7 and table["fit"] is None
    assert table == dict(wf.acceptance_estimate([7, 7], "ICLR 2026"), samples=7, accepted=3)
    eight = seven + [anchor(6.5, "accept")]
    fitted = wf.acceptance_estimate([7, 7], "ICLR 2026", eight)
    assert fitted["method"] == "openreview_logistic" and fitted["samples"] == 8 and fitted["accepted"] == 4
    assert fitted["fit"]["slope"] > 0 and 5 < fitted["borderline"] < 7 and fitted["band"] == "likely_accept"
    assert "8 篇" in fitted["note"]
    low = wf.acceptance_estimate([4, 4], "ICLR 2026", eight)
    assert low["method"] == "openreview_logistic" and low["chance"] < 0.3
    one_class = [anchor(m, "accept") for m in range(1, 10)]
    assert wf.acceptance_estimate([7], "ICLR", one_class)["method"] == "venue_table"
    assert wf.acceptance_estimate([7], "ICLR", eight + [{"mean_on_family": None, "decision": "accept"}])["samples"] == 8


TITLED = MAIN.replace(r"\begin{document}", r"\title{Evolving Agent Workflows}" "\n" r"\begin{document}"
                      "\n" r"\begin{abstract}Workflows keep changing with feedback.\end{abstract}")


def test_reviewers_see_real_reviews_and_the_report_lists_the_anchors(paper, offline):
    (paper / "0main.tex").write_text(TITLED)
    openreview.fetch = serve(offline)
    with script_roles({wf.reviewer: [review(), review(score=6)], wf.area_chair: [meta()]}) as calls:
        out = run(paper, reviewers="2")
    texts = [c.text for c in calls[wf.reviewer]]
    for t in texts:
        assert "do not copy them" in t and "Evolving Agent Workflows with Feedback" in t      # an accepted one
        assert "Workflow Graphs for Iterative Agents" in t                                   # a rejected one
    assert "do not copy them" not in calls[wf.area_chair][0].text
    assert offline[0]["term"].startswith("evolving agent workflows") and offline[0]["group"].startswith("NeurIPS")
    assert out["calibration"]["anchors"] == 6 and out["calibration"]["unavailable"] == []
    assert out["acceptance_estimate"]["method"] == "venue_table"                       # 6 < 8 anchors
    text = open(out["report"], encoding="utf-8").read()
    assert "校准锚点" in text and "https://openreview.net/forum?id=acc1" in text and "会议常见接收线" in text
    record = json.load(open(out["review"], encoding="utf-8"))
    assert len(record["calibration"]["anchors"]) == 6
    cached = len(offline)
    with script_roles({wf.reviewer: [review()]}):
        again = run(paper)
    assert len(offline) == cached and again["calibration"]["cached"] is True         # the cache answers


def test_an_unreachable_openreview_never_stops_the_review(paper, offline):
    (paper / "0main.tex").write_text(TITLED)
    with script_roles({wf.reviewer: [review()]}) as calls:
        out = run(paper)
    assert offline and out["calibration"]["unavailable"] and out["calibration"]["anchors"] == 0
    assert "do not copy them" not in calls[wf.reviewer][0].text
    assert out["acceptance_estimate"]["method"] == "venue_table"
    assert "未能获取：OpenReview" in open(out["report"], encoding="utf-8").read()


def test_calibration_off_makes_no_request(paper, offline):
    (paper / "0main.tex").write_text(TITLED)
    with script_roles({wf.reviewer: [review()]}):
        out = run(paper, calibration="off")
    assert offline == [] and out["calibration"]["mode"] == "off"
    assert "校准已关闭" in open(out["report"], encoding="utf-8").read()
    with pytest.raises(ValueError, match="calibration"):
        run(paper, calibration="sometimes")
