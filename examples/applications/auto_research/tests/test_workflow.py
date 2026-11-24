"""Task gates, the hand-off to each task's workflow, and the status view."""

import importlib
import json
import shutil
import subprocess
import urllib.error

import pytest

wf = importlib.import_module("auto_research.workflow")
release = importlib.import_module("auto_research.release")
registry = importlib.import_module("auto_research.registry")

CLAIMS = "# Claims\nApproved: yes\n\nC1: Workflows keep changing with feedback.\nRQ: RQ2\n"
EVIDENCE = "id,claim,statement,source,script\nE1,C1,26 changes over 725 iterations,data.csv,\n"
CARD = """Section: Workflow Evolution (RQ2)
Claims: C1
Evidence: E1
Approved: {approved}

## Purpose
Show continuous revision.

## What the evidence shows
Changes from iteration 12 to 689.

## What it cannot claim
Per-change rating causes.

## Storyline
1. Continuous revision.
"""


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def commit(repo, message="x"):
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", message)


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "references.bib").write_text("@article{smith2024,\n title={x}}\n")
    (repo / "data.csv").write_text("x\n1\n")
    commit(repo)
    return repo


def call(repo, **kwargs):
    return json.loads(wf.auto_research(str(repo), **kwargs))


def ready(repo, approved="yes"):
    folder = repo / "workflow"
    (folder / "plans").mkdir(parents=True)
    (folder / "claims.md").write_text(CLAIMS)
    (folder / "evidence.csv").write_text(EVIDENCE)
    (folder / "plans" / "workflow-evolution-rq2.md").write_text(CARD.format(approved=approved))


def test_status_writes_nothing_and_a_task_run_scaffolds_its_templates(paper):
    result = call(paper)
    assert result["created"] == [] and not (paper / "workflow").exists()
    rows = {r["name"]: r for r in result["tasks"]}
    assert all(rows[t.name]["state"] == "not_started" and rows[t.name]["problems"] == []
               for t in registry.TASKS if any(not registry.GATES[g].needs for g in t.gates))
    assert rows["paper_check"]["state"] == "ready"                  # runs on the whole paper
    assert rows["plan"]["state"] == "ready"                         # judged per section
    result = call(paper, task="experiments")
    assert "workflow/claims.md" in result["created"] and "workflow/evidence.csv" in result["created"]
    assert result["status"] == "BLOCKED"


def test_a_template_copy_is_not_a_start(paper):
    call(paper, task="survey")                                      # venue.md, rules.json, literature.md
    rows = {r["name"]: r for r in call(paper)["tasks"]}
    assert rows["survey"]["state"] == "blocked"                     # it ran, so it is judged
    assert rows["venue_checklist"]["state"] == "not_started"        # venue.md is still the template
    (paper / "workflow" / "venue.md").write_text("## Submission checklist\n- [ ] Anonymous\n")
    rows = {r["name"]: r for r in call(paper)["tasks"]}
    assert rows["venue_checklist"]["state"] == "blocked"


def test_existing_artifacts_are_never_overwritten(paper):
    ready(paper)
    call(paper)
    assert (paper / "workflow" / "claims.md").read_text() == CLAIMS


def fakes(monkeypatch, paper_status="PASS", refs_status="PASS"):
    seen = {"paper": [], "refs": []}
    monkeypatch.setattr(registry, "paper_revision_check",
                        lambda paper_dir, **k: seen["paper"].append(k) or
                        json.dumps({"status": paper_status, "report": "p.md"}))
    monkeypatch.setattr(registry, "citation_check",
                        lambda source, **k: seen["refs"].append((source, k["investigate"])) or
                        json.dumps({"status": refs_status, "report": "c.md"}))
    return seen


def test_each_task_checks_only_its_own_inputs(paper, monkeypatch):
    ready(paper)
    (paper / "workflow" / "claims.md").write_text(CLAIMS.replace("yes", "no"))
    result = call(paper, task="idea")
    assert result["status"] == "BLOCKED" and "not approved" in result["problems"][0]
    assert "claims.md" in result["next"]
    # unapproved claims and an unfinished survey do not stop the other tasks
    assert call(paper, task="experiments")["status"] == "PASS"
    seen = fakes(monkeypatch)
    result = call(paper, task="paper_check", section="Workflow Evolution (RQ2)")
    assert result["status"] == "PASS" and seen["refs"] == []          # one task, one workflow
    record = json.loads(open(result["run"]).read())
    assert [s["stage"] for s in record["stages"]] == ["gate plan", "paper_revision_check"]
    assert record["roles"] == {}


