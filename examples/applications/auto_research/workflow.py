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
                  reviewer_model: str = "", reviewers: str = "", min_shared: str = "",
                  other_projects: str = "", question: str = "", runs: str = "", data: str = "", own_papers: str = "",
                  media_kind: str = "", minutes: str = "", rounds: str = "", population: str = "",
                  calibration: str = "", rerun: str = "", tolerance: str = "", host: str = "",
                  remote_command: str = "", scheduler: str = "", include: str = "", anonymize: str = "",
                  target_venue: str = "", write_venue: str = "", arxiv_id: str = "", publish: str = "", funder: str = "", stats_mode: str = "",
                  alpha: str = "", budget: str = "", components: str = "", queries: str = "", since: str = "",
                  text_field: str = "", ngram: str = "", sample: str = "", infini_index: str = "", work: str = "",
                  code_dir: str = "", audio: str = "", count: str = "", agenda: str = "", members: str = "",
                  space: str = "", trials: str = "", seconds: str = "", language: str = "", venue: str = "", exclude: str = "", theorem: str = "",
                  attempts: str = "", codebook: str = "", power: str = "",
                  effect: str = "", by_dataset: str = "", call: str = "", kinds: str = "", outputs: str = "",
                  criteria: str = "", raters: str = "", responses: str = "", works: str = "", screeners: str = "", author: str = "") -> str:
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
    on reviewer_model when one is named, by a panel of reviewers (1 to 5, default 3) and an
    area chair. citation_graph flags retracted references and the works cited by at least
    min_shared (default 3) of the paper's references that it does not cite. research_map
    draws the argument map and keeps the world model, with the findings of other_projects.
    consensus answers question by independent runs (default 3) over the results and the
    data files named. text_overlap compares the prose with own_papers and the cited works.
    media makes a podcast or a video (media_kind) of about minutes minutes. idea_generation
    evolves the ideas for rounds rounds, keeping at most population alive; paper_review
    calibrates on real OpenReview reviews unless calibration is off. campaign runs one
    step of the unattended campaign of workflow/campaign.md. clean_rerun reruns the kept
    runs named by rerun within tolerance; remote_run runs on host (ssh or slurm scheduler);
    anonymous_package bundles include, anonymize list or replace; venue_move plans the move
    to target_venue; related_by_paragraph uses arxiv_id; project_page copies to docs/ when
    publish is yes; grant drafts a proposal for funder. claim_stats runs stats_mode
    (tests or popper) at alpha; data_explore explores data within budget hypotheses;
    ablation_target ablates components; topic_trends counts queries since a year;
    contamination checks text_field of data with ngram windows, sample items and
    infini_index; after_release follows work; paper_agent wraps code_dir; talk_prep makes
    audio when asked. status lists every task with its declaration, its last runs and the current
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
             "reviewer_model": reviewer_model.strip(), "reviewers": reviewers.strip(),
             "min_shared": min_shared.strip(), "other_projects": other_projects.strip(), "question": question.strip(),
             "runs": runs.strip(), "data": data.strip(), "own_papers": own_papers.strip(),
             "media_kind": media_kind.strip(), "minutes": minutes.strip(), "rounds": rounds.strip(),
             "population": population.strip(), "calibration": calibration.strip(), "rerun": rerun.strip(),
             "tolerance": tolerance.strip(), "host": host.strip(), "remote_command": remote_command.strip(),
             "scheduler": scheduler.strip(), "include": include.strip(), "anonymize": anonymize.strip(),
             "target_venue": target_venue.strip(), "write_venue": write_venue.strip(), "arxiv_id": arxiv_id.strip(),
             "publish": publish.strip(), "funder": funder.strip(), "stats_mode": stats_mode.strip(),
             "alpha": alpha.strip(), "budget": budget.strip(), "components": components.strip(),
             "queries": queries.strip(), "since": since.strip(), "text_field": text_field.strip(),
             "ngram": ngram.strip(), "sample": sample.strip(), "infini_index": infini_index.strip(),
             "count": count.strip(), "agenda": agenda.strip(), "members": members.strip(), "space": space.strip(),
             "trials": trials.strip(), "seconds": seconds.strip(), "language": language.strip(), "venue": venue.strip(), "exclude": exclude.strip(), "theorem": theorem.strip(),
             "attempts": attempts.strip(), "codebook": codebook.strip(),
             "power": power.strip(), "effect": effect.strip(), "by_dataset": by_dataset.strip(), "call": call.strip(),
             "kinds": kinds.strip(), "outputs": outputs.strip(), "criteria": criteria.strip(),
             "raters": raters.strip(), "responses": responses.strip(), "works": works.strip(),
             "screeners": screeners.strip(), "author": author.strip(),
             "work": work.strip(), "code_dir": code_dir.strip(), "audio": audio.strip()}
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
            "reviewers": {"description": "How many reviewers paper_review runs, 1 to 5, each with its own focus, "
                                         "followed by an area chair; empty is 3"},
            "other_projects": {"description": "research_map reads the findings of these project folders too, comma "
                                              "separated; OpenProgram's projects are read anyway"},
            "question": {"description": "The question consensus answers from the results, thematic_analysis from the "
                                        "transcripts or meta_analysis from published works, in one sentence"},
            "outputs": {"description": "human_eval_build's system outputs: JSONL or CSV with item id, input, system, output"},
            "criteria": {"description": "human_eval_build's criteria, comma separated; empty drafts them from the claims"},
            "raters": {"description": "Raters human_eval_build plans for; empty estimates them from the pilot"},
            "responses": {"description": "human_eval_analyze's answer CSVs downloaded from the survey: comma list or folder"},
            "works": {"description": "meta_analysis' candidate works: arXiv ids, DOIs or PDFs comma separated, or a .bib; "
                                    "empty is the project's bibliography and survey"},
            "screeners": {"description": "meta_analysis' independent screeners: 2 (default) or 3, or 2-3 model names"},
            "runs": {"description": "How many independent analyses consensus runs, 2 to 7; empty is 3"},
            "data": {"description": "Data files inside the project consensus reads besides workflow/, comma separated"},
            "own_papers": {"description": "The author's earlier papers text_overlap compares with (.tex, .pdf, .txt "
                                          "or folders), comma separated, besides workflow/own_papers/"},
            "rounds": {"description": "Evolution rounds of idea_generation, 0 to 5 (AI co-scientist's loop), empty is 0; "
                                      "discussion rounds of lab_meeting, 1 to 5, empty is 2"},
            "population": {"description": "Most ideas idea_generation keeps alive across rounds, up to 40; empty is count"},
            "calibration": {"description": "openreview (default) shows paper_review's reviewers real reviews of similar "
                                           "papers and fits the acceptance estimate on them; off uses no network"},
            "stats_mode": {"description": "tests (default: t-tests and bootstrap CIs across seeds) or popper (falsification experiments combined by e-values)"},
            "alpha": {"description": "Significance level of claim_stats and power_analysis; empty is 0.05"},
            "power": {"description": "power_analysis' target power; empty is 0.8"},
            "effect": {"description": "Smallest difference power_analysis should detect, absolute (0.002) or relative "
                                     "(5%); empty is the claimed difference"},
            "by_dataset": {"description": "yes makes source_independence also group works built on the same dataset"},
            "call": {"description": "call_requirements' funding call: a URL or a local PDF, HTML or text file"},
            "kinds": {"description": "paper_statements' statements, comma separated from availability, credit, "
                                    "interests, funding, ethics; empty is what the venue requires plus availability"},
            "budget": {"description": "How many hypotheses data_explore tests; empty is 12"},
            "components": {"description": "ablation_target's components: name=file:start-end or function names, comma separated; empty lets it propose them"},
            "queries": {"description": "topic_trends' queries, separated by ;; empty reads them from the project"},
            "since": {"description": "First year topic_trends counts (empty is 2016), or the date missing_baselines searches from (empty is three years back)"},
            "text_field": {"description": "The text column of the test data contamination checks; empty finds the longest"},
            "ngram": {"description": "Window length in tokens for contamination; empty is 13"},
            "sample": {"description": "How many test items contamination checks; empty is 200"},
            "infini_index": {"description": "infini-gram indexes contamination queries, comma separated; empty is v4_rpj_llama_s4"},
            "work": {"description": "The published paper after_release follows: DOI, arXiv id or OpenAlex id; empty reads "
                                    "Published: in project.md. The work deep_read reads: arXiv id, DOI or PDF path"},
            "author": {"description": "publication_list's author: a name, ORCID iD, OpenAlex author id or DBLP pid; "
                                      "empty is the paper's first author"},
            "code_dir": {"description": "The code repository paper_agent wraps, code_wiki documents, code_origin checks or "
                                        "software_citation describes; empty is the "
                                        "project or rules.json's code repo"},
            "audio": {"description": "yes makes talk_prep record the questions with macOS say for practice"},
            "funder": {"description": "The funder grant writes for: nsf, nih_r01, erc, nsfc, nsfc_youth or generic; "
                                      "for funding_calls a source (us, eu) or a funder name to filter by, empty is all"},
            "rerun": {"description": "Which kept runs clean_rerun reruns: best (default), all, or commits, comma separated"},
            "tolerance": {"description": "clean_rerun's tolerance: 1% relative or 0.002 absolute; empty is experiment.md's Tolerance or 1%"},
            "host": {"description": "The ssh host remote_run runs on"},
            "remote_command": {"description": "The command remote_run runs; empty is experiment.md's Command"},
            "scheduler": {"description": "ssh (default) or slurm for remote_run"},
            "include": {"description": "Globs anonymous_package bundles, comma separated; empty is the repository minus the paper"},
            "anonymize": {"description": "list (default) reports names and paths and fails; replace writes Anonymous over them"},
            "target_venue": {"description": "The venue venue_move moves to: iclr, neurips, icml, acl, cvpr, eccv, aaai, ijcai, acm, ieee, tmlr, jmlr or arxiv"},
            "write_venue": {"description": "yes makes venue_move write the new workflow/venue.md, keeping the old one"},
            "arxiv_id": {"description": "The paper's own arXiv id, so related_by_paragraph gets recommendations for it"},
            "publish": {"description": "yes makes project_page copy the page to docs/"},
            "media_kind": {"description": "media makes a podcast or a video; empty is podcast"},
            "minutes": {"description": "About how long the podcast or video runs; empty is 5"},
            "count": {"description": "How many ideas facet_ideas writes up (empty is 6), or how many people "
                                     "peer_feedback drafts requests for (empty is 5), or how many calls funding_calls lists or stronger works missing_baselines lists per benchmark (empty is 10)"},
            "theorem": {"description": "lean_prove proves only these declarations or paper labels, comma separated; "
                                       "empty is every sorry"},
            "attempts": {"description": "Prover rounds per declaration in lean_prove, 1 to 10; empty is 4"},
            "codebook": {"description": "thematic_analysis's codebook file; empty uses workflow/codebook.md when present"},
            "exclude": {"description": "People peer_feedback never proposes: names or OpenAlex ids, comma separated"},
            "agenda": {"description": "The question lab_meeting discusses, in one or two sentences"},
            "members": {"description": "lab_meeting's specialist roles, comma separated; empty lets the model pick "
                                       "three (a scientific critic always joins)"},
            "space": {"description": "hparam_sweep's search space: name=low..high (add log for a log scale) or "
                                     "name=a,b,c, separated by ;"},
            "trials": {"description": "How many trials hparam_sweep runs, 1 to 200; empty is 12"},
            "seconds": {"description": "hparam_sweep's total time budget in seconds; empty is 300"},
            "language": {"description": "paper_translate's target language: zh (default), zh-TW, ja, ko, de, fr, es, "
                                        "it or pt"},
            "venue": {"description": "impact_forecast's venue; empty reads venue.md"},
            "min_shared": {"description": "citation_graph lists a work you do not cite when at least this many of "
                                          "your references cite it; empty is 3"},
        },
    },
}
