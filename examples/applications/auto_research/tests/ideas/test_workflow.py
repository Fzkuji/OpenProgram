"""The idea_generation entry with the search replaced and the roles scripted."""

import json
import subprocess

import re

import pytest

from openprogram import Runtime
from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.ideas import workflow as wf
from auto_research.tasks.survey import search

SURVEY = {"direction": "LLM agent memory", "directions": {"cards": [
    {"title": "记忆的遗忘", "problem": "现有记忆只增不删 [p1][p2]", "evidence": ["p1.L1", "p2.L1"], "active": [],
     "cost": "单卡", "first_step": "在 LongMemEval 上加遗忘策略"}]}}
LITERATURE = "# Literature\n\n- key: contribution; difference from our work\n- wang2024: workflow memory; ours edits it\n"
PRIOR = search.candidate("Forgetting in agent memory", "We prune agent memory by usage.", "2025-03-01",
                         ["A B"], "ICLR", arxiv="2503.00001")
OFFTOPIC = search.candidate("Cooking with robots", "Kitchen robotics.", "2024-01-01", ["C D"], "", arxiv="2401.00002")


def idea(title, gaps, queries):
    return {"title": title, "hypothesis": "假设", "approach": "方法", "outcome": "结果", "effort": "medium",
            "risk": "风险", "gaps": gaps, "queries": queries}


IDEAS = {"ideas": [idea("按使用频率遗忘", ["G1"], ["agent memory forgetting"]),
                   idea("记忆的因果编辑", ["G1", "L1"], ["causal memory editing agents"])]}


@pytest.fixture
def paper(tmp_path, monkeypatch):
    repo = tmp_path / "paper"
    run_dir = repo / "workflow" / "surveys" / "llm-agent-memory" / "20261101-120000"
    run_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (run_dir / "survey.json").write_text(json.dumps(SURVEY, ensure_ascii=False))
    (repo / "workflow" / "literature.md").write_text(LITERATURE)

    def fake(q, n, since):
        return [dict(PRIOR), dict(OFFTOPIC)] if "forgetting" in q else []
    monkeypatch.setattr(search, "SEARCHES", {"arxiv": fake})
    return repo


def run(paper, **kw):
    return json.loads(wf.idea_generation(str(paper), **kw))


def test_ideas_come_from_the_gaps_and_are_ranked_against_the_records(paper):
    verdicts = {"assessments": [
        {"id": "I1", "novelty": "incremental", "closest": ["R1"], "differentiation": "只换了遗忘的依据",
         "feasibility": 5, "impact": 3, "note": ""},
        {"id": "I2", "novelty": "novel", "closest": [], "differentiation": "", "feasibility": 3, "impact": 4,
         "note": ""}]}
    referee = {"matches": [{"match": "M1", "winner": "A", "reason": "能马上在现有基准上验证"}]}
    with script_roles({wf.ideator: [IDEAS], wf.assessor: [verdicts], wf.judge: [referee]}) as calls:
        out = run(paper)
    gen = calls[wf.ideator][0]
    assert "Range from incremental to ambitious" in gen.text                     # docstring
    assert "现有记忆只增不删" in gen.text and "wang2024" in gen.text             # both kinds of gap
    assert "key: contribution" not in gen.text                                   # the template line is not a gap
    judged = calls[wf.assessor][0]
    assert "Forgetting in agent memory" in judged.text and "a bounded search does not prove" in judged.text
    match = calls[wf.judge][0]
    assert "the order A/B" in match.text and "按使用频率遗忘" in match.text and "记忆的因果编辑" in match.text
    record = json.load(open(out["ideas"], encoding="utf-8"))
    # I2 found no record, so it is unverified whatever the assessor said; I1 won the match, so it ranks first
    assert [(i["id"], i["novelty"]) for i in record["ideas"]] == [("I1", "incremental"), ("I2", "unverified")]
    assert record["ideas"][0]["elo"] > 1200 > record["ideas"][1]["elo"]
    assert record["ideas"][0]["beat"] == [{"idea": "I2", "reason": "能马上在现有基准上验证"}]
    assert record["ideas"][0]["closest"][0]["title"] == "Forgetting in agent memory"
    assert out["status"] == "PASS_WITH_NOTES"
    report = open(out["report"], encoding="utf-8").read()
    assert "最接近的工作：Forgetting in agent memory" in report and "胜过 I2：能马上在现有基准上验证" in report
    run_record = json.load(open(out["run"], encoding="utf-8"))
    assert [s["stage"] for s in run_record["stages"]] == ["gather", "generate", "search", "assess", "tournament",
                                                       "tournament", "rank"]
    assert set(run_record["roles"]) == {"ideator", "assessor", "judge"}
    assert not {"rounds", "lineage", "retired", "clusters"} & set(record)      # rounds=0: no evolution
    assert "谱系" not in report and "near_duplicates" not in record["ideas"][0]
    assert out["report"].startswith(str(paper / "drafts" / "idea_generation"))
    assert (paper / "workflow" / "literature.md").read_text() == LITERATURE


