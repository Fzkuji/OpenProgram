"""Run one task of a research project.

The tasks are declared in registry.py, each doing one thing, and do not depend on each
other. Any task can run on its own, in any order: it checks only its own inputs and,
when they pass, runs its workflow if it has one. Each run leaves its result in
drafts/auto_research/state.json, which the status view reads. Plain code: no role of
its own and no model call.
"""

import json
from datetime import datetime
from pathlib import Path

from openprogram.runtime.agent.call_state import check_cancelled

from auto_research.trail import Trail

from . import registry, release, stages
from .stages import STATE


def auto_research(paper_dir: str, task: str = "status", section: str = "", draft_dir: str = "",
                  baseline: str = "HEAD", scope: str = "changes", phases: str = "", direction: str = "",
                  reader_level: str = "", purpose: str = "", our_work: str = "", preset: str = "",
                  reviewer_model: str = "") -> str:
    """Run one task of a research project and say what to do next.

    task is status or one of the tasks in registry.TASKS. A task checks only its own
    inputs and, when they pass, runs its workflow; section, draft_dir, baseline, scope and
    phases go to the tasks that declare them. paper_check checks one section against its
    plan card, or the whole paper when section is empty; phases picks checklist phases
    ("4,6"). field_survey surveys the field named by direction for a reader who knows
    reader_level; purpose is survey, scouting or related_work (which needs our_work);
    preset fixes its research lines (llm_knowledge: the four 2025-2026 LLM knowledge
    tracks). idea_generation proposes ranked ideas from the surveyed gaps, of
    direction's survey when one is named. figures checks the plotting scripts against the figure
    rules and redraws every figure from them. paper_review reviews the paper, or draft_dir, as a reviewer of its venue,
    on reviewer_model when one is named. status lists every task with its declaration, its last runs and the current
    state of its own inputs, for the app; it writes nothing. A task run creates its missing
    artifacts from templates; nothing in the paper is edited.
    """
    if task != "status" and task not in registry.BY_NAME:
        raise ValueError(f"task must be status or one of {', '.join(registry.BY_NAME)}")
    paper = Path(paper_dir).expanduser().resolve()
    if not (paper / ".git").exists():
        raise ValueError(f"{paper} is not a Git repository")
    if task == "status":
        return json.dumps(status(paper), ensure_ascii=False, indent=2)
    created: list[str] = []

    spec = registry.BY_NAME[task]
    given = {"section": section.strip(), "draft_dir": draft_dir, "baseline": baseline, "scope": scope,
             "phases": phases, "direction": direction.strip(), "reader_level": reader_level, "purpose": purpose,
             "our_work": our_work, "preset": preset.strip(),
             "reviewer_model": reviewer_model.strip()}
    inputs = {k: v for k, v in given.items() if k in spec.inputs}
    for name in spec.required:
        if not inputs.get(name):
            raise ValueError(f"task {task} needs {name}")

    trail = Trail("auto_research", {"paper_dir": str(paper), "task": task, **inputs}, {})
    out = paper / "drafts" / "auto_research" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    result = {"task": task, "created": created, "run": str(out / "run.json")}

    problems, failed = {}, None
    for name in spec.gates:
        gate = registry.GATES[name]
        if any(not inputs.get(need) for need in gate.needs):
            trail.step(f"gate {name}", skipped=f"no {', '.join(gate.needs)}")
            continue
        found = gate.check(paper, created, inputs)
        trail.step(f"gate {name}", problems=found)
        if found:
            problems[name] = found
            failed = failed or name
    result["problems"] = [p for found in problems.values() for p in found]
    if failed:
        result.update(status="BLOCKED", blocked_by=failed, next=registry.GATES[failed].next)
    elif spec.worker is None:
        result.update(status="PASS", next=spec.done)
    else:
        check_cancelled()
        check = spec.worker.run(paper, inputs)
        trail.step(spec.worker.workflow, status=check.get("status"), report=check.get("report"),
                   run=check.get("run"))
        result.update(status=check.get("status", "FAIL"), check=check, next=spec.done)
    trail.save(out, report=(result.get("check") or {}).get("report") or "")
    remember(paper, task, inputs, result)
    return json.dumps(result, ensure_ascii=False, indent=2)


