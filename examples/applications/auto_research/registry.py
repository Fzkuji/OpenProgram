"""The task registry: every task of a research project, declared as data.

A task does one thing: it checks one set of artifacts, or it runs one workflow. Tasks
are not grouped into larger steps and do not depend on each other: any task runs on its
own, in any order, and checks only what it reads. A new need is a new row here, added
when it arises; the entry, the status view and the app read this table and do not change.
The phase is only a label the app groups rows by.

Gates and workers are plain code. A worker builds the child workflow's arguments from
the paper folder and the entry inputs; no model chooses or fills them. Model calls
happen only inside the child workflows, whose roles may hold tools they call when needed.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import release, stages
from .tasks.citations import citation_check
from .tasks.figures import paper_figures
from .tasks.figures import scripts as figure_scripts
from .tasks.ideas import idea_generation
from .tasks.ideas.workflow import gather as gather_gaps
from .tasks.review import paper_review
from .tasks.revision import paper_revision_check
from .tasks.revision.rules import paper as paper_rules
from .tasks.survey import research_survey


@dataclass(frozen=True)
class Gate:
    name: str
    check: Callable[[Path, list, dict], list[str]]   # (paper, created, inputs) -> problems
    next: str                                         # what to do when it fails
    needs: tuple[str, ...] = ()                       # inputs it reads; skipped while they are empty
    live: bool = False                                # calls the network; the status view skips it


@dataclass(frozen=True)
class Worker:
    workflow: str                                     # the child workflow, as registered
    run: Callable[[Path, dict], dict]                 # (paper, inputs) -> the child's JSON result


@dataclass(frozen=True)
class Task:
    name: str
    title: str                                        # shown in the app
    phase: str                                        # a label for grouping: research, writing, release
    gates: tuple[str, ...] = ()                       # checks on this task's own inputs
    artifacts: tuple[str, ...] = ()                   # paths under workflow/
    inputs: tuple[str, ...] = ()                      # entry arguments the task uses
    required: tuple[str, ...] = ()                    # inputs that must not be empty
    worker: Worker | None = None                      # the one workflow it runs; None: gates only
    done: str = ""                                    # what to do after it passes


# Gates. Each checks one task's own artifacts and reads any other artifact it refers
# to without judging it: the plan gate needs the claim and evidence ids, not their approval.

def gate_survey(paper, created, inputs):
    return stages.survey(paper, created)


def gate_idea(paper, created, inputs):
    return stages.claims(paper, created)[0]


def gate_gaps(paper, created, inputs):
    """Something to generate ideas from: a field survey's open directions or surveyed works."""
    stages.scaffold(paper, "literature.md", created)
    if gather_gaps(paper, inputs.get("direction", ""))[0]:
        return []
    where = f"workflow/surveys/{stages.slug(inputs['direction'])}/" if inputs.get("direction") else "workflow/surveys/"
    return [f"no gaps: no field survey with open directions in {where} and no surveyed work in literature.md"]


def gate_experiments(paper, created, inputs):
    claims = stages.claims(paper, created)[1]
    return stages.evidence(paper, created, claims)[0]


def gate_plan(paper, created, inputs):
    claims = stages.claims(paper, created)[1]
    evidence = stages.evidence(paper, created, claims)[1]
    return stages.plan(paper, inputs["section"], created, claims, evidence)


def gate_code(paper, created, inputs):
    return release.code_release(paper, paper_rules.load(paper).code)


def gate_deliverables(paper, created, inputs):
    return release.deliverables(paper, paper_rules.load(paper).deliverables)


def gate_figures(paper, created, inputs):
    """The helper module is in place and at least one plotting script exists."""
    stages.scaffold(paper, "paperfig.py", created, target="figures/paperfig.py")
    patterns = paper_rules.load(paper).figure_scripts
    return [] if figure_scripts(paper, patterns) else [f"no plotting script matches {list(patterns)}"]


def gate_checklist(paper, created, inputs):
    """The venue.md checklist of the version being prepared, as rules.json names it."""
    return stages.checklist(paper, stages.CHECKLISTS[paper_rules.load(paper).version], created)


