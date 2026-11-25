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

from . import brief, checks, release, stages
from .tasks.citations import citation_check
from .tasks.figures import paper_figures
from .tasks.figures import scripts as figure_scripts
from .tasks.campaign import campaign_step
from .tasks.consensus import consensus_analysis
from .tasks.grant import grant_proposal
from .tasks.lineage import missing_baselines
from .tasks.resultnumbers import result_numbers
from .tasks.citationcff import software_citation
from .tasks.codeorigin import code_origin
from .tasks.publist import publication_list
from .tasks.codewiki import code_wiki
from .tasks.humaneval import human_eval_analyze, human_eval_build
from .tasks.metaanalysis import meta_analysis
from .tasks.baselinecode import baseline_code
from .tasks.funding import call_requirements, funding_calls
from .tasks.independence import source_independence
from .tasks.power import power_analysis
from .tasks.statements import paper_statements
from .tasks.deepread import deep_read
from .tasks.diagrams import caption_check, method_diagram
from .tasks.discovery import ablation_target, data_explore
from .tasks.facets import facet_ideas
from .tasks.feedback import peer_feedback
from .tasks.ideas import idea_generation
from .tasks.impact import impact_forecast, tech_transfer
from .tasks.integrity import contamination_check, hidden_text_check, image_reuse
from .tasks.labmeeting import lab_meeting
from .tasks.library import citation_graph
from .tasks.media import paper_media
from .tasks.methods import claim_stats, method_lint
from .tasks.overlap import text_overlap
from .tasks.outreach import croissant_check, lean_check, lean_prove, project_page
from .tasks.present import paper_agent, talk_prep
from .tasks.proofread import paper_proofread
from .tasks.qualitative import thematic_analysis
from .tasks.provenance import writing_provenance
from .tasks.reading import baseline_numbers, related_by_paragraph, skim_test
from .tasks.reproduce import clean_rerun, remote_run, settings_check
from .tasks.scouting import after_release, personal_rank, topic_trends, venue_pick
from .tasks.style import paper_translate, writing_style
from .tasks.sweep import hparam_sweep
from .tasks.ideas.workflow import gather as gather_gaps
from .tasks.review import paper_review
from .tasks.revision import paper_revision_check
from .tasks.revision.rules import paper as paper_rules
from .tasks.submission import ai_disclosure, arxiv_package, submission_check
from .tasks.survey import research_survey
from .tasks.venue import anonymous_package, camera_ready_check, submission_metadata, venue_move
from .tasks.watch import related_work_watch
from .tasks.worldmap import research_map


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
    kinds: tuple[str, ...] = ()                       # kinds of work it serves (brief.KINDS); () serves every kind
    goal: str = ""                                    # when it passes, for the author (Chinese)
    brief: str = ""                                   # how the agent of its page does the work
    approves: str = ""                                # the file under workflow/ the author approves ({section})


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
    if brief.read(paper)["kind"] in brief.CITES_WORKS:      # a survey or position paper cites works as evidence
        evidence = {**dict.fromkeys(stages.bib_keys(paper)), **evidence}
    return stages.plan(paper, inputs["section"], created, claims, evidence)


def gate_code(paper, created, inputs):
    return release.code_release(paper, paper_rules.load(paper).code)


def gate_campaign(paper, created, inputs):
    from .tasks.campaign import workflow as campaign
    return campaign.problems(campaign.read_spec(paper), schedule=True)


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


def own(check):
    """A gate from a check of checks.py, which reads only the paper and its own files."""
    return lambda paper, created, inputs: check(paper, created)


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
    Gate("experiment", own(checks.experiment),
         "Write the loop's spec in workflow/experiment.md (files to edit, command, metric, direction, budget, "
         "stop rule) and have the author approve it; log every run as a row of workflow/results.tsv."),
    Gate("campaign", gate_campaign,
         "Write workflow/campaign.md (tasks, agent work, Every, Budget, Until) and have the author approve it."),
    Gate("experiment_plan", own(checks.experiment_plan),
         "List in workflow/experiment_plan.csv the experiments each claim needs (main, ablation, robustness, "
         "significance), and point each done one at its evidence.csv row."),
    Gate("baselines", own(checks.baselines),
         "Run each baseline of workflow/baselines.csv in its reported setting, record the number and its log, "
         "and mark it reproduced, gap or failed."),
    Gate("findings", own(checks.findings),
         "Record each hypothesis tested in workflow/findings.md with its run, result and verdict."),
    Gate("claim_support", own(checks.claim_support),
         "Set each claim against at least two works in workflow/claim_support.csv, quoting the sentence that "
         "supports or contradicts it."),
    Gate("literature_table", own(checks.literature_table),
         "Fill workflow/literature_table.csv: one row per compared work, every cell from the work itself."),
    Gate("taxonomy", own(checks.taxonomy),
         "Write the survey's categories in workflow/taxonomy.md and place every surveyed work in one."),
    Gate("rebuttal", own(checks.rebuttal),
         "Paste the reviews into workflow/rebuttal/reviews.md, answer every concern in concerns.csv, write "
         "the response per reviewer within the limit and have the author approve it."),
    Gate("datasheet", own(checks.datasheet),
         "Write the dataset's license, location and every part of workflow/datasheet.md."),
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
                                      rounds=inputs.get("rounds") or "0", population=inputs.get("population") or "0",
                                      out_dir=str(paper / "drafts" / "idea_generation")))


def run_paper_review(paper: Path, inputs: dict) -> dict:
    """paper_review on the draft, or the working tree, with the reviewer on reviewer_model
    when one is named; reviews go to drafts/paper_review/, where the last one is the previous round."""
    return json.loads(paper_review(str(paper), draft_dir=inputs.get("draft_dir", ""),
                                   reviewer_model=inputs.get("reviewer_model", ""),
                                   reviewers=inputs.get("reviewers") or "3",
                                   calibration=inputs.get("calibration") or "openreview",
                                   out_dir=str(paper / "drafts" / "paper_review")))


def run_paper_figures(paper: Path, inputs: dict) -> dict:
    """paper_figures on the draft, or the working tree; reports go to drafts/paper_figures/."""
    return json.loads(paper_figures(str(paper), draft_dir=inputs.get("draft_dir", ""),
                                    out_dir=str(paper / "drafts" / "paper_figures")))


def run_watch(paper: Path, inputs: dict) -> dict:
    """related_work_watch on the project; its trail goes to drafts/watch/."""
    return json.loads(related_work_watch(str(paper), out_dir=str(paper / "drafts" / "watch")))


def run_paper_proofread(paper: Path, inputs: dict) -> dict:
    """paper_proofread on the draft, or the working tree; reports go to drafts/paper_proofread/."""
    return json.loads(paper_proofread(str(paper), draft_dir=inputs.get("draft_dir", ""),
                                      out_dir=str(paper / "drafts" / "paper_proofread")))


def run_citation_graph(paper: Path, inputs: dict) -> dict:
    """citation_graph on the paper's bibliography; reports go to drafts/citation_graph/."""
    return json.loads(citation_graph(str(paper), min_shared=inputs.get("min_shared") or "3",
                                     out_dir=str(paper / "drafts" / "citation_graph")))


def run_submission_check(paper: Path, inputs: dict) -> dict:
    """submission_check on the compiled paper; reports go to drafts/submission_check/."""
    return json.loads(submission_check(str(paper), out_dir=str(paper / "drafts" / "submission_check")))


def run_arxiv_package(paper: Path, inputs: dict) -> dict:
    """arxiv_package of the paper's sources; the bundle goes to drafts/arxiv_package/."""
    return json.loads(arxiv_package(str(paper), out_dir=str(paper / "drafts" / "arxiv_package")))


def run_ai_disclosure(paper: Path, inputs: dict) -> dict:
    """ai_disclosure drafted from the project's runs and commits; it goes to drafts/ai_disclosure/."""
    return json.loads(ai_disclosure(str(paper), out_dir=str(paper / "drafts" / "ai_disclosure")))


def run_research_map(paper: Path, inputs: dict) -> dict:
    """research_map of the project, with the findings of other projects; workflow/world.json is kept."""
    return json.loads(research_map(str(paper), other_projects=inputs.get("other_projects", ""),
                                   out_dir=str(paper / "drafts" / "research_map")))


def run_consensus(paper: Path, inputs: dict) -> dict:
    """consensus_analysis of the question over the project's results, by independent runs."""
    return json.loads(consensus_analysis(str(paper), inputs["question"], runs=inputs.get("runs") or "3",
                                         data=inputs.get("data", ""),
                                         out_dir=str(paper / "drafts" / "consensus_analysis")))


def run_text_overlap(paper: Path, inputs: dict) -> dict:
    """text_overlap of the paper against the author's earlier papers and the cited works."""
    return json.loads(text_overlap(str(paper), own_papers=inputs.get("own_papers", ""),
                                   out_dir=str(paper / "drafts" / "text_overlap")))


def run_paper_media(paper: Path, inputs: dict) -> dict:
    """paper_media: a two-host podcast or a narrated slide video of the paper."""
    return json.loads(paper_media(str(paper), kind=inputs.get("media_kind") or "podcast",
                                  minutes=inputs.get("minutes") or "5", out_dir=str(paper / "drafts" / "paper_media")))


def run_campaign(paper: Path, inputs: dict) -> dict:
    """One step of the unattended campaign of workflow/campaign.md (the scheduler calls it)."""
    result = json.loads(campaign_step(str(paper)))
    status = {"RAN": "PASS", "STOPPED": "PASS_WITH_NOTES"}.get(result["status"], "FAIL")
    return dict(result, status=status, campaign=result["status"], report=None)


