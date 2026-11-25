"""grant_proposal on a fixture project with claims, evidence, findings, runs and a bib,
the planner and the writer scripted."""

import json
import subprocess
from pathlib import Path

import pytest

from openprogram.programs._authoring.testing import script_roles

from auto_research.tasks.grant import facts
from auto_research.tasks.grant import workflow as wf

PROJECT = "# Project\nKind: method\nClaim: Sparse memory keeps agents accurate while retrieval gets faster.\n"
CLAIMS = """# Claims
Approved: yes

C1: Pruning rarely read memories keeps accuracy at 73.5% on 1,000 tasks.
RQ: RQ1
C2: Retrieval gets 2.4 times faster.
RQ: RQ2
"""
EVIDENCE = """id,claim,statement,source,script
E1,C1,Accuracy 73.5% on 1000 tasks,analysis/acc.csv,scripts/acc.py
E2,C2,Retrieval 2.4x faster than full memory,analysis/speed.csv,scripts/speed.py
"""
FINDINGS = """# Findings

## F1: Usage predicts value
Hypothesis: Rarely read memories are rarely needed.
Run: abc123
Result: Dropping the lowest 20% kept accuracy.
Verdict: supported
"""
EXPERIMENT = "# Experiment loop\nApproved: yes\nEdit: train.py\nCommand: python train.py\nMetric: acc\n" \
             "Direction: higher\nBudget: 5 min\nStop: 30 runs\n"
RESULTS = "commit\tmetric\tstatus\tseconds\tdescription\n" \
          "a1\t0.790\tkeep\t60\tbaseline\nb2\t0.812\tkeep\t61\tprune by usage\nc3\t0.700\tdiscard\t59\tprune random\n"
LITERATURE = "# Literature\n\n- key: contribution; difference from our work\n- wang2024: workflow memory; ours forgets\n"
BIB = """@inproceedings{wang2024,
  title = {Agent Workflow Memory},
  year = {2024},
}
@article{lee2023,
  title = {{Forgetting} in Neural Networks},
  year = 2023,
}
"""
TEX = r"""\documentclass{article}
\title{Sparse Memory for Agents}
\begin{document}
\maketitle
\begin{abstract}
We prune agent memory by usage and keep accuracy at 73.5\% on 1{,}000 tasks.
\end{abstract}
\section{Introduction}
Agents keep every memory they write, so retrieval slows down as the store grows.
\end{document}
"""


def aim(title, claims, support, start, end, gap="Memories only grow."):
    return {"title": title, "objective": "Establish when forgetting is safe.", "gap": gap, "claims": claims,
            "support": support, "months": {"start": start, "end": end}}


PLAN = {"title": "Principled Forgetting for Language Agents",
        "goal": "Agents should keep only what they use. Our pilot kept 73.5% accuracy.",
        "aims": [aim("Usage-based forgetting", ["C1"], ["E1", "F1"], 1, 18,
                     gap="Memories only grow. Stores grow by 50% a month."),
                 aim("Causal memory editing", ["C2"], ["E2"], 13, 30),
                 aim("A forgetting benchmark", ["C1"], ["R1"], 19, 36)]}


def para(heading, kind, text, support=()):
    return {"heading": heading, "kind": kind, "text": text, "support": list(support)}


NSF_SUMMARY = {"paragraphs": [
    para("Overview", "context", "This project extends memory for language agents [@wang2024]. "
                                "It pursues Aim 1, Aim 2 and Aim 3."),
    para("Intellectual Merit", "preliminary", "Pruning kept accuracy at 73.5% on 1,000 tasks. "
                                              "It beat 37 rival systems.", ["E1"]),
    para("Broader Impacts", "proposed", "We will release the code openly. Prior work did the same [@ghost2020].")]}
NSF_DESCRIPTION = {"paragraphs": [
    para("Objectives", "context", "The intellectual merit lies in principled forgetting [@lee2023]."),
    para("Preliminary Results", "preliminary", "Retrieval was 2.4 times faster with the same accuracy.", ["E2", "R1"]),
    para("Research Plan", "proposed", "Aim 1 studies usage-based forgetting in months 1 to 18. "
                                      "Aim 2 studies causal editing. Aim 3 builds a benchmark."),
    para("Broader Impacts", "proposed", "Students will be trained in agent research."),
    para("", "preliminary", "Our best run reached 0.812 accuracy.")]}