def test_direction_picks_one_survey_and_no_gaps_is_rejected(paper, tmp_path):
    gaps, topics = wf.gather(paper, "LLM agent memory")
    assert topics == ["LLM agent memory"] and [g["id"] for g in gaps] == ["G1", "L1"]
    gaps, _ = wf.gather(paper, "something else")
    assert [g["id"] for g in gaps] == ["L1"]
    empty = tmp_path / "empty"
    subprocess.run(["git", "init", "-q", str(empty)], check=True)
    with pytest.raises(ValueError, match="no gaps"):
        run(empty)
    with pytest.raises(ValueError, match="count"):
        run(paper, count="9")
    with pytest.raises(ValueError, match="rounds"):
        run(paper, rounds="6")
    with pytest.raises(ValueError, match="population"):
        run(paper, count="5", population="3")
    with pytest.raises(ValueError, match="population"):
        run(paper, rounds="1", population="41")


def test_the_roles_can_point_only_at_what_they_were_given():
    with pytest.raises(ValueError, match="no known gap"):
        wf.check_ideas({"ideas": [idea("x", ["G9"], ["q q q"])]}, ["G1"])
    ideas = [{"id": "I1"}]
    with pytest.raises(ValueError, match="not given"):
        wf.check_assessments({"assessments": [{"id": "I1", "novelty": "known", "closest": ["R3"],
                                               "feasibility": 1, "impact": 1}]}, ideas, {"I1": [PRIOR]})
    with pytest.raises(ValueError, match="without naming"):
        wf.check_assessments({"assessments": [{"id": "I1", "novelty": "known", "closest": [],
                                               "feasibility": 1, "impact": 1}]}, ideas, {"I1": [PRIOR]})
    with pytest.raises(ValueError, match="unassessed"):
        wf.check_assessments({"assessments": []}, ideas, {"I1": []})


def test_the_tournament_rates_every_pair_and_known_ideas_go_last():
    ideas = [{"id": f"I{n}"} for n in (1, 2, 3)]
    pairs = wf.matches(ideas)
    assert [(m["a"], m["b"]) for m in pairs] == [("I1", "I2"), ("I3", "I1"), ("I2", "I3")]   # A alternates
    beats = {("I1", "I2"), ("I1", "I3"), ("I2", "I3")}
    results = wf.check_matches({"matches": [
        {"match": m["id"], "winner": "A" if (m["a"], m["b"]) in beats else "B", "reason": "r"} for m in pairs]}, pairs)
    ratings = wf.elo(ideas, pairs, results)
    assert ratings["I1"] > ratings["I2"] > ratings["I3"]
    assert sum(ratings.values()) == 3 * wf.ELO
    with pytest.raises(ValueError, match="undecided"):
        wf.check_matches({"matches": []}, pairs)
    with pytest.raises(ValueError, match="unknown or repeated"):
        wf.check_matches({"matches": [{"match": "M9", "winner": "A"}]}, pairs)
    base = {"title": "t", "effort": "low", "queries": ["q"]}
    verdict = lambda novelty: {"novelty": novelty, "closest": [], "differentiation": "", "feasibility": 3,
                               "impact": 3, "note": ""}
    ranked = wf.rank([dict(base, id=i["id"]) for i in ideas], {"I1": verdict("known"), "I2": verdict("novel"),
                                                                "I3": verdict("novel")},
                     {"I1": [PRIOR], "I2": [PRIOR], "I3": [PRIOR]}, ratings, results, pairs)
    assert [i["id"] for i in ranked] == ["I2", "I3", "I1"]