def test_evidence_must_name_existing_sources_and_cover_claims(paper):
    ready(paper)
    (paper / "workflow" / "evidence.csv").write_text("id,claim,statement,source,script\nE1,C9,x,missing.csv,\n")
    problems = call(paper, task="experiments")["problems"]
    assert any("unknown claim C9" in p for p in problems)
    assert any("missing.csv does not exist" in p for p in problems)
    assert any("claim C1 has no evidence" in p for p in problems)


def test_plan_creates_a_card_and_blocks_until_approved(paper):
    ready(paper)
    result = call(paper, task="plan", section="Agent Behavior Analysis (RQ3)")
    assert result["status"] == "BLOCKED"
    assert "workflow/plans/agent-behavior-analysis-rq3.md" in result["created"]
    assert "cannot claim" in result["next"]


def test_a_section_check_needs_an_approved_plan(paper, monkeypatch):
    ready(paper, approved="no")
    monkeypatch.setattr(registry, "paper_revision_check", lambda *a, **k: pytest.fail("checked without a plan"))
    result = call(paper, task="paper_check", section="Workflow Evolution (RQ2)")
    assert result["status"] == "BLOCKED" and any("not approved" in p for p in result["problems"])


def test_paper_check_fills_every_argument(paper, monkeypatch):
    ready(paper)
    seen = fakes(monkeypatch)
    result = call(paper, task="paper_check", section="Workflow Evolution (RQ2)", draft_dir="d", baseline="WORKTREE")
    assert result["status"] == "PASS" and result["check"]["report"] == "p.md"
    (k,) = seen["paper"]
    assert k["plan_dir"].endswith("workflow/plans") and k["baseline"] == "WORKTREE" and k["draft_dir"] == "d"
    assert k["scope"] == "changes" and k["sections"] == "Workflow Evolution (RQ2)" and k["phases"] == ""
    call(paper, task="paper_check", section="Workflow Evolution (RQ2)", phases="4,6")
    assert seen["paper"][-1]["phases"] == "4,6"


def test_paper_check_without_a_section_checks_the_whole_paper(paper, monkeypatch):
    seen = fakes(monkeypatch)
    result = call(paper, task="paper_check", scope="paper")
    assert result["status"] == "PASS"
    assert seen["paper"][0]["sections"] == "" and seen["paper"][0]["scope"] == "paper"
    record = json.loads(open(result["run"]).read())
    assert record["stages"][0] == {**record["stages"][0], "stage": "gate plan", "skipped": "no section"}
    state = json.loads((paper / wf.STATE).read_text())
    assert state["paper_check"]["commit"] == release.head(paper) and state["paper_check"]["report"] == "p.md"


def test_citation_check_runs_on_every_bibliography_of_the_draft(paper, monkeypatch):
    seen = fakes(monkeypatch, refs_status="PASS_WITH_NOTES")
    result = call(paper, task="citation_check")
    assert result["status"] == "PASS_WITH_NOTES" and seen["paper"] == []
    assert seen["refs"] == [(str(paper / "references.bib"), "off")]
    draft = paper.parent / "draft"
    shutil.copytree(paper, draft)
    call(paper, task="citation_check", draft_dir=str(draft))
    assert seen["refs"][-1] == (str(draft / "references.bib"), "off")
    (draft / "references.bib").unlink()
    assert call(paper, task="citation_check", draft_dir=str(draft))["status"] == "FAIL"


def test_venue_checklist_reads_the_checklist_of_the_version(paper):
    call(paper)
    problems = call(paper, task="venue_checklist")["problems"]
    assert problems and all("Submission checklist" in p for p in problems)
    (paper / "workflow" / "rules.json").write_text('{"version": "camera_ready"}')
    problems = call(paper, task="venue_checklist")["problems"]
    assert problems and all("Camera-ready checklist" in p for p in problems)
    venue = paper / "workflow" / "venue.md"
    venue.write_text(venue.read_text().replace("- [ ]", "- [x]"))
    assert call(paper, task="venue_checklist")["status"] == "PASS"


REPO = "https://github.com/someone/code"