NSF_DMP = {"paragraphs": [para("Data and code", "proposed",
                               "All code and logs will be shared under an open license and archived publicly.")]}


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    files = {"project.md": PROJECT, "claims.md": CLAIMS, "evidence.csv": EVIDENCE, "findings.md": FINDINGS,
             "experiment.md": EXPERIMENT, "results.tsv": RESULTS, "literature.md": LITERATURE}
    for name, text in files.items():
        (repo / "workflow" / name).write_text(text, encoding="utf-8")
    (repo / "refs.bib").write_text(BIB, encoding="utf-8")
    (repo / "main.tex").write_text(TEX, encoding="utf-8")
    return repo


def run(paper, funder, plan, sections, **kw):
    with script_roles({wf.planner: [plan], wf.writer: sections}) as calls:
        out = json.loads(wf.grant_proposal(str(paper), funder, **kw))
    return out, calls


def test_nsf_draft_keeps_only_grounded_sentences(paper):
    before = {p: p.read_bytes() for p in paper.rglob("*") if p.is_file() and ".git" not in p.parts}
    out, calls = run(paper, "NSF", PLAN, [NSF_SUMMARY, NSF_DESCRIPTION, NSF_DMP])
    planned = calls[wf.planner][0].text
    assert "preliminary results" in planned and "Sparse memory keeps agents accurate" in planned
    assert "prune by usage" in planned and "Usage predicts value" in planned and "Agents keep every memory" in planned
    assert "key: contribution" not in planned                           # the template line is not a work
    first = calls[wf.writer][0].text
    assert "Return paragraphs in order" in first and "Intellectual Merit" in first and "Agent Workflow Memory" in first
    assert "4600 characters" in first and len(calls[wf.writer]) == 3

    assert out["status"] == "PASS_WITH_NOTES" and out["funder"] == "nsf" and out["aims"] == 3
    assert out["report"].startswith(str(paper / "drafts" / "grant_proposal"))
    record = json.loads(Path(out["json"]).read_text(encoding="utf-8"))
    dropped = sorted((d["section"], d["reason"], tuple(d.get("numbers") or d.get("keys"))) for d in record["dropped"])
    assert dropped == [("plan", "numbers", ("50",)), ("project_summary", "cite", ("ghost2020",)),
                       ("project_summary", "numbers", ("37",))]
    assert record["plan"]["aims"][0]["gap"] == "Memories only grow."
    assert record["plan"]["aims"][2]["support"] == ["R1"]
    assert record["errors"] == []
    assert any("前期结果段落没有指明证据" in n for n in record["notes"])
    summary = record["sections"][0]
    assert summary["unit"] == "chars" and summary["limit"] == 4600 and summary["findings"] == []

    proposal = Path(out["proposal"]).read_text(encoding="utf-8")
    assert "### Broader Impacts" in proposal and "**[Preliminary result]** Pruning kept accuracy at 73.5%" in proposal
    assert "**[Proposed work]** We will release the code openly." in proposal
    assert "37 rival" not in proposal and "ghost2020" not in proposal and "50%" not in proposal
    assert "- [@wang2024] Agent Workflow Memory (2024)" in proposal and "- [@lee2023] Forgetting in Neural Networks" in proposal
    timeline = (Path(out["report"]).parent / "timeline.md").read_text(encoding="utf-8")
    assert "| Aim | Months | Year 1 | Year 2 | Year 3 |" in timeline
    assert "| Aim 1：Usage-based forgetting | 1–18 | ● | ● |  |" in timeline
    budget = (Path(out["report"]).parent / "budget_justification.md").read_text(encoding="utf-8")
    assert "## Participant support costs" in budget and "TODO: which aims need it (Aim 1：Usage-based forgetting" in budget
    sections = sorted(p.name for p in (Path(out["report"]).parent / "sections").iterdir())
    assert sections == ["01-project_summary.md", "02-project_description.md", "03-data_management_plan.md"]
    report = Path(out["report"]).read_text(encoding="utf-8")
    assert "结论：PASS_WITH_NOTES" in report and "删掉的句子" in report and "未知引用 ghost2020" in report
    assert "提交前对照当年指南核对" in report and "https://www.nsf.gov/policies/pappg" in report
    trail = json.loads(Path(out["run"]).read_text(encoding="utf-8"))
    assert [s["stage"] for s in trail["stages"]] == ["gather", "plan", "write", "write", "write", "ground", "check",
                                                     "render", "report"]
    assert set(trail["roles"]) == {"planner", "writer"}
    after = {p: p.read_bytes() for p in paper.rglob("*") if p.is_file() and ".git" not in p.parts
             and "drafts" not in p.parts}
    assert after == before