# The evolution loop. The scripted judge prefers the idea whose title has the smaller
# number S<n>, so the ratings follow the numbers.

def strong(n, name, approach, gaps=("G1",), queries=("evolved memory idea",), **made):
    return dict(idea(f"S{n:02d} {name}", list(gaps), list(queries)), approach=approach, **made)


def power(text):
    return int(re.search(r"S(\d+)", text).group(1))


def verdict(iid, novelty="novel", closest=()):
    return {"id": iid, "novelty": novelty, "closest": list(closest), "differentiation": "", "feasibility": 3,
            "impact": 3, "note": ""}


EIGHT = {"ideas": [
    strong(10, "按使用频率遗忘记忆", "统计每条记忆被检索的次数，低频记忆定期删除", queries=["agent memory forgetting"]),
    strong(20, "因果记忆编辑", "用干预实验找到导致错误的记忆并改写"),
    strong(30, "分层摘要压缩", "把旧对话逐层压成摘要树"),
    strong(40, "检索器自我校准", "让检索器根据任务反馈调整阈值"),
    strong(50, "多智能体共享记忆", "多个智能体共用一个带锁的记忆库"),
    strong(60, "时间衰减权重", "按时间指数衰减调整召回分数"),
    strong(70, "记忆冲突检测", "用自然语言推理模型找出互相矛盾的条目"),
    strong(80, "图结构记忆索引", "把实体关系建成知识图谱再检索")]}
EVOLVED = {"ideas": [
    strong(5, "频率遗忘结合因果编辑", "先按频率筛出候选，再用干预实验决定删改", method="combine", parents=["I1", "I2"]),
    strong(12, "按使用频率遗忘记忆", "统计每条记忆被检索的次数，低频记忆定期删除并保留摘要", method="refine",
           parents=["I1"]),
    strong(25, "单层摘要压缩", "只做一层摘要，先在小基准上验证", method="simplify", parents=["I3"]),
    strong(95, "记忆价格机制", "给每条记忆定价，由市场机制决定保留", gaps=["L1"], method="out_of_box", parents=[])]}
META = {"strengths": ["能在现有基准上验证的想法常赢"], "weaknesses": ["规模大的想法缺少数据"],
        "advice": "下一轮多做可以直接在 LongMemEval 上验证的小改动"}


@pytest.fixture
def referee():
    """The judge answers from the matches it is shown; log holds each call's matches as
    (id, title A, title B)."""
    log = []

    def answer(content, model=None, **_):
        for block in content:
            text = block.get("text") or ""
            if text.startswith("matches:\n"):
                shown = json.loads(text.split("\n", 1)[1])
                log.append([(m["match"], m["A"]["title"], m["B"]["title"]) for m in shown])
                return json.dumps({"matches": [
                    {"match": m["match"], "winner": "A" if power(m["A"]["title"]) < power(m["B"]["title"]) else "B",
                     "reason": "数字小的更扎实"} for m in shown]}, ensure_ascii=False)
        raise AssertionError("the judge was shown no matches")

    def install():
        wf.judge.runtime = Runtime(call=answer)
    return log, install


def check_schedule(log):
    ids = [m[0] for call in log for m in call]
    pairs = [frozenset(m[1:]) for call in log for m in call]
    assert len(ids) == len(set(ids)) and len(pairs) == len(set(pairs))          # no match or pair twice
    assert all(len(call) <= wf.BATCH for call in log)