def drafts(paper: Path, name: str) -> str:
    return str(paper / "drafts" / name)


def run_clean_rerun(paper: Path, inputs: dict) -> dict:
    """clean_rerun of the kept runs in a fresh clone and environment."""
    return json.loads(clean_rerun(str(paper), runs=inputs.get("rerun") or "best", tolerance=inputs.get("tolerance", ""),
                                  out_dir=drafts(paper, "clean_rerun")))


def run_remote_run(paper: Path, inputs: dict) -> dict:
    """remote_run of experiment.md's command at HEAD on a remote machine, over ssh or Slurm."""
    return json.loads(remote_run(str(paper), inputs["host"], command=inputs.get("remote_command", ""),
                                 scheduler=inputs.get("scheduler") or "ssh", out_dir=drafts(paper, "remote_run")))


def run_settings_check(paper: Path, inputs: dict) -> dict:
    """settings_check: the paper's reported settings against the code and configs."""
    return json.loads(settings_check(str(paper), out_dir=drafts(paper, "settings_check")))


def run_baseline_numbers(paper: Path, inputs: dict) -> dict:
    """baseline_numbers: the numbers the paper cites from other works against those works."""
    return json.loads(baseline_numbers(str(paper), out_dir=drafts(paper, "baseline_numbers")))


def run_related_by_paragraph(paper: Path, inputs: dict) -> dict:
    """related_by_paragraph: uncited related works for each paragraph."""
    return json.loads(related_by_paragraph(str(paper), arxiv_id=inputs.get("arxiv_id", ""),
                                           out_dir=drafts(paper, "related_by_paragraph")))


def run_skim_test(paper: Path, inputs: dict) -> dict:
    """skim_test: what a reader of the title, abstract, captions and first sentences takes away."""
    return json.loads(skim_test(str(paper), out_dir=drafts(paper, "skim_test")))


def run_lean_check(paper: Path, inputs: dict) -> dict:
    """lean_check: the Lean project of workflow/lean/ against the paper's theorems."""
    return json.loads(lean_check(str(paper), out_dir=drafts(paper, "lean_check")))


def run_camera_ready(paper: Path, inputs: dict) -> dict:
    """camera_ready_check of the de-anonymized final version."""
    return json.loads(camera_ready_check(str(paper), out_dir=drafts(paper, "camera_ready_check")))


def run_anonymous_package(paper: Path, inputs: dict) -> dict:
    """anonymous_package: the supplementary code without names, paths, history or outputs."""
    return json.loads(anonymous_package(str(paper), include=inputs.get("include", ""),
                                        fix=inputs.get("anonymize") or "list", out_dir=drafts(paper, "anonymous_package")))


def run_venue_move(paper: Path, inputs: dict) -> dict:
    """venue_move: the plan for moving the paper to another venue's template."""
    return json.loads(venue_move(str(paper), inputs["target_venue"], write_venue=inputs.get("write_venue") or "no",
                                 out_dir=drafts(paper, "venue_move")))


def run_submission_metadata(paper: Path, inputs: dict) -> dict:
    """submission_metadata: TL;DR, keywords, primary area and lay summary for the form."""
    return json.loads(submission_metadata(str(paper), out_dir=drafts(paper, "submission_metadata")))


def run_croissant(paper: Path, inputs: dict) -> dict:
    """croissant_check of workflow/croissant.json, with the RAI fields."""
    return json.loads(croissant_check(str(paper), out_dir=drafts(paper, "croissant_check")))


def run_project_page(paper: Path, inputs: dict) -> dict:
    """project_page: the page and social thread, every number from the paper."""
    return json.loads(project_page(str(paper), publish=inputs.get("publish") or "no", out_dir=drafts(paper, "project_page")))


def run_grant_proposal(paper: Path, inputs: dict) -> dict:
    """grant_proposal: a proposal in the funder's form from the project's claims and results."""
    return json.loads(grant_proposal(str(paper), inputs["funder"], out_dir=drafts(paper, "grant_proposal")))


def run_method_lint(paper: Path, inputs: dict) -> dict:
    """method_lint: leakage, unseeded randomness and other method errors in the experiment code."""
    return json.loads(method_lint(str(paper), out_dir=drafts(paper, "method_lint")))


def run_claim_stats(paper: Path, inputs: dict) -> dict:
    """claim_stats: each claim's comparison tested across seeds, or falsified in popper mode."""
    return json.loads(claim_stats(str(paper), mode=inputs.get("stats_mode") or "tests",
                                  alpha=inputs.get("alpha") or "0.05", out_dir=drafts(paper, "claim_stats")))


def run_data_explore(paper: Path, inputs: dict) -> dict:
    """data_explore: hypotheses from the data, ranked by how much the analysis changed belief."""
    return json.loads(data_explore(str(paper), inputs["data"], budget=inputs.get("budget") or "12",
                                   out_dir=drafts(paper, "data_explore")))


def run_ablation_target(paper: Path, inputs: dict) -> dict:
    """ablation_target: each component ablated in its own worktree, ranked by its effect."""
    return json.loads(ablation_target(str(paper), components=inputs.get("components", ""),
                                      out_dir=drafts(paper, "ablation_target")))


def run_topic_trends(paper: Path, inputs: dict) -> dict:
    """topic_trends: yearly OpenAlex counts per query, labelled rising, crowded, quiet or steady."""
    return json.loads(topic_trends(str(paper), queries=inputs.get("queries", ""), since=inputs.get("since") or "2016",
                                   out_dir=drafts(paper, "topic_trends")))


def run_personal_rank(paper: Path, inputs: dict) -> dict:
    """personal_rank: watch.csv and today's arXiv ranked by a model of the author's decisions."""
    return json.loads(personal_rank(str(paper), out_dir=drafts(paper, "personal_rank")))


def run_contamination(paper: Path, inputs: dict) -> dict:
    """contamination_check: test items found in pretraining corpora through infini-gram."""
    return json.loads(contamination_check(str(paper), inputs["data"], field=inputs.get("text_field", ""),
                                          n=inputs.get("ngram") or "13", sample=inputs.get("sample") or "200",
                                          index=inputs.get("infini_index") or "v4_rpj_llama_s4",
                                          out_dir=drafts(paper, "contamination_check")))


def run_hidden_text(paper: Path, inputs: dict) -> dict:
    """hidden_text_check: text in the PDF or source a reader cannot see, and prompts aimed at AI reviewers."""
    return json.loads(hidden_text_check(str(paper), out_dir=drafts(paper, "hidden_text_check")))


def run_image_reuse(paper: Path, inputs: dict) -> dict:
    """image_reuse: the paper's figures against each other and the author's earlier papers."""
    return json.loads(image_reuse(str(paper), own_papers=inputs.get("own_papers", ""), out_dir=drafts(paper, "image_reuse")))


def run_venue_pick(paper: Path, inputs: dict) -> dict:
    """venue_pick: where the references and similar works are published, with next deadlines."""
    return json.loads(venue_pick(str(paper), out_dir=drafts(paper, "venue_pick")))


def run_after_release(paper: Path, inputs: dict) -> dict:
    """after_release: citing works with their stance, and public comments, new since the last run."""
    return json.loads(after_release(str(paper), work=inputs.get("work", ""), out_dir=drafts(paper, "after_release")))


def run_paper_agent(paper: Path, inputs: dict) -> dict:
    """paper_agent: the method as an MCP server whose tests reproduce the paper's results."""
    return json.loads(paper_agent(str(paper), code_dir=inputs.get("code_dir", ""), out_dir=drafts(paper, "paper_agent")))


def run_talk_prep(paper: Path, inputs: dict) -> dict:
    """talk_prep: the audience's likely questions with answers grounded in the paper."""
    return json.loads(talk_prep(str(paper), minutes=inputs.get("minutes", ""), audio=inputs.get("audio") or "no",
                                out_dir=drafts(paper, "talk_prep")))


def run_facet_ideas(paper: Path, inputs: dict) -> dict:
    """facet_ideas: purpose, mechanism and evaluation of the paper and its references, recombined."""
    return json.loads(facet_ideas(str(paper), count=inputs.get("count") or "6", out_dir=drafts(paper, "facet_ideas")))


def run_deep_read(paper: Path, inputs: dict) -> dict:
    """deep_read: one work at five zoom levels, its derivations step by step, its references by your history."""
    return json.loads(deep_read(str(paper), inputs["work"], out_dir=drafts(paper, "deep_read")))


def run_lab_meeting(paper: Path, inputs: dict) -> dict:
    """lab_meeting: specialists and a critic discuss the agenda over the project's files."""
    return json.loads(lab_meeting(str(paper), inputs["agenda"], rounds=inputs.get("rounds") or "2",
                                  members=inputs.get("members", ""), out_dir=drafts(paper, "lab_meeting")))


def run_hparam_sweep(paper: Path, inputs: dict) -> dict:
    """hparam_sweep: Bayesian-optimisation trials of experiment.md's Command, each a results.tsv row."""
    return json.loads(hparam_sweep(str(paper), inputs["space"], trials=inputs.get("trials") or "12",
                                   budget=inputs.get("seconds") or "300", out_dir=drafts(paper, "hparam_sweep")))