def test_nih_over_the_page_and_missing_parts_fail(paper):
    long = " ".join(["Memory"] * 600) + "."
    aims_page = {"paragraphs": [para("", "context", long), para("", "proposed", "Aim 1 prunes. Aim 2 edits.")]}
    strategy = {"paragraphs": [para("Significance", "context", "Agents forget nothing today."),
                               para("Approach", "proposed", "Aim 1, Aim 2 and Aim 3 follow in turn.")]}
    out, _ = run(paper, "r01", PLAN, [aims_page, strategy])
    record = json.loads(Path(out["json"]).read_text(encoding="utf-8"))
    assert out["status"] == "FAIL" and out["funder"] == "nih_r01"
    assert record["sections"][0]["length"] == 1.21 and record["sections"][0]["unit"] == "pages"
    assert "Specific Aims：1.21 页，超过上限 1" in record["errors"]
    assert "Specific Aims：没有写到：Aim 3" in record["errors"]
    assert "Research Strategy：缺少：Innovation heading" in record["errors"]
    report = Path(out["report"]).read_text(encoding="utf-8")
    assert "结论：FAIL" in report and "| Specific Aims | 1.21 页 | 1 页 |" in report


def test_a_section_whose_sentences_all_go_is_missing(paper):
    gone = {"paragraphs": [para("Overview", "context", "It beat 37 rivals.")]}
    out, _ = run(paper, "nsf", PLAN, [gone, NSF_DESCRIPTION, NSF_DMP])
    record = json.loads(Path(out["json"]).read_text(encoding="utf-8"))
    assert out["status"] == "FAIL"
    assert "Project Summary：整节缺失：写出的句子全部被删掉" in record["errors"]


ZH_PLAN = {"title": "面向智能体的有原则遗忘", "goal": "让智能体只保留用到的记忆。",
           "aims": [aim("按使用频率遗忘", ["C1"], ["E1"], 1, 24, gap="记忆只增不删。"),
                    aim("因果记忆编辑", ["C2"], ["E2"], 13, 48, gap="缺少编辑记忆的方法。")]}
ZH_SECTIONS = [
    {"paragraphs": [para("", "context", "记忆" * 250)]},
    {"paragraphs": [para("研究意义", "context", "本项目具有重要的科学意义[@wang2024]。国内外研究现状表明记忆只增不删。")]},
    {"paragraphs": [para("研究内容", "proposed", "研究内容1：按使用频率遗忘。研究内容2：因果记忆编辑。"),
                    para("研究目标", "proposed", "建立有原则的遗忘方法。"),
                    para("拟解决的关键科学问题", "context", "什么样的记忆可以安全删除。")]},
    {"paragraphs": [para("技术路线", "proposed", "研究方法是按读取频率给记忆打分。"),
                    para("可行性分析", "preliminary", "前期实验准确率达到 73.5%。准确率提高了 99.9%。", ["E1"])]},
    {"paragraphs": [para("", "context", "本项目的创新之处在于有原则的遗忘。")]},
    {"paragraphs": [para("", "proposed", "第1年完成研究内容1。预期形成一套遗忘方法。")]},
    {"paragraphs": [para("", "preliminary", "前期已经完成剪枝实验，检索快 2.4 倍。", ["E2"])]},
]


def test_nsfc_is_written_and_counted_in_chinese(paper):
    out, calls = run(paper, "面上", ZH_PLAN, ZH_SECTIONS)
    assert "Simplified Chinese" in calls[wf.writer][0].text and "400 Chinese characters" in calls[wf.writer][0].text
    record = json.loads(Path(out["json"]).read_text(encoding="utf-8"))
    assert out["status"] == "FAIL" and out["funder"] == "nsfc" and out["sections"] == 7
    assert record["sections"][0]["length"] == 500 and record["sections"][0]["unit"] == "zh_chars"
    assert record["errors"] == ["摘要：500 字，超过上限 400"]
    assert [(d["reason"], d["numbers"], d["text"]) for d in record["dropped"]] == [
        ("numbers", ["99.9"], "准确率提高了 99.9%。")]
    proposal = Path(out["proposal"]).read_text(encoding="utf-8")
    assert "**【前期结果】** 前期实验准确率达到 73.5%。" in proposal and "99.9" not in proposal
    assert "## 参考文献" in proposal and "| 研究内容 | 月份 | 第1年 | 第2年 | 第3年 | 第4年 |" in proposal
    budget = (Path(out["report"]).parent / "budget_justification.md").read_text(encoding="utf-8")
    assert "# 预算说明" in budget and "## 劳务费" in budget and "TODO：测算依据" in budget