def test_one_evolution_round_evolves_the_top_ideas_and_folds_near_duplicates(paper, referee):
    log, install = referee
    first = {"assessments": [verdict("I1", "incremental", ["R1"])] + [verdict(f"I{n}") for n in range(2, 9)]}
    second = {"assessments": [verdict(f"I{n}") for n in range(9, 13)]}
    with script_roles({wf.ideator: [EIGHT], wf.assessor: [first, second], wf.judge: [],
                       wf.evolver: [EVOLVED], wf.meta_reviewer: [META]}) as calls:
        install()
        out = run(paper, count="8", rounds="1")
    # 28 matches of the first round in two calls, then the Swiss round of 12 ideas
    assert [len(c) for c in log[:2]] == [20, 8] and len(log) >= 3
    check_schedule(log)
    later = [m for call in log[2:] for m in call]
    for t in (i["title"] for i in EVOLVED["ideas"]):
        assert sum(t in m[1:] for m in later) >= wf.NEW_MATCHES
    assert len(later) < 12 * 11 // 2 - 28                                        # not every pair
    evolve = calls[wf.evolver][0]
    assert "combine (join the strengths" in evolve.text                           # docstring
    assert all(t["title"] in evolve.text for t in EIGHT["ideas"][:4])              # the top four
    assert "数字小的更扎实" in evolve.text and "Forgetting in agent memory" in evolve.text
    assert "meta_review" not in evolve.text                                       # no advice yet
    meta = calls[wf.meta_reviewer][0]
    assert "数字小的更扎实" in meta.text and "what recurs" in meta.text
    record = json.load(open(out["ideas"], encoding="utf-8"))
    assert [x["round"] for x in record["rounds"]] == [0, 1] and record["rounds"][1]["meta_review"] == META
    assert record["lineage"]["I9"]["method"] == "combine" and record["lineage"]["I9"]["parents"] == ["I1", "I2"]
    assert record["lineage"]["I11"] == {"round": 1, "method": "simplify", "parents": ["I3"],
                                        "parent_titles": [EIGHT["ideas"][2]["title"]], "gaps": ["G1"]}
    # I1 and its refinement I10 are near-duplicates: the better rated one stays
    (group,) = record["clusters"]
    assert {group["kept"], *group["near_duplicates"]} == {"I1", "I10"}
    kept = next(i for i in record["ideas"] if i["id"] == group["kept"])
    assert kept["near_duplicates"][0]["elo"] <= kept["elo"]
    # population defaults to count: 12 ideas, 4 retire, 8 alive, 7 listed
    assert len(record["retired"]) == 4 and len(record["ideas"]) == 7
    assert max(r["elo"] for r in record["retired"]) <= min(i["elo"] for i in record["ideas"])
    assert "I12" in [r["id"] for r in record["retired"]]
    report = open(out["report"], encoding="utf-8").read()
    for text in ("## 谱系", "组合自 I1", "## 各轮", "## 元评审", META["advice"], "## 退场的想法", "近似重复"):
        assert text in report
    stages = [s["stage"] for s in json.load(open(out["run"], encoding="utf-8"))["stages"]]
    assert stages[:6] == ["gather", "generate", "search", "assess", "tournament", "tournament"]
    assert stages[6:11] == ["tournament", "evolve", "search", "assess", "tournament"]
    assert stages[-3:] == ["meta_review", "proximity", "rank"] and stages.count("round") == 1
    assert set(json.load(open(out["run"], encoding="utf-8"))["roles"]) == set(wf.ROLES)


def test_two_rounds_feed_the_meta_review_on_and_keep_the_population(paper, referee, monkeypatch):
    log, install = referee
    monkeypatch.setattr(wf, "BATCH", 5)
    four = {"ideas": EIGHT["ideas"][:4]}
    round1 = {"ideas": [EVOLVED["ideas"][0],
                        strong(15, "因果编辑加回滚", "改写记忆前存快照，出错就回滚", method="refine", parents=["I2"]),
                        strong(25, "记忆保险", "为高风险记忆建立备份", method="out_of_box", parents=[])]}
    round2 = {"ideas": [strong(3, "频率因果双筛", "频率筛选后做因果检验并定价", method="combine", parents=["I5", "I1"]),
                        strong(35, "只按频率删", "只保留频率一个信号", method="simplify", parents=["I1"]),
                        strong(50, "记忆的遗忘曲线", "仿照艾宾浩斯曲线安排复习", gaps=["L1"], method="out_of_box",
                               parents=[])]}
    advice2 = dict(META, advice="第二轮的建议")
    assess = [{"assessments": [verdict(f"I{n}") for n in ns]} for ns in ((1, 2, 3, 4), (5, 6, 7), (8, 9, 10))]
    with script_roles({wf.ideator: [four], wf.assessor: assess, wf.judge: [], wf.evolver: [round1, round2],
                       wf.meta_reviewer: [META, advice2]}) as calls:
        install()
        out = run(paper, count="4", rounds="2", population="6")
    assert [len(c) for c in log[:2]] == [5, 1]                                    # 6 matches, batches of 5
    check_schedule(log)
    assert sum(len(c) for c in log[2:5]) == 7 * 6 // 2 - 6                        # 7 alive: every new pair
    evolves = calls[wf.evolver]
    assert META["advice"] not in evolves[0].text and META["advice"] in evolves[1].text
    assert len(calls[wf.meta_reviewer]) == 2
    record = json.load(open(out["ideas"], encoding="utf-8"))
    assert [x["retired"] for x in record["rounds"]][:2] == [[], ["I4"]]
    assert len(record["rounds"][2]["retired"]) == 3 and len(record["retired"]) == 4
    assert len(record["rounds"][2]["alive"]) == 6
    assert len(record["ideas"]) + sum(len(c["near_duplicates"]) for c in record["clusters"]) == 6
    last = [m for call in log for m in call if any(t in m[1:] for t in (i["title"] for i in round2["ideas"]))]
    for t in (i["title"] for i in round2["ideas"]):
        assert sum(t in m[1:] for m in last) >= wf.NEW_MATCHES
    assert record["lineage"]["I8"]["parents"] == ["I5", "I1"]
    i8 = next(i for i in record["ideas"] if i["id"] == "I8")
    assert i8["wins"] >= wf.NEW_MATCHES and i8["losses"] == 0 and i8["elo"] > wf.ELO
    assert "第二轮的建议" in open(out["report"], encoding="utf-8").read()