def test_code_release_must_be_public_linked_and_kept_to_allowed_contributors(paper, monkeypatch):
    answers = {"": {"private": False}, "/readme": {}, "/contributors": [{"login": "me"}, {"login": "bot"}]}
    monkeypatch.setattr(release, "github", lambda url, path="": answers[path])
    assert release.code_release(paper, {}) == []                     # no code release asked for
    found = release.code_release(paper, {"repo": REPO, "allowed_contributors": ["me"]})
    assert f"the paper does not link {REPO}" in found and f"{REPO}: contributor bot is not allowed" in found
    (paper / "main.tex").write_text(f"Code: \\url{{{REPO}}}.")
    assert release.code_release(paper, {"repo": REPO, "allowed_contributors": ["me", "bot"]}) == []

    def missing(url, path=""):
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    monkeypatch.setattr(release, "github", missing)
    assert "missing or private" in release.code_release(paper, {"repo": REPO})[0]
    assert "not a GitHub repository" in release.code_release(paper, {"repo": "https://example.com/x"})[0]
    (paper / "workflow").mkdir()
    (paper / "workflow" / "rules.json").write_text(json.dumps({"code": {"repo": REPO}}))
    assert call(paper, task="code_release")["blocked_by"] == "code"


def test_deliverables_must_exist(paper):
    call(paper, task="survey")
    (paper / "workflow" / "rules.json").write_text(
        json.dumps({"deliverables": [{"name": "poster", "path": "poster/poster.pdf"}]}))
    assert call(paper, task="deliverables")["problems"] == ["poster: poster/poster.pdf is missing"]
    (paper / "poster").mkdir()
    (paper / "poster" / "poster.pdf").write_bytes(b"%PDF")
    assert call(paper, task="deliverables")["status"] == "PASS"


RULES = '{"body_page_limit": 5, "required_source": ["\\\\usepackage{neurips_2026}"]}'


def test_paper_rules_must_be_filled_in(paper):
    call(paper)
    rules = paper / "workflow" / "rules.json"
    problems = call(paper, task="survey")["problems"]
    assert "rules.json: body_page_limit not set" in problems
    rules.write_text('{"body_page_limit": 5, "page_limit": 4}')
    problems = call(paper, task="survey")["problems"]
    assert "rules.json: unknown key page_limit" in problems
    rules.write_text("{not json")
    assert any("not valid JSON" in p for p in call(paper, task="survey")["problems"])
    rules.write_text('{"body_page_limit": 5, "version": "final"}')
    assert "rules.json: version must be one of submission, camera_ready" in call(paper, task="survey")["problems"]
    rules.write_text('{"body_page_limit": 5, "deliverables": [{"name": "poster"}]}')
    assert any("deliverables must be a list" in p for p in call(paper, task="survey")["problems"])
    rules.write_text(RULES)
    assert not any(p.startswith("rules.json") for p in call(paper, task="survey")["problems"])


def test_surveyed_works_must_exist_in_the_bibliography(paper):
    call(paper, task="survey")
    (paper / "workflow" / "literature.md").write_text("- jones2023: something\n")
    assert any("jones2023 is not in the .bib" in p for p in call(paper, task="survey")["problems"])


def test_unknown_task_and_missing_section_are_rejected(paper):
    with pytest.raises(ValueError, match="task must be"):
        call(paper, task="revise")
    with pytest.raises(ValueError, match="needs section"):
        call(paper, task="plan")


def test_registry_names_known_gates_and_inputs():
    assert registry.check_registry() == []
    assert all(t.gates or t.worker for t in registry.TASKS)


def test_status_lists_every_task_for_the_app(paper, monkeypatch):
    (paper / "workflow").mkdir()
    (paper / "workflow" / "rules.json").write_text(json.dumps({"code": {"repo": REPO}}))
    monkeypatch.setattr(release, "github", lambda *a, **k: pytest.fail("status called GitHub"))
    result = call(paper)
    rows = {r["name"]: r for r in result["tasks"]}
    assert list(rows) == [t.name for t in registry.TASKS]
    assert rows["survey"]["state"] == "blocked" and rows["survey"]["title"] == "调研材料"
    assert rows["survey"]["blocked_by"] == "survey" and "venue.md" in rows["survey"]["next"]
    assert rows["plan"]["state"] == "ready" and rows["plan"]["problems"] == []   # judged per section
    assert rows["idea"]["state"] == "not_started"                                # claims.md not begun
    assert rows["code_release"]["state"] == "ready"                              # not judged offline
    assert rows["paper_check"]["workflow"] == "paper_revision_check" and rows["idea"]["workflow"] is None
    assert rows["citation_check"]["workflow"] == "citation_check"
    assert rows["paper_check"]["required"] == [] and "section" in rows["paper_check"]["inputs"]
    assert not (paper / "drafts").exists()          # status keeps no record