def test_nsfc_youth_shares_the_sections_with_its_own_duration():
    youth, general = facts.funder("青年"), facts.funder("nsfc")
    assert youth["key"] == "nsfc_youth" and youth["duration_months"] == 36 and general["duration_months"] == 48
    assert youth["sections"] == general["sections"] and youth["language"] == "zh"


def test_chinese_counting_and_lengths():
    assert facts.zh_chars("基金ABC 123，测试73.5%") == 8          # 基 金 ABC 123 ， 测 试 73.5%
    assert facts.measure("研究" * 400, "pages", "zh") == 1.0
    assert facts.measure("word " * 250, "pages", "en") == 0.5
    assert facts.measure("ab cd", "chars", "en") == 5 and facts.measure("ab cd", "words", "en") == 2
    nsf = facts.funder("nsf")
    assert "about 6750 words" in facts.target(nsf["sections"][1]["limit"], "en")
    assert facts.aim_pattern(nsf, 1).search("Aim 12") is None and facts.aim_pattern(nsf, 1).search("aim 1:")
    assert facts.aim_pattern(facts.funder("nsfc"), 2).search("研究内容 2：")


def test_every_funder_entry_is_well_formed():
    funders = facts.load()
    assert set(funders) == {"nsf", "nih_r01", "erc", "nsfc", "nsfc_youth", "generic"}
    for f in funders.values():
        assert facts.problems(f) == []
        assert f["key"] == "generic" or f["guides"]
    limits = {(k, s["id"]): (s["limit"] or {}).get("value") for k, f in funders.items() for s in f["sections"]}
    assert limits[("nsf", "project_summary")] == 4600 and limits[("nsf", "project_description")] == 15
    assert limits[("nsf", "data_management_plan")] == 2 and limits[("nih_r01", "specific_aims")] == 1
    assert limits[("nih_r01", "research_strategy")] == 12 and limits[("erc", "b1_extended_synopsis")] == 5
    assert limits[("erc", "b2_scientific_proposal")] == 14 and limits[("nsfc", "abstract")] == 400
    with pytest.raises(ValueError, match="unknown funder"):
        facts.funder("darpa")


def test_inputs_and_role_replies_are_checked(paper, tmp_path):
    empty = tmp_path / "empty"
    subprocess.run(["git", "init", "-q", str(empty)], check=True)
    with pytest.raises(ValueError, match="nothing to write from"):
        wf.grant_proposal(str(empty), "nsf")
    with pytest.raises(ValueError, match="not a Git repository"):
        wf.grant_proposal(str(tmp_path), "nsf")
    nsf = facts.funder("nsf")
    known = {"claims": {"C1", "C2"}, "support": {"E1", "E2", "F1", "R1"}}
    with pytest.raises(ValueError, match="not 2 to 4"):
        wf.check_plan(dict(PLAN, aims=PLAN["aims"][:1]), nsf, known)
    with pytest.raises(ValueError, match="not given: E9"):
        wf.check_plan(dict(PLAN, aims=[aim("x", ["C1"], ["E9"], 1, 2), aim("y", ["C2"], [], 1, 2)]), nsf, known)
    with pytest.raises(ValueError, match="builds on no claim"):
        wf.check_plan(dict(PLAN, aims=[aim("x", [], [], 1, 2), aim("y", ["C2"], [], 1, 2)]), nsf, known)
    with pytest.raises(ValueError, match="months must run within 1 to 36"):
        wf.check_plan(dict(PLAN, aims=[aim("x", ["C1"], [], 30, 40), aim("y", ["C2"], [], 1, 2)]), nsf, known)
    with pytest.raises(ValueError, match="not given: R7"):
        wf.check_section({"paragraphs": [para("", "preliminary", "x", ["R7"])]}, known["support"])
    with pytest.raises(ValueError, match="no paragraph"):
        wf.check_section({"paragraphs": []}, known["support"])
    schema = wf.plan_schema(nsf, known)["properties"]["aims"]
    assert schema["minItems"] == 2 and schema["maxItems"] == 4
    assert schema["items"]["properties"]["claims"]["items"]["enum"] == ["C1", "C2"]
    assert wf.cite_keys(r"see [@a2020; @b2021] and \citep{c2022,d2023}") == ["a2020", "b2021", "c2022", "d2023"]