GATES = {g.name: g for g in (
    Gate("survey", gate_survey,
         "Write the venue requirements and checklists in workflow/venue.md, the paper's rules in "
         "workflow/rules.json, and list the surveyed works by BibTeX key in workflow/literature.md."),
    Gate("idea", gate_idea,
         "Write each claim with the RQ that tests it in workflow/claims.md, show them to the author "
         "in Chinese, and set Approved: yes only after the author agrees."),
    Gate("gaps", gate_gaps,
         "Run field_survey on the direction, whose open directions are the gaps, or list the surveyed "
         "works by BibTeX key in workflow/literature.md."),
    Gate("experiments", gate_experiments,
         "Run the experiments and record every number the paper will use in workflow/evidence.csv "
         "with its source file and the script that produced it."),
    Gate("plan", gate_plan, needs=("section",), next=(
         "Fill the plan card before writing: the claim the section supports, what the evidence actually "
         "shows, what it cannot claim (including groupings or framing the figures do not contain), and "
         "the storyline. Show it to the author in Chinese; set Approved: yes only after the author agrees.")),
    Gate("figures", gate_figures,
         "Write each figure as workflow/figures/plot_<name>.py with the helpers of workflow/figures/paperfig.py, "
         "reading its numbers from the sources in workflow/evidence.csv. Scripts kept elsewhere are named in "
         "figure_scripts of workflow/rules.json."),
    Gate("code", gate_code, live=True, next=
         "Make the repository in code.repo of workflow/rules.json public with a README, link it in the paper, "
         "and keep its contributors to code.allowed_contributors."),
    Gate("deliverables", gate_deliverables,
         "Make every deliverable listed in workflow/rules.json (poster, slides, video) at its path."),
    Gate("checklist", gate_checklist,
         "Tick every item of the checklist for this version (version in workflow/rules.json) in "
         "workflow/venue.md after doing it. Items another task checks are not listed there."),
)}


# Workers. Code fills every argument of the child workflow.

def draft_of(paper: Path, inputs: dict) -> Path:
    return Path(inputs["draft_dir"]).expanduser() if inputs.get("draft_dir") else paper


def run_paper_check(paper: Path, inputs: dict) -> dict:
    """paper_revision_check on the section when one is given, otherwise on the whole paper."""
    return json.loads(paper_revision_check(
        str(paper), draft_dir=inputs.get("draft_dir", ""), baseline=inputs.get("baseline") or "HEAD",
        scope=inputs.get("scope") or "changes", plan_dir=str(stages.folder(paper) / "plans"),
        phases=inputs.get("phases", ""), sections=inputs.get("section", "")))


def run_citation_check(paper: Path, inputs: dict) -> dict:
    """citation_check on every bibliography of the draft, in plain code (investigate off)."""
    draft = draft_of(paper, inputs)
    results = [json.loads(citation_check(str(draft / name), out_dir=str(paper / "drafts" / "citation_check"),
                                         investigate="off"))
               for name in paper_rules.load(paper).bib_files if (draft / name).exists()]
    if not results:
        return {"status": "FAIL", "report": None, "problems": ["no bibliography file found"]}
    return {"status": worst(r["status"] for r in results), "report": results[0]["report"],
            "run": results[0].get("run"), "results": results}


def run_research_survey(paper: Path, inputs: dict) -> dict:
    """research_survey on the direction, within the preset's tracks when one is named,
    saved under workflow/surveys/<direction>/."""
    base = stages.folder(paper) / "surveys" / stages.slug(inputs["direction"])
    return json.loads(research_survey(inputs["direction"], reader_level=inputs.get("reader_level", ""),
                                      purpose=inputs.get("purpose") or "survey", our_work=inputs.get("our_work", ""),
                                      out_dir=str(base), preset=inputs.get("preset", "")))


def run_idea_generation(paper: Path, inputs: dict) -> dict:
    """idea_generation on the surveyed gaps, of direction's survey only when one is given;
    reports go to drafts/idea_generation/."""
    return json.loads(idea_generation(str(paper), direction=inputs.get("direction", ""),
                                      out_dir=str(paper / "drafts" / "idea_generation")))


def run_paper_review(paper: Path, inputs: dict) -> dict:
    """paper_review on the draft, or the working tree, with the reviewer on reviewer_model
    when one is named; reviews go to drafts/paper_review/, where the last one is the previous round."""
    return json.loads(paper_review(str(paper), draft_dir=inputs.get("draft_dir", ""),
                                   reviewer_model=inputs.get("reviewer_model", ""),
                                   out_dir=str(paper / "drafts" / "paper_review")))