def run_writing_style(paper: Path, inputs: dict) -> dict:
    """writing_style: the author's style from earlier papers, and exact edits toward it."""
    return json.loads(writing_style(str(paper), own_papers=inputs.get("own_papers", ""),
                                    out_dir=drafts(paper, "writing_style")))


def run_paper_translate(paper: Path, inputs: dict) -> dict:
    """paper_translate: the whole LaTeX paper translated and compiled, structure untouched."""
    return json.loads(paper_translate(str(paper), language=inputs.get("language") or "zh",
                                      out_dir=drafts(paper, "paper_translate")))


def run_method_diagram(paper: Path, inputs: dict) -> dict:
    """method_diagram: the method section as an editable TikZ diagram."""
    return json.loads(method_diagram(str(paper), section=inputs.get("section", ""), out_dir=drafts(paper, "method_diagram")))


def run_caption_check(paper: Path, inputs: dict) -> dict:
    """caption_check: every caption readable on its own, labelled and referenced; plotting scripts fit to print."""
    return json.loads(caption_check(str(paper), out_dir=drafts(paper, "caption_check")))


def run_impact_forecast(paper: Path, inputs: dict) -> dict:
    """impact_forecast: citations at 1-3 years estimated from comparable published works."""
    return json.loads(impact_forecast(str(paper), venue=inputs.get("venue", ""), out_dir=drafts(paper, "impact_forecast")))


def run_tech_transfer(paper: Path, inputs: dict) -> dict:
    """tech_transfer: technology readiness level and the patent landscape."""
    return json.loads(tech_transfer(str(paper), out_dir=drafts(paper, "tech_transfer")))


def run_peer_feedback(paper: Path, inputs: dict) -> dict:
    """peer_feedback: human readers of the field without conflicts, with request drafts never sent."""
    return json.loads(peer_feedback(str(paper), count=inputs.get("count") or "5", exclude=inputs.get("exclude", ""),
                                    out_dir=drafts(paper, "peer_feedback")))


def run_writing_provenance(paper: Path, inputs: dict) -> dict:
    """writing_provenance: who wrote each paragraph, from the Git history and the runs."""
    return json.loads(writing_provenance(str(paper), out_dir=drafts(paper, "writing_provenance")))


def run_lean_prove(paper: Path, inputs: dict) -> dict:
    """lean_prove: each sorry of workflow/lean/ proved by a model and checked by Lean."""
    return json.loads(lean_prove(str(paper), theorem=inputs.get("theorem", ""), attempts=inputs.get("attempts") or "4",
                                 out_dir=drafts(paper, "lean_prove")))


def run_thematic_analysis(paper: Path, inputs: dict) -> dict:
    """thematic_analysis: interview data coded by two blind coders, agreement measured, themes found."""
    return json.loads(thematic_analysis(str(paper), inputs["data"], question=inputs.get("question", ""),
                                        codebook=inputs.get("codebook", ""), out_dir=drafts(paper, "thematic_analysis")))


def run_power_analysis(paper: Path, inputs: dict) -> dict:
    """power_analysis: seeds each claimed comparison needs, from the pilot runs."""
    return json.loads(power_analysis(str(paper), alpha=inputs.get("alpha") or "0.05", power=inputs.get("power") or "0.8",
                                     effect=inputs.get("effect", ""), out_dir=drafts(paper, "power_analysis")))


def run_source_independence(paper: Path, inputs: dict) -> dict:
    """source_independence: independent groups of supporting works per claim."""
    return json.loads(source_independence(str(paper), by_dataset=inputs.get("by_dataset") or "no",
                                          out_dir=drafts(paper, "source_independence")))


def run_baseline_code(paper: Path, inputs: dict) -> dict:
    """baseline_code: each baseline's repository, official or not."""
    return json.loads(baseline_code(str(paper), out_dir=drafts(paper, "baseline_code")))


def run_funding_calls(paper: Path, inputs: dict) -> dict:
    """funding_calls: open calls that fit the project."""
    return json.loads(funding_calls(str(paper), funder=inputs.get("funder", ""), count=inputs.get("count") or "10",
                                    out_dir=drafts(paper, "funding_calls")))


def run_call_requirements(paper: Path, inputs: dict) -> dict:
    """call_requirements: one call's requirements, checked against the grant draft."""
    return json.loads(call_requirements(str(paper), inputs["call"], out_dir=drafts(paper, "call_requirements")))


def run_paper_statements(paper: Path, inputs: dict) -> dict:
    """paper_statements: availability, contribution, interests, funding and ethics statements."""
    return json.loads(paper_statements(str(paper), kinds=inputs.get("kinds", ""), out_dir=drafts(paper, "paper_statements")))


def run_human_eval_build(paper: Path, inputs: dict) -> dict:
    """human_eval_build: a blind survey of system outputs, piloted by a model rater."""
    return json.loads(human_eval_build(str(paper), inputs["outputs"], criteria=inputs.get("criteria", ""),
                                       raters=inputs.get("raters", ""), out_dir=drafts(paper, "human_eval_build")))


def run_human_eval_analyze(paper: Path, inputs: dict) -> dict:
    """human_eval_analyze: agreement and win rates from the survey's answers."""
    return json.loads(human_eval_analyze(str(paper), inputs["responses"], out_dir=drafts(paper, "human_eval_analyze")))


def run_meta_analysis(paper: Path, inputs: dict) -> dict:
    """meta_analysis: published results pooled for one question, certainty graded."""
    return json.loads(meta_analysis(str(paper), inputs["question"], works=inputs.get("works", ""),
                                    screeners=inputs.get("screeners") or "2", out_dir=drafts(paper, "meta_analysis")))


def run_code_wiki(paper: Path, inputs: dict) -> dict:
    """code_wiki: module docs tying the paper's method to the implementing functions."""
    return json.loads(code_wiki(str(paper), code_dir=inputs.get("code_dir", ""), out_dir=drafts(paper, "code_wiki")))


def run_code_origin(paper: Path, inputs: dict) -> dict:
    """code_origin: copied files of the released code, their origin, license and credit."""
    return json.loads(code_origin(str(paper), code_dir=inputs.get("code_dir", ""), out_dir=drafts(paper, "code_origin")))


def run_software_citation(paper: Path, inputs: dict) -> dict:
    """software_citation: CITATION.cff, .zenodo.json and codemeta.json, and the archive DOI."""
    return json.loads(software_citation(str(paper), code_dir=inputs.get("code_dir", ""),
                                        out_dir=drafts(paper, "software_citation")))


def run_publication_list(paper: Path, inputs: dict) -> dict:
    """publication_list: the author's papers from OpenAlex, ORCID and DBLP, merged once each."""
    return json.loads(publication_list(str(paper), author=inputs.get("author", ""),
                                       out_dir=drafts(paper, "publication_list")))


def run_result_numbers(paper: Path, inputs: dict) -> dict:
    """result_numbers: the paper's own numbers and derived claims against the runs."""
    return json.loads(result_numbers(str(paper), out_dir=drafts(paper, "result_numbers")))


def run_missing_baselines(paper: Path, inputs: dict) -> dict:
    """missing_baselines: uncited works with stronger results on the paper's benchmarks."""
    return json.loads(missing_baselines(str(paper), since=inputs.get("since", ""), count=inputs.get("count") or "10",
                                        out_dir=drafts(paper, "missing_baselines")))


ORDER = ("PASS", "PASS_WITH_NOTES", "FAIL")


def worst(statuses) -> str:
    return max(statuses, key=lambda s: ORDER.index(s) if s in ORDER else len(ORDER))


FIX = ("Fix every FAIL and REVIEW, then show the author the clean and red/green PDFs. Checked, "
       "correct fixes are written into the paper and committed.")