def test_status_shows_the_last_run_of_each_task_and_section(paper, monkeypatch):
    ready(paper)
    monkeypatch.setattr(registry, "paper_revision_check",
                        lambda *a, **k: json.dumps({"status": "PASS_WITH_NOTES", "report": "r.md"}))
    call(paper, task="paper_check", section="Workflow Evolution (RQ2)")
    call(paper, task="idea")
    rows = {r["name"]: r for r in call(paper)["tasks"]}
    (check,) = rows["paper_check"]["last_runs"]
    assert check["inputs"]["section"] == "Workflow Evolution (RQ2)" and check["status"] == "PASS_WITH_NOTES"
    assert check["report"] == "r.md" and check["run"].endswith("run.json")
    assert check["commit"] == release.head(paper)
    assert rows["idea"]["last_runs"][0]["status"] == "PASS" and rows["survey"]["last_runs"] == []


def test_field_survey_hands_the_direction_to_research_survey(paper, monkeypatch):
    seen = {}

    def survey(direction, **kw):
        seen.update(direction=direction, **kw)
        return json.dumps({"status": "PASS", "report": "r.md", "run": "run.json"})
    monkeypatch.setattr(registry, "research_survey", survey)
    with pytest.raises(ValueError):
        call(paper, task="field_survey")
    result = call(paper, task="field_survey", direction="LLM agents in games", reader_level="RL basics",
                  section="ignored")
    assert result["status"] == "PASS" and seen["direction"] == "LLM agents in games"
    assert seen["reader_level"] == "RL basics" and seen["purpose"] == "survey"
    assert seen["out_dir"].endswith("workflow/surveys/llm-agents-in-games")
    assert seen["preset"] == ""
    call(paper, task="field_survey", direction="LLM knowledge", preset="llm_knowledge")
    assert seen["preset"] == "llm_knowledge" and seen["out_dir"].endswith("workflow/surveys/llm-knowledge")


def test_paper_review_hands_the_draft_and_reviewer_model_on(paper, monkeypatch):
    seen = {}

    def review(paper_dir, **kw):
        seen.update(paper_dir=paper_dir, **kw)
        return json.dumps({"status": "PASS_WITH_NOTES", "report": "review.md", "run": "run.json"})
    monkeypatch.setattr(registry, "paper_review", review)
    result = call(paper, task="paper_review", reviewer_model="openai:gpt-5", direction="ignored")
    assert result["status"] == "PASS_WITH_NOTES" and result["check"]["report"] == "review.md"
    assert seen["reviewer_model"] == "openai:gpt-5" and seen["draft_dir"] == ""
    assert seen["out_dir"].endswith("drafts/paper_review")


def test_idea_generation_needs_gaps_and_hands_the_direction_on(paper, monkeypatch):
    seen = {}

    def ideas(paper_dir, **kw):
        seen.update(kw)
        return json.dumps({"status": "PASS", "report": "ideas.md", "run": "run.json"})
    monkeypatch.setattr(registry, "idea_generation", ideas)
    result = call(paper, task="idea_generation")
    assert result["status"] == "BLOCKED" and result["blocked_by"] == "gaps" and not seen
    (paper / "workflow" / "literature.md").write_text("- smith2024: x; we differ\n")
    result = call(paper, task="idea_generation", direction="LLM agents")
    assert result["status"] == "PASS" and seen["direction"] == "LLM agents"
    assert seen["out_dir"].endswith("drafts/idea_generation")


def test_figures_needs_a_plotting_script_and_hands_the_draft_on(paper, monkeypatch):
    seen = {}

    def figures(paper_dir, **kw):
        seen.update(paper_dir=paper_dir, **kw)
        return json.dumps({"status": "PASS_WITH_NOTES", "report": "report.md", "run": "run.json"})
    monkeypatch.setattr(registry, "paper_figures", figures)
    result = call(paper, task="figures")
    assert result["status"] == "BLOCKED" and result["blocked_by"] == "figures" and not seen
    assert "workflow/figures/paperfig.py" in result["created"] and "paperfig.py" in result["next"]
    assert "apply_publication_style" in (paper / "workflow" / "figures" / "paperfig.py").read_text()
    (paper / "workflow" / "figures" / "plot_main.py").write_text("print('x')\n")
    result = call(paper, task="figures", draft_dir="d", section="ignored")
    assert result["status"] == "PASS_WITH_NOTES" and result["check"]["report"] == "report.md"
    assert seen["draft_dir"] == "d" and seen["out_dir"].endswith("drafts/paper_figures")