def test_swiss_pairing_never_repeats_a_pair_and_gives_new_ideas_three_matches():
    alive = [f"I{n}" for n in range(1, 13)]
    played = {frozenset(p) for p in combinations_of(alive[:8])}
    ratings = {f"I{n}": 1300 - 20 * n for n in range(1, 9)}
    new = alive[8:]
    pairs = wf.schedule(alive, ratings, played, new, 29)
    met = [frozenset((m["a"], m["b"])) for m in pairs]
    assert len(met) == len(set(met)) and not set(met) & played
    assert [m["id"] for m in pairs] == [f"M{n}" for n in range(29, 29 + len(pairs))]
    assert all(sum(i in m for m in met) >= wf.NEW_MATCHES for i in new)
    assert len(pairs) < 12 * 11 // 2 - 28
    # up to eight alive, every pair not met yet
    assert len(wf.schedule(alive[:8], ratings, {frozenset(("I1", "I2"))}, [], 1)) == 27
    assert [len(b) for b in wf.batches(list(range(45)), 20)] == [20, 20, 5]
    assert wf.batches([], 0) == []


def combinations_of(ids):
    return [(x, y) for k, x in enumerate(ids) for y in ids[k + 1:]]


def test_near_duplicates_cluster_by_words_of_title_and_approach():
    ideas = [{"id": "I1", "title": "按使用频率遗忘记忆", "approach": "统计检索次数，低频记忆定期删除"},
             {"id": "I2", "title": "按使用频率遗忘记忆", "approach": "统计检索次数，低频记忆定期删除并留摘要"},
             {"id": "I3", "title": "Causal memory editing", "approach": "intervene and rewrite memories"}]
    assert wf.cluster(ideas, ["I2", "I3", "I1"]) == [["I2", "I1"], ["I3"]]
    assert wf.jaccard(wf.words("a b"), wf.words("c d")) == 0


def test_the_evolver_must_name_the_parents_its_way_takes():
    top = ["I1", "I2"]

    def made(method, parents, gaps=("G1",)):
        return {"ideas": [dict(idea("x", list(gaps), ["q q q"]), method=method, parents=parents)]}
    assert wf.check_evolved(made("combine", ["I1", "I2"]), ["G1"], top, 9)[0]["id"] == "I9"
    for bad in (made("combine", ["I1"]), made("refine", ["I3"]), made("out_of_box", ["I1"]),
                made("mutate", []), made("combine", ["I1", "I1"])):
        with pytest.raises(ValueError, match="parents|unknown way"):
            wf.check_evolved(bad, ["G1"], top, 9)
    with pytest.raises(ValueError, match="no known gap"):
        wf.check_evolved(made("out_of_box", [], ["G7"]), ["G1"], top, 9)
    with pytest.raises(ValueError, match="meta-reviewer"):
        wf.check_meta({"strengths": [], "weaknesses": [], "advice": ""})