TASKS = (
    # Research.
    Task("survey", "调研材料", "research", gates=("survey",),
         artifacts=("venue.md", "rules.json", "literature.md"),
         goal="投稿要求和规则写齐，调研过的工作都在参考文献里。",
         brief="Read the call for papers the author gives (or find it), write venue.md and rules.json from it, "
               "and list the works the paper builds on in literature.md by their BibTeX keys."),
    Task("field_survey", "领域综述", "research", artifacts=("surveys/",),
         inputs=("direction", "reader_level", "purpose", "our_work", "preset"), required=("direction",),
         worker=Worker("research_survey", run_research_survey), kinds=("survey", "position"),
         done="Open survey.html and read report.md; audit issues it lists are fixed in a new run, not by hand.",
         goal="对一个方向的综述跑完，审核没有问题。",
         brief="Help the author pick the direction and reader level, run the check to survey it, then read the "
               "report with them."),
    Task("watch", "新工作跟踪", "research", artifacts=("watch.md", "watch.csv"),
         worker=Worker("related_work_watch", run_watch),
         goal="新出的相关论文都看过，和主张撞车的每一篇都写明怎么处理。",
         brief="Each check searches for new papers related to the claims and judges them. For every paper marked "
               "overlap, read it with the author and write in the handled column of watch.csv what the paper does "
               "about it: cite it, narrow the claim, or say how ours differs. Edit the queries in watch.md if they "
               "miss or over-reach."),
    Task("idea", "想法与研究问题", "research", gates=("idea",), artifacts=("claims.md",), approves="claims.md",
         goal="每条主张都有检验它的研究问题，并由你批准。",
         brief="Write each claim as a result a reader can check, with the research question that tests it, in "
               "claims.md. Keep Approved: no; the author approves from the app."),
    Task("idea_generation", "想法生成", "research", gates=("gaps",), artifacts=("surveys/", "literature.md"),
         inputs=("direction", "rounds", "population"), worker=Worker("idea_generation", run_idea_generation), kinds=("method", "analysis"),
         done="Read ideas.md with the author; an idea they choose becomes a claim in workflow/claims.md, "
              "written and approved through the idea task.",
         goal="从综述的空白里提出想法并查过新颖性。",
         brief="Run the check to propose ideas from the surveyed gaps, then go through ideas.md with the author; "
               "a chosen idea becomes a claim on the idea task's page."),
    Task("facet_ideas", "组合式想法", "research", inputs=("count",),
         worker=Worker("facet_ideas", run_facet_ideas),
         done="Copy the ideas worth pursuing into literature.md's ideas, then run 想法生成 to rank them with the rest.",
         goal="把本文和参考文献拆成目的、方法、评测三个面重新组合，得到查过新颖性的新想法。",
         brief="Run the task, then go through ideas.md with the author: which facet came from which work, the novelty "
               "check, and whether the idea is worth a run of 想法生成 or an experiment."),
    Task("deep_read", "论文精读", "research", inputs=("work",), required=("work",),
         worker=Worker("deep_read", run_deep_read),
         done="Note what the work changes for your paper in findings.md or the related work, and cite the new "
              "references you need.",
         goal="一篇论文从一句话到逐段的五层读法、公式推导逐步讲清，参考文献标出你读过、引过和没见过的。",
         brief="Run the task on an arXiv id, DOI or PDF, then read read.html with the author from the top level down; "
               "explain any derivation step they ask about from the work's own equations."),
    Task("source_independence", "支持文献独立性", "research", inputs=("by_dataset",),
         worker=Worker("source_independence", run_source_independence),
         done="For a claim resting on one group, cite an independent work that supports it, or soften the claim.",
         goal="每条主张至少有两组彼此独立（不同作者群）的支持文献。",
         brief="Run the audit after 主张与文献对照; for each claim supported by one group only, search for an "
               "independent work with the author or soften the claim."),
    Task("claim_support", "主张与文献对照", "research", gates=("claim_support",), artifacts=("claim_support.csv",),
         goal="每条主张至少对照两篇文献，有引文；反驳它的文献写明怎么回应。",
         brief="For each claim, find works of the bibliography that support or contradict it, quote the sentence "
               "that bears on it, and for a contradiction write how the paper answers it."),
    Task("literature_table", "文献对比表", "research", gates=("literature_table",), artifacts=("literature_table.csv",),
         goal="相关工作逐篇比较，每一格都来自原文。",
         brief="Agree the columns with the author (task, data, metric, result, setting), then fill one row per "
               "compared work from the work itself; write \"not reported\" where it does not say."),
    Task("taxonomy", "分类体系", "research", gates=("taxonomy",), artifacts=("taxonomy.md",), kinds=("survey",),
         goal="综述的分类写好，每篇调研过的工作都归了类。",
         brief="Propose the survey's categories from literature.md, agree them with the author, and place every "
               "surveyed work in at least one."),
    Task("research_map", "研究地图", "research", inputs=("other_projects",),
         worker=Worker("research_map", run_research_map),
         done="Open map.html with the author: close each gap, and turn the open questions worth answering into "
              "experiments or findings.",
         goal="每条主张都有证据或计划中的实验；研究地图标出还没回答的问题和下一步。",
         brief="Run the check, then go through map.html with the author: the argument map's gaps first (claims without "
               "evidence, evidence for no claim, claims argued nowhere), then the open questions and proposed next "
               "tasks. Findings of the author's other projects are shown; use them, never copy their results. To "
               "bring in the author's own notes, slides and tables, put them in workflow/notes/ and use the "
               "notes_ingest operation; go through the entries it lists as 待确认 with the author and set "
               "approved true in workflow/world.json for the ones they accept."),
    Task("campaign", "无人值守研究", "research", gates=("campaign",), artifacts=("campaign.md", "campaign_log.md"),
         worker=Worker("campaign", run_campaign), approves="campaign.md",
         done="Read campaign_log.md and the commits on the campaign/<date> branches; merge only what you checked.",
         goal="写好并批准运行规则（任务、可做的事、频率、预算、截止），按计划自动跑，到预算或截止就停。",
         brief="Write workflow/campaign.md with the author: the tasks to run each step, what the unattended agent may "
               "do (within experiment.md's Edit rules), how often, the budget in dollars and the end date. The author "
               "approves it and starts it with the button on this page; the scheduler then runs a step on every tick "
               "and stops at the budget or the date. Never start or approve it yourself."),
    Task("lab_meeting", "组会讨论", "research", inputs=("agenda", "rounds", "members"), required=("agenda",),
         worker=Worker("lab_meeting", run_lab_meeting),
         done="Decide on the action items: add the proposed rows to experiment_plan.csv yourself and run the tasks named.",
         goal="几位专家和一位批评者围绕议题讨论，事实都有出处，最后给出决定和可执行的下一步。",
         brief="Help the author (the PI) state one agenda, run the meeting, then go through the summary: accept or "
               "drop each action item, add the proposed experiment rows to experiment_plan.csv only when they agree."),
    Task("thematic_analysis", "质性主题分析", "research", inputs=("data", "question", "codebook"), required=("data",),
         worker=Worker("thematic_analysis", run_thematic_analysis),
         done="Edit workflow/codebook.md where a code is wrong or unclear and run again; quote themes from themes.md.",
         goal="访谈材料按两位独立编码者的结果整理出编码表和主题，编码一致性达标，每个主题都有原话。",
         brief="Run the analysis on the transcripts, then go through codebook.md and the low-agreement codes with the "
               "author; they own the codebook. Themes go into the paper only with their quotes and prevalence."),
    Task("human_eval_build", "人工评测问卷", "research", inputs=("outputs", "criteria", "raters"), required=("outputs",),
         worker=Worker("human_eval_build", run_human_eval_build),
         done="Publish survey.html with the author (they recruit raters, e.g. on Prolific), then run 人工评测分析 on the answers.",
         goal="系统输出做成盲评问卷：顺序和左右随机、带注意力检查和重复题，评分指南经你批准，算好要招多少人。",
         brief="Collect the systems' outputs into one file with the author, run the task, go through eval_guide.md "
               "with them until they approve it, and run again. Publishing and recruiting are the author's."),
    Task("human_eval_analyze", "人工评测分析", "research", inputs=("responses",), required=("responses",),
         artifacts=("human_eval.csv",), worker=Worker("human_eval_analyze", run_human_eval_analyze),
         done="Accept the evidence rows from edits.json with apply_edit; recruit more raters when agreement is low.",
         goal="剔除没通过注意力检查的人，评分一致性达标，每条相关主张都有带置信区间的胜率。",
         brief="Run the analysis on the downloaded answer files; when agreement is below 0.667, revise the guide with "
               "the author rather than dropping raters who disagree."),
    Task("meta_analysis", "荟萃分析", "research", inputs=("question", "works", "screeners"), required=("question",),
         worker=Worker("meta_analysis", run_meta_analysis),
         done="Settle the screeners' disagreements with the author; check flagged outliers against the papers.",
         goal="已发表工作对同一问题报告的数字合并成一个效应，逐篇评估偏倚，给出证据确定性等级。",
         brief="Agree on one answerable question and its comparison with the author, run the task, check each "
               "extracted number and every outlier against its paper, and report the pooled effect with its certainty."),
    Task("consensus", "共识分析", "research", inputs=("question", "runs", "data"), required=("question",),
         worker=Worker("consensus_analysis", run_consensus), kinds=("method", "analysis", "benchmark"),
         done="Write only what the runs agree on into findings.md or the paper, with the rows they cite.",
         goal="几次独立分析对这个问题的结论一致，每个数字都能指到数据里的行。",
         brief="Help the author state one question the results should answer, run the check, and keep only the "
               "consensus with its cited rows; where the runs disagree, say the data does not settle it and propose "
               "the experiment that would."),
    Task("funding_calls", "基金项目检索", "research", inputs=("funder", "count"),
         worker=Worker("funding_calls", run_funding_calls),
         done="Pick a call with the author, then run 申请要求清单 on it before writing the proposal.",
         goal="找出与项目相符、仍开放的资助项目，每个都附原文依据和已资助的相似项目。",
         brief="Run the search, go through calls.csv with the author, and check each kept call's page yourself; "
               "eligibility (career stage, institution, country) is theirs to confirm."),
    Task("call_requirements", "申请要求清单", "research", inputs=("call",), required=("call",),
         artifacts=("call_requirements.json",), worker=Worker("call_requirements", run_call_requirements),
         done="Fix the draft where a checked requirement fails; confirm the listed ones with the author.",
         goal="一个资助项目的要求逐条列出、每条带原文，申请书草稿的页数、字数和必需章节都核对过。",
         brief="Ask for the call's URL or document, run the task, then go through the requirements code could not "
               "check with the author and change the proposal where it falls short."),
    Task("grant", "基金申请书", "research", inputs=("funder",), required=("funder",),
         worker=Worker("grant_proposal", run_grant_proposal),
         done="Rewrite the draft with the author into the funder's own template; check every limit against the current call.",
         goal="按资助机构的格式写出申请书草稿：目标对应到主张和证据，数字都有出处，不超篇幅。",
         brief="Ask which funder and program (NSF, NIH R01, ERC, NSFC 面上 or 青年, or generic), run the task, then go "
               "through proposal.md with the author: preliminary results only from the project's evidence, proposed "
               "work clearly marked, every limit checked against this year's call. Fill the budget justification's TODOs with them."),
    Task("experiment_plan", "实验计划", "research", gates=("experiment_plan",), artifacts=("experiment_plan.csv",),
         kinds=("method", "analysis", "benchmark"),
         goal="每条主张都有要做的实验；方法论文每条主张都有消融；做完的指向证据。",
         brief="From claims.md, list the experiments each claim needs (main comparison, ablation, robustness, "
               "significance) before compute is spent, and keep their status current."),
    Task("experiment_loop", "实验循环", "research", gates=("experiment",),
         artifacts=("experiment.md", "results.tsv"), approves="experiment.md", kinds=("method", "analysis", "benchmark"),
         goal="循环的规格由你批准；每次运行都记在 results.tsv，每条分支保留的每一次都比上一次好，最好的一次合回主线。",
         brief="Write experiment.md with the author first: the only files you may edit, the command, the metric and "
               "its direction, the time budget per run and the stop rule; wait for their approval. Then loop on a "
               "branch: make one change, commit it, run the command within the budget, read the metric, and append "
               "a row to results.tsv (commit, metric, keep/discard/crash, seconds, description). Keep the commit "
               "only if the metric improves on the best kept run; otherwise git reset --hard to it. Stop by the rule "
               "and report the best run. Never change files outside Edit. To explore several directions at once, run "
               "each on its own git branch (results.tsv columns branch and parent), prune branches that stop improving, "
               "and add a merge row for the best kept commit when you bring it back."),
    Task("topic_trends", "选题趋势", "research", inputs=("queries", "since"),
         worker=Worker("topic_trends", run_topic_trends),
         done="Use the labels when choosing among ideas: a crowded topic needs a sharper difference.",
         goal="每个候选方向近几年的发文趋势清楚：升温、拥挤、冷门还是平稳。",
         brief="Run the task on the directions or idea titles (or let it read them from the project), then discuss "
               "the labels with the author when picking what to work on."),
    Task("personal_rank", "新工作排序", "research",
         worker=Worker("personal_rank", run_personal_rank),
         done="Read the top of ranked.md and write your decision in watch.csv's handled column; the model learns from it.",
         goal="按你以往的取舍给新论文排序，最相关的排在最前。",
         brief="Run the task after the author has decided on at least five relevant and five irrelevant papers in "
               "watch.csv; go through the top of ranked.md with them and record each decision in handled."),
    Task("data_explore", "数据探索", "research", inputs=("data", "budget"), required=("data",),
         worker=Worker("data_explore", run_data_explore), kinds=("method", "analysis", "benchmark"),
         done="Copy the surprising findings you trust into findings.md and plan the experiments that would confirm them.",
         goal="从数据本身找出出人意料的发现，每条都有分析脚本和结果。",
         brief="Run the task on the data files, then go through hypotheses.csv by surprise with the author: read each "
               "script and output, and turn the ones worth keeping into findings.md entries or planned experiments."),
    Task("method_lint", "实验方法检查", "research",
         worker=Worker("method_lint", run_method_lint), kinds=("method", "analysis", "benchmark"),
         done="Fix each high finding in the code and rerun the affected experiments; mark false alarms ignore.",
         goal="实验代码没有数据泄漏、在测试集上调参、没固定种子这类方法错误。",
         brief="Run the check, then for each high and medium finding show the author the line, fix it in the code, "
               "and rerun the runs it affects; mark a false alarm ignore in workflow/method_lint.csv."),
    Task("ablation_target", "消融定位", "research", inputs=("components",),
         worker=Worker("ablation_target", run_ablation_target), kinds=("method", "analysis", "benchmark"),
         done="Narrow experiment.md's Edit to the component that matters most, after the author agrees.",
         goal="知道代码里哪一部分对指标影响最大，下一轮集中改它。",
         brief="Run the task (components optional), then show the author the impact ranking and propose narrowing "
               "Edit in experiment.md to the top component; change it only when they agree."),
    Task("hparam_sweep", "超参数搜索", "research", inputs=("space", "trials", "seconds"), required=("space",),
         worker=Worker("hparam_sweep", run_hparam_sweep), kinds=("method", "analysis", "benchmark"),
         done="Rerun the best setting with several seeds and test it with 显著性检验 before it goes into the paper.",
         goal="在给定的参数范围里用贝叶斯优化找到最好的设置，每次试验都记在 results.tsv。",
         brief="Write the search space with the author (name=low..high, log for rates, a,b,c for choices), run the "
               "sweep, then rerun the best setting with more seeds; never report a single-seed winner as a result."),
    Task("power_analysis", "种子数功效分析", "research", inputs=("alpha", "power", "effect"),
         worker=Worker("power_analysis", run_power_analysis), kinds=("method", "analysis", "benchmark"),
         done="Accept the Seeds line from edits.json with apply_edit, or set the effect the claim really needs.",
         goal="跑实验前算出每条主张的对比需要几个种子，试跑的种子数够不够。",
         brief="Make sure there are pilot runs (two seeds or more per side), run the analysis, and agree on the seed "
               "count with the author before spending compute; a claim that needs too many seeds is narrowed."),
    Task("claim_stats", "显著性检验", "research", inputs=("stats_mode", "alpha"),
         worker=Worker("claim_stats", run_claim_stats), kinds=("method", "analysis", "benchmark"),
         done="Run more seeds for claims on one seed or with a CI crossing zero, or soften the claim.",
         goal="每条主张的比较超过了种子噪声：多个种子、置信区间不跨零。",
         brief="Run the tests; for each flagged claim, add seeds with the experiment loop or soften the claim with "
               "the author. In popper mode, run the falsification experiments in workflow/falsification.md, tagged "
               "falsify:<claim>:<n>, and run again."),
    Task("contamination", "基准污染检查", "research", inputs=("data", "text_field", "ngram", "sample", "infini_index"),
         required=("data",), worker=Worker("contamination_check", run_contamination), kinds=("benchmark",),
         done="Put known_contamination.md into the datasheet's limitations and drop or flag contaminated items.",
         goal="知道测试集有多少样本出现在常见预训练语料里，并写进数据说明。",
         brief="Run the check on the test split, then with the author decide whether to drop or flag the contaminated "
               "items, and add the drafted paragraph to the datasheet's limitations."),
    Task("clean_rerun", "干净环境复现", "research", inputs=("rerun", "tolerance"),
         worker=Worker("clean_rerun", run_clean_rerun), kinds=("method", "analysis", "benchmark"),
         done="Fix what made a run differ (an undeclared dependency, a seed, a file outside the repository) and rerun.",
         goal="保留的结果在全新克隆和全新环境里重跑，数字在容差内一致。",
         brief="Run the task: it clones HEAD, builds the environment the repository declares, and reruns the chosen "
               "kept runs of results.tsv. For each run that differs or crashes, read its log, find the cause (missing "
               "dependency, unfixed seed, data outside the repository) and fix it in the repository, then run again."),
    Task("remote_run", "远程运行", "research", inputs=("host", "remote_command", "scheduler"), required=("host",),
         worker=Worker("remote_run", run_remote_run), kinds=("method", "analysis", "benchmark"),
         done="Record the run in results.tsv as the experiment loop does.",
         goal="实验在远程机器或集群上按当前提交跑完，拿回指标和日志。",
         brief="Make sure experiment.md has Remote: (the repository's folder on the host) and HEAD is pushed, then "
               "run the task over ssh or Slurm. Record the metric it returns in results.tsv like any other run. To "
               "bring in runs already logged in W&B or MLflow, use the import_tracking operation."),
    Task("baseline_code", "基线代码", "research", worker=Worker("baseline_code", run_baseline_code),
         kinds=("method", "benchmark"),
         done="Apply the repo edits the author accepts from edits.json with apply_edit, then reproduce from the official code.",
         goal="每个基线都找到代码仓库，标出是否官方，记下许可证、最近更新和是否已归档。",
         brief="Run the search, check unofficial picks with the author, and prefer the official repository even when "
               "a fork has more stars; note the license before copying any code."),
    Task("baselines", "基线复现", "research", gates=("baselines",), artifacts=("baselines.csv",),
         kinds=("method", "benchmark"),
         goal="每个基线都在原设置下跑过，记录数字和日志，标明复现、差距或失败。",
         brief="For each baseline in baselines.csv, get its code, run the reported setting, save the log, and record "
               "the number; mark it reproduced when within tolerance, gap otherwise, failed when it cannot run."),
    Task("experiments", "实验证据", "research", gates=("experiments",), artifacts=("evidence.csv",),
         kinds=("method", "analysis", "benchmark"),
         goal="论文用到的每个数字都在 evidence.csv 里，有来源文件和产生它的脚本。",
         brief="Record every number the paper uses in evidence.csv with its source file and the script that "
               "produced it; never type a number by hand."),
    Task("findings", "研究笔记", "research", gates=("findings",), artifacts=("findings.md",),
         kinds=("method", "analysis", "benchmark"),
         goal="每个检验过的假设都记下了运行、结果和结论，包括被否定的。",
         brief="After each experiment, append an entry to findings.md: the hypothesis, the run that tested it, the "
               "result with numbers and the verdict. Read it before proposing anything, so a refuted idea is not tried again."),
    # Writing.
    Task("plan", "章节计划", "writing", gates=("plan",), artifacts=("plans/",),
         inputs=("section",), required=("section",), approves="plans/{section}.md",
         done="Write the section in a draft copy along the approved plan card, then run paper_check on it.",
         goal="这一节的计划卡写齐（主张、证据、不能说的、段落安排），并由你批准。",
         brief="Fill the section's plan card from the approved claims and the evidence: what the evidence shows, "
               "what it cannot claim, and the storyline. Keep Approved: no; the author approves from the app."),
    Task("paper_check", "论文检查", "writing", gates=("plan",),
         inputs=("section", "draft_dir", "baseline", "scope", "phases"),
         worker=Worker("paper_revision_check", run_paper_check), done=FIX,
         goal="论文按规则和计划卡检查，没有必改的问题。",
         brief="Fix each FAIL and REVIEW of the last check in the paper's source, then run the check again."),
    Task("settings_check", "实验设置核对", "writing",
         worker=Worker("settings_check", run_settings_check), kinds=("method", "analysis", "benchmark"),
         done="Correct the paper or the config for every mismatch; fill code_location for settings it did not find.",
         goal="论文里写的每个超参数和设置都和代码、配置一致。",
         brief="Run the check, then for each mismatch ask the author which is right and correct the paper or the "
               "config. For settings not found, fill code_location in workflow/settings.csv or mark the row ignore."),
    Task("result_numbers", "结果数字核对", "writing", artifacts=("result_numbers.csv",),
         worker=Worker("result_numbers", run_result_numbers),
         done="Apply the edits the author accepts with apply_edit; give each unsourced number an evidence row.",
         goal="论文里自己的结果数字和推导结论都对到产生它的运行：没有过期的、张冠李戴的或按当前结果不再成立的。",
         brief="Run the check after every kept result; go through stale and misattributed numbers and wrong derived "
               "claims with the author and apply the accepted edits; never change a number without its run."),
    Task("missing_baselines", "漏掉的更强基线", "writing", inputs=("since", "count"), artifacts=("missing_baselines.csv",),
         worker=Worker("missing_baselines", run_missing_baselines), kinds=("method", "benchmark"),
         done="Cite and compare against each comparable stronger work, or say in the paper why its setting differs.",
         goal="同一数据集、指标和设置上比本文或最强已引基线更强、却没引用的工作都找出来了。",
         brief="Run the search, check each stronger work's setting against the paper's with the author, import the ones "
               "to cite from candidates.bib with import_library, and add them to the results table or the discussion."),
    Task("baseline_numbers", "基线数字核对", "writing",
         worker=Worker("baseline_numbers", run_baseline_numbers), kinds=("method", "benchmark"),
         done="Correct every mismatched number from the quoted source; consider the better numbers the works report.",
         goal="论文里引用的别人的数字都和原文一致。",
         brief="Run the check, then correct each number that differs from the quoted passage of the cited work. Where "
               "the work reports a better number for the same setting, tell the author and use it unless the setting differs."),
    Task("related_by_paragraph", "逐段文献推荐", "writing", inputs=("arxiv_id",),
         worker=Worker("related_by_paragraph", run_related_by_paragraph),
         done="Read the suggestions with the author; import the works to cite from candidates.bib with import_library.",
         goal="每一段都看过相关但没引用的工作，该引的引上了。",
         brief="Run the task, go through report.md paragraph by paragraph with the author, read the works that fit, "
               "and cite the ones that belong; import them with the import_library operation from candidates.bib."),
    Task("skim_test", "速读测试", "writing",
         worker=Worker("skim_test", run_skim_test),
         done="Rewrite the title, abstract, captions or first sentences so each missed claim is carried.",
         goal="只读标题、摘要、图注和每段首句的读者也能读出主要主张。",
         brief="Run the test, then for each claim a skimming reader misses, rewrite the sentence the report points to "
               "(abstract, a caption or a paragraph's first sentence) so it carries the claim; run again."),
    Task("lean_check", "定理形式化", "writing",
         worker=Worker("lean_check", run_lean_check),
         done="Prove the statements left with sorry, or tell the author which theorems stay unverified.",
         goal="论文的定理在 Lean 里形式化并证明，没有 sorry。",
         brief="For a theorem the author wants verified, draft its Lean statement with the lean_draft operation, check "
               "with the author that it says what the paper says, then prove it in workflow/lean/ and run the check."),
    Task("lean_prove", "定理证明", "writing", inputs=("theorem", "attempts"),
         worker=Worker("lean_prove", run_lean_prove),
         done="Read each accepted proof, then run 定理形式化 to confirm nothing is left with sorry.",
         goal="形式化里留着 sorry 的定理由模型证明、Lean 检查通过，只用标准公理，项目仍能编译。",
         brief="Run the prover on the statements the author approved; it writes only accepted proofs back. Go through "
               "the ones it could not prove with the author: a wrong statement is fixed in the statement, never by an axiom."),
    Task("citation_check", "参考文献核查", "writing", inputs=("draft_dir",),
         worker=Worker("citation_check", run_citation_check),
         done="Correct each flagged entry from its published record.",
         goal="每条参考文献都和发表记录对得上。",
         brief="Correct each flagged .bib entry from its published record; never invent a reference."),
    Task("figures", "论文图表", "writing", gates=("figures",), artifacts=("figures/",), inputs=("draft_dir",),
         worker=Worker("paper_figures", run_paper_figures),
         done="Fix each finding in the plotting scripts and run the task again; look at every redrawn figure "
              "before it goes into the paper.",
         goal="每张图都由脚本从证据画出，检查没有问题。",
         brief="Fix each finding in the plotting scripts, never in the image, and redraw from the evidence sources."),
    Task("writing_style", "写作风格", "writing", inputs=("own_papers",),
         worker=Worker("writing_style", run_writing_style),
         done="Apply the edits you agree with one at a time; correct workflow/style.md where it misreads you.",
         goal="论文读起来像你自己写的：风格跟你以前的论文一致，没有被动堆砌和名词化。",
         brief="Run the task with the author's earlier papers, show them workflow/style.md to correct, then go through "
               "edits.json and apply the ones they accept."),
    Task("paper_translate", "论文翻译", "writing", inputs=("language",),
         worker=Worker("paper_translate", run_paper_translate),
         done="Read the translated PDF; fix terms in workflow/glossary.md and run again.",
         goal="整篇论文译成目标语言并能编译，公式、引用、标签原样保留，术语前后一致。",
         brief="Run the task, then go through the glossary and the passages the report flags with the author; edit "
               "workflow/glossary.md and run again. The translation never replaces the source."),
    Task("method_diagram", "方法示意图", "writing", inputs=("section",),
         worker=Worker("method_diagram", run_method_diagram),
         done="\\input diagram.tex into the paper and adjust labels in graph.json if a component is named differently.",
         goal="方法部分有一张从原文画出的流程图，组件和数据流都和正文一致，是可编辑的 TikZ。",
         brief="Run the task on the method section, show the author diagram.png, and adjust graph.json or the section "
               "text where the diagram misses or misnames a component; then \\input diagram.tex."),
    Task("caption_check", "图表标题检查", "writing",
         worker=Worker("caption_check", run_caption_check),
         done="Apply the caption edits you agree with, add the labels and references, and fix the plotting scripts.",
         goal="每个图表只看标题就能读懂，有标签并被正文引用，绘图脚本达到出版质量。",
         brief="Run the check, apply the caption edits the author accepts, add missing labels and references, and fix "
               "font sizes, formats and axis labels in the plotting scripts, then redraw."),
    Task("proofread", "论文校对", "writing", inputs=("draft_dir",),
         worker=Worker("paper_proofread", run_paper_proofread),
         done="Go through report.md with the author: fix every math and number error, cite or drop each claim "
              "without a citation, and apply the language edits the author accepts.",
         goal="推导、符号、数字和统计没有错，该引用的地方都有引用。",
         brief="Run the check, then go through the issues with the author one by one: fix math and number errors in "
               "the source, add the suggested citations the author accepts to the .bib and the text, and apply "
               "the language edits the author accepts from edits.json with the apply_edit operation, one at a time. "
               "Offer the proposed titles and abstracts; change them only if the author picks one."),
    Task("citation_graph", "引文图谱", "writing",
         inputs=("min_shared",), worker=Worker("citation_graph", run_citation_graph),
         done="Replace or remove every retracted reference, note corrections, and decide for each missing key "
              "work whether to cite it.",
         goal="没有引用被撤稿的论文，你的参考文献共同引用的关键工作都考虑过。",
         brief="Run the check and open graph.html. Remove or replace retracted references; for each missing key work, "
               "read it and, if it belongs, add it to the .bib and cite it where it fits. To bring in the author's "
               "Zotero or other library, have them export it as .bib or CSL-JSON and merge it with the import_library "
               "operation; it only adds entries."),
    Task("peer_feedback", "投稿前找人审读", "writing", inputs=("count", "exclude"),
         worker=Worker("peer_feedback", run_peer_feedback),
         done="Send the drafts you agree with yourself, and keep workflow/feedback.csv's status up to date.",
         goal="找到领域里没有利益冲突、能在投稿前读稿的人，每人一封具体的请求草稿，由你决定发不发。",
         brief="Run the task, go through candidates.md with the author (scores and every exclusion with its reason), "
               "edit the drafts in workflow/feedback/ with them. Never send anything: the author sends and records "
               "sent, replied or declined in workflow/feedback.csv."),
    Task("paper_review", "论文评审", "writing", inputs=("draft_dir", "reviewer_model", "reviewers", "calibration"),
         worker=Worker("paper_review", run_paper_review),
         done="Fix each fatal and major weakness in a draft copy, then run paper_review on that draft; "
              "the next review says which earlier weaknesses are resolved.",
         goal="按投稿会议的标准评审，致命和主要问题都解决了。",
         brief="Go through the review's fatal and major weaknesses with the author, fix them in the paper, and run "
               "the review again: the next round says which are resolved. Stop when the score stops rising."),
    # Release.
    Task("code_wiki", "代码 Wiki", "release", inputs=("code_dir",), worker=Worker("code_wiki", run_code_wiki),
         done="Read the unmapped parts with the author: implement them or narrow the paper; copy the site into docs/ if wanted.",
         goal="代码有模块级文档，论文的方法小节、公式和算法都对应到实现它们的函数，每条对应都有核对过的代码行和原文。",
         brief="Run the task on the paper's code, go through the paper parts with no code and the partial ones with "
               "the author, and copy docs/code_wiki into the repository only if they want it published."),
    Task("code_origin", "代码来源与许可证核查", "release", inputs=("code_dir",),
         worker=Worker("code_origin", run_code_origin),
         done="Copy THIRD_PARTY_NOTICES.md into the repository; replace or relicense any file whose license conflicts.",
         goal="发布的代码里复制或改编的文件都查清来源和许可证，都有署名，没有许可证冲突。",
         brief="Run the check on the code to release; for each copied file without credit, add the notice with the "
               "author; a license conflict is resolved by removing, rewriting or relicensing, never by dropping the notice."),
    Task("software_citation", "软件引用文件", "release", inputs=("code_dir",),
         worker=Worker("software_citation", run_software_citation),
         done="Copy CITATION.cff, .zenodo.json and codemeta.json into the code repository; cite the archive DOI in the paper.",
         goal="代码有合法的 CITATION.cff 等引用文件，有能解析的存档 DOI，论文也引用了它。",
         brief="Run the task, copy the files into the code repository with the author, and if there is no archive DOI "
               "walk them through switching on Zenodo's GitHub integration themselves, then run again and cite the DOI."),
    Task("code_release", "代码发布", "release", gates=("code",), artifacts=("rules.json",),
         kinds=("method", "benchmark"),
         goal="代码仓库公开、有说明、论文里有链接。",
         brief="Make the code repository in rules.json public with a README that reproduces the paper's numbers, "
               "and link it in the paper."),
    Task("dataset_release", "数据集发布", "release", gates=("datasheet",), artifacts=("datasheet.md",),
         kinds=("benchmark",),
         goal="数据集有许可、发布位置和完整的说明卡。",
         brief="Write the dataset's datasheet with the author: license, location and every part (motivation, "
               "composition, collection, preprocessing, uses, distribution, maintenance)."),
    Task("deliverables", "海报与报告材料", "release", gates=("deliverables",), artifacts=("rules.json",),
         goal="rules.json 里列的海报、幻灯片、视频都做好了。",
         brief="Make each deliverable of rules.json from the final paper: the poster and slides from its figures "
               "and claims; render them and check that no text overflows before saying they are done."),
    Task("rebuttal", "审稿回复", "release", gates=("rebuttal",),
         artifacts=("rebuttal/reviews.md", "rebuttal/concerns.csv", "rebuttal/response.md"),
         approves="rebuttal/response.md",
         goal="每条审稿意见都拆出来回答了，回复不超字数，并由你批准。",
         brief="Split the pasted reviews into single concerns in concerns.csv, typed clarify, experiment, "
               "misunderstanding, limitation or concede; answer each from the paper and evidence.csv, and list any "
               "promised change. Then write the response per reviewer within the limit. Never promise a result "
               "that has not been run."),
    Task("text_overlap", "文本重合检查", "release", inputs=("own_papers",),
         worker=Worker("text_overlap", run_text_overlap),
         done="Rewrite or quote and cite each uncited match; note reused text from your own papers in "
              "workflow/text_overlap.md.",
         goal="没有未引用的大段重合；复用自己旧文的地方写明了。",
         brief="Run the check, then for each match rewrite the passage in your own words or quote and cite it; for "
               "text reused from the author's earlier papers, cite them and note it in workflow/text_overlap.md. "
               "Remind the author the venue's own similarity check still applies."),
    Task("media", "播客与讲解视频", "release", inputs=("media_kind", "minutes"),
         worker=Worker("paper_media", run_paper_media),
         done="Listen to or watch the result; fix the script where it says something the paper does not, and run again.",
         goal="生成的播客或讲解视频只讲论文里有的内容，数字都和论文一致。",
         brief="Run the task for a podcast (two hosts) or a narrated video of the slides or paper pages, then go "
               "through script.md with the author and run again after changes to the paper."),
    Task("submission_check", "投稿前检查", "release",
         worker=Worker("submission_check", run_submission_check),
         done="Fix every error the check lists in the source, compile again and run the check until it passes.",
         goal="编译没有错误和未定义的引用，页数不超，匿名投稿没有暴露身份，字体都嵌入了。",
         brief="Compile the paper, run the check, and fix what it finds in the source: LaTeX errors at their line, "
               "undefined references, pages over the limit, anything that reveals the authors in a double-blind "
               "submission. Compile and check again until it passes."),
    Task("arxiv_package", "arXiv 打包", "release",
         worker=Worker("arxiv_package", run_arxiv_package),
         done="Upload arxiv.zip to arXiv and compare its compiled PDF with yours.",
         goal="arXiv 源码包能编译：带 .bbl，用到的文件都在，没有注释和多余文件。",
         brief="Compile the paper once so the .bbl exists, then run the task; if it fails, add the missing files. "
               "Show the author the manifest of what is included and dropped."),
    Task("paper_statements", "论文声明", "release", inputs=("kinds",),
         worker=Worker("paper_statements", run_paper_statements),
         done="Fill every [TODO] with the author, then \\input statements.tex where the venue asks.",
         goal="代码与数据可用性、作者贡献、利益冲突、资助和伦理声明都从项目事实起草，查不到的留待你填。",
         brief="Run the task, go through statements.tex with the author, fill each [TODO] from them (never guess "
               "conflicts or funding), and put the sections where the venue's template asks."),
    Task("ai_disclosure", "AI 使用声明", "release",
         worker=Worker("ai_disclosure", run_ai_disclosure),
         done="Read ai_disclosure.md with the author, correct it, and put it where the venue asks for it.",
         goal="按这个项目的实际运行记录写好 AI 使用声明的草稿，由你核对。",
         brief="Run the task to draft the disclosure from the project's runs and commits, go through it with the "
               "author, and put the corrected text where the venue's policy asks (paper section or form)."),
    Task("venue_pick", "选投会议", "release",
         worker=Worker("venue_pick", run_venue_pick),
         done="Choose with the author, then run 换投会议 or write venue.md for the chosen venue.",
         goal="根据参考文献和相似工作的去向，列出 3–5 个合适的会议和截稿日期。",
         brief="Run the task and go through the candidates with the author: fit, deadline, page limit, double blind. "
               "Once they choose, write venue.md (or run 换投会议 for a move)."),
    Task("hidden_text", "隐藏文字检查", "release",
         worker=Worker("hidden_text_check", run_hidden_text),
         done="Remove every hidden passage from the source, compile and check again.",
         goal="PDF 和源码里没有读者看不见的文字，也没有写给 AI 审稿的提示。",
         brief="Compile the paper, run the check, and remove each hidden passage it finds at its line; instructions "
               "aimed at AI reviewers are misconduct at most venues."),
    Task("image_reuse", "图片复用检查", "release", inputs=("own_papers",),
         worker=Worker("image_reuse", run_image_reuse),
         done="Credit each reused figure in its caption or replace it.",
         goal="图没有未注明地复用以前论文的，论文内部也没有重复的图。",
         brief="Run the check with the author's earlier papers; for each match, add \"adapted from\" with the citation "
               "to the caption or redraw the figure."),
    Task("talk_prep", "报告问答准备", "release", inputs=("minutes", "audio"),
         worker=Worker("talk_prep", run_talk_prep),
         done="Answer the questions marked needs your answer, and rehearse with qa.md.",
         goal="准备好听众最可能问的 15 个问题，每个答案都有论文依据。",
         brief="Run the task, go through qa.md with the author, write the answers marked needs your answer with them, "
               "and fix the slides it says are too dense for the time."),
    Task("paper_agent", "论文工具包", "release", inputs=("code_dir",),
         worker=Worker("paper_agent", run_paper_agent), kinds=("method", "benchmark"),
         done="Add server.py to the code repository with its README section once the tests pass.",
         goal="论文的方法被包装成 MCP 工具，测试能复现论文的结果。",
         brief="Run the task on the code repository; fix the code or the tool definitions until the generated tests "
               "reproduce the paper's numbers, then add server.py and the README section to the repository."),
    Task("after_release", "发表后跟踪", "release", inputs=("work",),
         worker=Worker("after_release", run_after_release),
         done="Reply to or correct what needs it; write in after_release.csv's handled what you did.",
         goal="知道谁引用了论文、是支持还是反驳，公开评论里需要回应或勘误的都处理了。",
         brief="Run the task after publication (work: DOI, arXiv id or OpenAlex id), go through the items that need a "
               "reply or erratum first, draft replies with the author, and record what was done in handled."),
    Task("impact_forecast", "引用预测", "release", inputs=("venue",),
         worker=Worker("impact_forecast", run_impact_forecast),
         done="Use the factors as hints only: they are correlations among comparable works, not causes.",
         goal="根据相近论文的引用轨迹，估计本文 1–3 年的引用区间；已发表的拿实际引用对比。",
         brief="Run the forecast and read it with the author as a range, not a promise; point out which of the "
               "correlated factors (code, preprint) are still in their hands."),
    Task("tech_transfer", "技术转化评估", "release",
         worker=Worker("tech_transfer", run_tech_transfer),
         done="Talk to your technology transfer office before any public release if a patent is worth filing.",
         goal="知道成果的技术成熟度（TRL）、相近专利和可能的应用方向。",
         brief="Run the assessment, go through the TRL evidence and the close patents with the author; for patent "
               "search set USPTO_API_KEY. Never advise on filing: point to the institution's transfer office."),
    Task("writing_provenance", "写作溯源", "release",
         worker=Worker("writing_provenance", run_writing_provenance),
         done="Use the drafted sentence in AI 使用声明, and cite or rewrite every passage marked pasted.",
         goal="每段文字的来源清楚：你写的、AI 生成或修改的、粘贴的，并有每段的修改回放作证。",
         brief="Commit the paper, run the task, open provenance.html with the author: check the AI and pasted passages, "
               "cite or rewrite pasted text, and carry the numbers into the AI-use disclosure."),
    Task("camera_ready", "终稿检查", "release",
         worker=Worker("camera_ready_check", run_camera_ready),
         done="Fix every blocker in the source and run the check again before uploading the final version.",
         goal="终稿去掉匿名、有作者和致谢、版权信息齐全、没有草稿标记、不超页数。",
         brief="Run the check after acceptance, fix each blocker at its line (review options, authors, acknowledgments "
               "and funding, ACM rights and CCS, figure descriptions, leftover TODOs), compile and check again."),
    Task("anonymous_package", "匿名补充材料", "release", inputs=("include", "anonymize"),
         worker=Worker("anonymous_package", run_anonymous_package),
         done="Upload supplementary.zip as the supplementary material; read manifest.md first.",
         goal="补充材料的代码包里没有姓名、单位、邮箱、个人路径和 Git 历史。",
         brief="Run the task; for each finding, change the file (or rerun with anonymize replace after the author "
               "agrees), then show the author manifest.md before they upload supplementary.zip."),
    Task("venue_move", "换投会议", "release", inputs=("target_venue", "write_venue"), required=("target_venue",),
         worker=Worker("venue_move", run_venue_move),
         done="Apply the plan in the paper, then run 投稿前检查 and 会议清单 for the new venue.",
         goal="换投的计划写清楚：模板、参考文献格式、要加减的章节、页数差和匿名要求。",
         brief="Run the task for the target venue, go through plan.md with the author, download the official template "
               "it names, and make the changes in the paper. With write_venue yes it also writes the new venue.md."),
    Task("submission_metadata", "投稿表单信息", "release",
         worker=Worker("submission_metadata", run_submission_metadata),
         done="Read submission_metadata.md with the author and paste it into the submission form.",
         goal="投稿表单的 TL;DR、关键词、主领域和通俗摘要写好，数字都在论文里。",
         brief="Run the task, go through workflow/submission_metadata.md with the author and correct it; the primary "
               "area comes from the venue's own list."),
    Task("croissant", "Croissant 元数据", "release", kinds=("benchmark",),
         worker=Worker("croissant_check", run_croissant),
         done="Fill each missing field in workflow/croissant.json and run the check again.",
         goal="数据集的 Croissant 元数据（含负责任 AI 字段）完整有效。",
         brief="If workflow/croissant.json does not exist, draft it with the croissant_draft operation from the "
               "datasheet; fill every TODO with the author, then run the check until it passes."),
    Task("publication_list", "论文列表", "release", inputs=("author",),
         worker=Worker("publication_list", run_publication_list),
         done="Confirm the uncertain entries with the author, then copy publications.html and .bib where they want them.",
         goal="作者的全部论文汇总成一页，每篇只出现一次并附各种链接，疑似同名作者的论文请你确认。",
         brief="Run the task for the author (name, ORCID, OpenAlex or DBLP id), go through the namesake candidates "
               "and the excluded entries with them, and publish the page only where they choose."),
    Task("project_page", "项目主页", "release", inputs=("publish",),
         worker=Worker("project_page", run_project_page),
         done="Open index.html with the author; publish it to docs/ only when they agree.",
         goal="项目主页和社交媒体帖子写好，数字都和论文一致，链接都能打开。",
         brief="Run the task, open the page with the author and fix what they want in the paper or rules.json links; "
               "run again with publish yes only after they agree. thread.md has the posts for them to post."),
    Task("venue_checklist", "会议清单", "release", gates=("checklist",), artifacts=("venue.md",),
         goal="这一版（投稿或终稿）的清单全部勾上。",
         brief="Go through the checklist of this version in venue.md with the author and tick each item only after "
               "it is done."),
)
BY_NAME = {t.name: t for t in TASKS}