def run_paper_figures(paper: Path, inputs: dict) -> dict:
    """paper_figures on the draft, or the working tree; reports go to drafts/paper_figures/."""
    return json.loads(paper_figures(str(paper), draft_dir=inputs.get("draft_dir", ""),
                                    out_dir=str(paper / "drafts" / "paper_figures")))


ORDER = ("PASS", "PASS_WITH_NOTES", "FAIL")


def worst(statuses) -> str:
    return max(statuses, key=lambda s: ORDER.index(s) if s in ORDER else len(ORDER))


FIX = ("Fix every FAIL and REVIEW, then show the author the clean and red/green PDFs. Checked, "
       "correct fixes are written into the paper and committed.")

TASKS = (
    Task("survey", "调研材料", "research", gates=("survey",),
         artifacts=("venue.md", "rules.json", "literature.md")),
    Task("field_survey", "领域综述", "research", artifacts=("surveys/",),
         inputs=("direction", "reader_level", "purpose", "our_work", "preset"), required=("direction",),
         worker=Worker("research_survey", run_research_survey),
         done="Open survey.html and read report.md; audit issues it lists are fixed in a new run, not by hand."),
    Task("idea", "想法与研究问题", "research", gates=("idea",), artifacts=("claims.md",)),
    Task("idea_generation", "想法生成", "research", gates=("gaps",), artifacts=("surveys/", "literature.md"),
         inputs=("direction",), worker=Worker("idea_generation", run_idea_generation),
         done="Read ideas.md with the author; an idea they choose becomes a claim in workflow/claims.md, "
              "written and approved through the idea task."),
    Task("experiments", "实验证据", "research", gates=("experiments",), artifacts=("evidence.csv",)),
    Task("plan", "章节计划", "writing", gates=("plan",), artifacts=("plans/",),
         inputs=("section",), required=("section",),
         done="Write the section in a draft copy along the approved plan card, then run paper_check on it."),
    Task("paper_check", "论文检查", "writing", gates=("plan",),
         inputs=("section", "draft_dir", "baseline", "scope", "phases"),
         worker=Worker("paper_revision_check", run_paper_check), done=FIX),
    Task("citation_check", "参考文献核查", "writing", inputs=("draft_dir",),
         worker=Worker("citation_check", run_citation_check),
         done="Correct each flagged entry from its published record."),
    Task("figures", "论文图表", "writing", gates=("figures",), artifacts=("figures/",), inputs=("draft_dir",),
         worker=Worker("paper_figures", run_paper_figures),
         done="Fix each finding in the plotting scripts and run the task again; look at every redrawn figure "
              "before it goes into the paper."),
    Task("paper_review", "论文评审", "writing", inputs=("draft_dir", "reviewer_model"),
         worker=Worker("paper_review", run_paper_review),
         done="Fix each fatal and major weakness in a draft copy, then run paper_review on that draft; "
              "the next review says which earlier weaknesses are resolved."),
    Task("code_release", "代码发布", "release", gates=("code",), artifacts=("rules.json",)),
    Task("deliverables", "海报与报告材料", "release", gates=("deliverables",), artifacts=("rules.json",)),
    Task("venue_checklist", "会议清单", "release", gates=("checklist",), artifacts=("venue.md",)),
)
BY_NAME = {t.name: t for t in TASKS}


def describe(task: Task) -> dict:
    """The task as the app sees it."""
    return {"name": task.name, "title": task.title, "phase": task.phase, "gates": list(task.gates),
            "artifacts": [f"{stages.FOLDER}/{a}" for a in task.artifacts], "inputs": list(task.inputs),
            "required": list(task.required), "workflow": task.worker.workflow if task.worker else None}


def check_registry() -> list[str]:
    """Every task names known gates and declares its required inputs and the inputs its gates read."""
    found = []
    for t in TASKS:
        found += [f"{t.name}: unknown gate {g}" for g in t.gates if g not in GATES]
        found += [f"{t.name}: required input {i} is not an input" for i in t.required if i not in t.inputs]
        found += [f"{t.name}: gate {g} reads {i}, which is not an input"
                  for g in t.gates if g in GATES for i in GATES[g].needs if i not in t.inputs]
        found += [f"{t.name}: does nothing" for _ in [0] if not t.gates and not t.worker]
    return found


__all__ = ["BY_NAME", "GATES", "TASKS", "Gate", "Task", "Worker", "check_registry", "describe", "worst"]