# Plain code below: no model calls.

def remember(paper: Path, task: str, inputs: dict, result: dict) -> None:
    """Keep the last result of each task, and of each section for tasks that take one."""
    path = paper / STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    state = stages.load_state(paper)
    key = f"{task}:{inputs['section']}" if inputs.get("section") else task
    state[key] = {"task": task, "inputs": inputs, "status": result["status"], "commit": release.head(paper),
                  "at": datetime.now().isoformat(timespec="seconds"), "run": result["run"],
                  "report": (result.get("check") or {}).get("report")}
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def status(paper: Path) -> dict:
    """Every task with its declaration, its last runs and the current state of its own
    inputs. Each gate runs once, however many tasks share it. Gates that read an input
    (the plan card of a section) or the network (the code release) are not run here; the
    task's last run shows them. A task with a gate that needs no input, whose artifacts the
    author has not begun (missing, or still the template) and that never ran, is
    not_started: its gates are not judged. Nothing is written."""
    last = stages.load_state(paper)
    results = {}
    rows = []
    for spec in registry.TASKS:
        runs = [v for v in last.values() if v.get("task") == spec.name]
        judged = [g for g in spec.gates if not registry.GATES[g].needs]
        if judged and not runs and not any(stages.started(paper, a) for a in spec.artifacts):
            rows.append({**registry.describe(spec), "state": "not_started", "blocked_by": None, "problems": [],
                         "done": spec.done, "next": spec.done, "last_runs": []})
            continue
        problems = []
        for name in spec.gates:
            gate = registry.GATES[name]
            if gate.needs or gate.live:
                continue
            if name not in results:
                results[name] = gate.check(paper, None, {})
            problems += results[name]
        blocked = next((g for g in spec.gates if results.get(g)), None)
        rows.append({**registry.describe(spec), "state": "blocked" if blocked else "ready",
                     "blocked_by": blocked, "problems": problems, "done": spec.done,
                     "next": registry.GATES[blocked].next if blocked else spec.done,
                     "last_runs": sorted(runs, key=lambda v: v["at"], reverse=True)})
    return {"task": "status", "created": [], "tasks": rows, "plans": stages.plan_cards(paper)}


PROGRAM_ENTRIES = [auto_research]

ENTRY_OPTIONS = {
    "auto_research": {
        "input": {
            "paper_dir": {"description": "The project's Git repository"},
            "task": {"description": "status, or one of " + ", ".join(registry.BY_NAME)},
            "section": {"description": "Section title as written in the paper; needed by plan; "
                                       "empty paper_check checks the whole paper"},
            "draft_dir": {"description": "Folder with the proposed revision; empty checks the working tree"},
            "baseline": {"description": "Git revision to compare against, or WORKTREE"},
            "scope": {"description": '"changes" reviews added paragraphs; "paper" reviews every paragraph'},
            "phases": {"description": "Checklist phases of paper_check by number, such as 4,6; empty runs all"},
            "direction": {"description": "The field field_survey surveys, in a few words; "
                                         "idea_generation works from that field's survey (empty: every survey)"},
            "reader_level": {"description": "What the survey's reader already knows; empty lets the planner state it"},
            "purpose": {"description": "survey, scouting or related_work; empty is survey"},
            "our_work": {"description": "What our paper contributes; needed when purpose is related_work"},
            "preset": {"description": "Fixed research lines for field_survey: llm_knowledge (2025-2026 LLM memory "
                                      "architecture, memory lifecycle, modular experts, knowledge adaptation); "
                                      "empty lets the planner choose the lines"},
            "reviewer_model": {"description": "Model the paper_review reviewer runs on (provider:model), so the paper "
                                              "is not reviewed by the model that wrote it; empty uses the caller's model"},
        },
    },
}