# The sidebar's groups: each phase split by what the author is trying to do, in the order
# shown. Every task is in exactly one group of its own phase (check_registry).
GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("research", "找方向", ("survey", "field_survey", "watch", "personal_rank", "topic_trends", "idea", "idea_generation",
                         "facet_ideas", "lab_meeting", "research_map", "taxonomy")),
    ("research", "读文献", ("deep_read", "literature_table", "claim_support", "source_independence", "meta_analysis")),
    ("research", "做实验", ("experiment_plan", "data_explore", "baseline_code", "baselines", "experiment_loop", "hparam_sweep",
                         "power_analysis", "ablation_target", "remote_run", "campaign", "findings")),
    ("research", "检验结果", ("method_lint", "claim_stats", "consensus", "contamination", "clean_rerun", "experiments")),
    ("research", "人工评测与访谈", ("human_eval_build", "human_eval_analyze", "thematic_analysis")),
    ("research", "基金", ("funding_calls", "call_requirements", "grant")),
    ("writing", "写稿", ("plan", "related_by_paragraph", "writing_style", "paper_translate")),
    ("writing", "图表", ("figures", "method_diagram", "caption_check")),
    ("writing", "核对数字和引用", ("paper_check", "settings_check", "result_numbers", "baseline_numbers", "missing_baselines",
                              "citation_check", "citation_graph")),
    ("writing", "定理", ("lean_check", "lean_prove")),
    ("writing", "审读", ("skim_test", "proofread", "peer_feedback", "paper_review")),
    ("release", "投稿", ("venue_pick", "venue_checklist", "submission_check", "submission_metadata", "paper_statements",
                       "ai_disclosure", "writing_provenance", "text_overlap", "hidden_text", "image_reuse",
                       "anonymous_package", "arxiv_package")),
    ("release", "审稿之后", ("rebuttal", "camera_ready", "venue_move")),
    ("release", "开源代码与数据", ("code_release", "code_wiki", "code_origin", "software_citation", "paper_agent",
                              "dataset_release", "croissant")),
    ("release", "介绍给别人", ("project_page", "deliverables", "talk_prep", "media", "publication_list")),
    ("release", "发表之后", ("after_release", "impact_forecast", "tech_transfer")),
)
GROUP_OF = {name: (title, order) for order, (_, title, names) in enumerate(GROUPS) for name in names}


def describe(task: Task) -> dict:
    """The task as the app sees it."""
    return {"name": task.name, "title": task.title, "phase": task.phase, "gates": list(task.gates),
            "artifacts": [f"{stages.FOLDER}/{a}" for a in task.artifacts], "inputs": list(task.inputs),
            "required": list(task.required), "workflow": task.worker.workflow if task.worker else None,
            "kinds": list(task.kinds), "goal": task.goal, "brief": task.brief, "approves": task.approves,
            "group": GROUP_OF.get(task.name, ("", len(GROUPS)))[0],
            "order": GROUP_OF.get(task.name, ("", len(GROUPS)))[1]}


def serves(task: Task, kind: str) -> bool:
    """Whether the task is listed for this kind of work; every task is, until a kind is set."""
    return not kind or not task.kinds or kind in task.kinds


def check_registry() -> list[str]:
    """Every task names known gates, declares its required inputs and the inputs its gates
    read, and sits in exactly one sidebar group of its own phase."""
    found = []
    for t in TASKS:
        found += [f"{t.name}: unknown gate {g}" for g in t.gates if g not in GATES]
        found += [f"{t.name}: required input {i} is not an input" for i in t.required if i not in t.inputs]
        found += [f"{t.name}: gate {g} reads {i}, which is not an input"
                  for g in t.gates if g in GATES for i in GATES[g].needs if i not in t.inputs]
        found += [f"{t.name}: does nothing" for _ in [0] if not t.gates and not t.worker]
        found += [f"{t.name}: unknown kind {k}" for k in t.kinds if k not in brief.KINDS]
        found += [f"{t.name}: no goal" for _ in [0] if not t.goal]
    listed = [name for _, _, names in GROUPS for name in names]
    found += [f"{n}: in {listed.count(n)} groups" for n in sorted(set(listed)) if listed.count(n) > 1]
    found += [f"{n}: in a group but no such task" for n in sorted(set(listed) - set(BY_NAME))]
    found += [f"{t.name}: in no group" for t in TASKS if t.name not in GROUP_OF]
    found += [f"{n}: grouped under {phase} but its phase is {BY_NAME[n].phase}"
              for phase, _, names in GROUPS for n in names if n in BY_NAME and BY_NAME[n].phase != phase]
    return found


__all__ = ["BY_NAME", "GATES", "GROUPS", "GROUP_OF", "TASKS", "Gate", "Task", "Worker", "check_registry", "describe", "serves", "worst"]
