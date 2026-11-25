"""The checks of the experiment loop, plans, baselines, findings, literature tables,
taxonomy, rebuttal and datasheet; the watch for new work; the author's approval."""

import importlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

checks = importlib.import_module("auto_research.checks")
brief = importlib.import_module("auto_research.brief")
registry = importlib.import_module("auto_research.registry")
stages = importlib.import_module("auto_research.stages")
watch = importlib.import_module("auto_research.tasks.watch.workflow")
wf = importlib.import_module("auto_research.workflow")

CLAIMS = "# Claims\nApproved: yes\n\nC1: Memory edits cut errors.\nRQ: RQ1\n"
EVIDENCE = "id,claim,statement,source,script\nE1,C1,errors fall 18%,data.csv,\n"
BIB = "@article{smith2024,\n title={Memory for agents}}\n@article{lee2025,\n title={Forgetting}}\n@article{wu2023,\n title={Long chats}}\n"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, message="x"):
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "--short", "HEAD")


@pytest.fixture
def paper(tmp_path):
    repo = tmp_path / "paper"
    (repo / "workflow").mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / "references.bib").write_text(BIB)
    (repo / "data.csv").write_text("x\n1\n")
    (repo / "train.py").write_text("print('val_loss: 1.0')\n")
    (repo / "workflow" / "claims.md").write_text(CLAIMS)
    (repo / "workflow" / "evidence.csv").write_text(EVIDENCE)
    commit(repo)
    return repo


def write(paper, name, text):
    path = paper / "workflow" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_every_task_has_a_goal_and_its_approval_file_has_an_approved_line():
    assert not registry.check_registry()
    for task in registry.TASKS:
        assert task.goal and task.brief
        if task.approves:
            assert "Approved:" in stages.template("plan.md" if "{section}" in task.approves else task.approves)


def test_the_experiment_loop_needs_an_approved_spec_and_kept_runs_that_improve(paper):
    assert checks.experiment(paper, []) == ["experiment.md: still the template; write the loop's spec"]
    spec = stages.template("experiment.md")
    write(paper, "experiment.md", spec.replace("Edit: train.py", "Edit: train.py, model.py"))
    found = checks.experiment(paper, [])
    assert "experiment.md: file to edit model.py does not exist" in found
    assert "experiment.md: not approved by the author" in found
    write(paper, "experiment.md", spec.replace("Approved: no", "Approved: yes").replace("## Goal", "## Goal\nCut loss."))
    first = commit(paper)
    (paper / "train.py").write_text("print('val_loss: 0.9')\n")
    second = commit(paper)
    write(paper, "results.tsv", "commit\tmetric\tstatus\tseconds\tdescription\n"
                                f"{first}\t1.0\tkeep\t300\tbaseline\n"
                                f"{second}\t0.9\tkeep\t300\tsmaller lr\n"
                                "abc1234\t0.95\tdiscard\t300\twider\n"
                                "\t\tcrash\t12\tout of memory\n")
    assert checks.experiment(paper, []) == []
    write(paper, "results.tsv", "commit\tmetric\tstatus\tseconds\tdescription\n"
                                f"{second}\t0.9\tkeep\t300\ta\n{first}\t1.0\tkeep\t300\tb\nzzz\tx\tkeep\t1\tc\n")
    found = checks.experiment(paper, [])
    assert any("does not improve on 0.9" in p for p in found)
    assert any("'x' is not a number" in p for p in found)



def test_parallel_branches_improve_from_their_parent_and_the_best_is_merged(paper):
    spec = stages.template("experiment.md").replace("Approved: no", "Approved: yes").replace("## Goal", "## Goal\nCut.")
    write(paper, "experiment.md", spec)
    (paper / "train.py").write_text("x")
    base = commit(paper)
    (paper / "train.py").write_text("a")
    a1 = commit(paper)
    (paper / "train.py").write_text("b")
    b1 = commit(paper)
    head = "commit\tmetric\tstatus\tseconds\tdescription\tbranch\tparent\n"
    rows = (f"{base}\t1.0\tkeep\t300\tbaseline\t\t\n"
            f"{a1}\t0.8\tkeep\t300\tsmaller lr\tlr\t{base}\n"
            f"{b1}\t0.7\tkeep\t300\twider\twidth\t{base}\n")
    write(paper, "results.tsv", head + rows)
    assert checks.experiment(paper, []) == [
        f"results.tsv: 3 branches kept runs but none is merged; merge the best one ({b1}, 0.7)"]
    write(paper, "results.tsv", head + rows + f"{a1}\t\tmerge\t0\tmerge lr\t\t\n")
    assert checks.experiment(paper, []) == [
        f"results.tsv row 4: merged {a1} (0.8), but the best kept run reached 0.7"]
    write(paper, "results.tsv", head + rows + f"{b1}\t\tmerge\t0\tmerge width\t\t\n")
    assert checks.experiment(paper, []) == []
    write(paper, "results.tsv", head + f"{base}\t1.0\tkeep\t300\tbaseline\t\t\n"
                                       f"{a1}\t1.2\tkeep\t300\tworse\tlr\t{base}\n"
                                       f"{b1}\t0.7\tkeep\t300\tx\twidth\tnope\n")
    found = checks.experiment(paper, [])
    assert "results.tsv row 2: kept, but 1.2 does not improve on 1.0 on branch lr" in found
    assert "results.tsv row 3: branch width starts from nope, which is not a kept run" in found


def test_an_experiment_plan_covers_every_claim_and_a_method_paper_has_ablations(paper):
    assert "experiment_plan.csv: claim C1 has no experiment" in checks.experiment_plan(paper, [])
    write(paper, "experiment_plan.csv", "claim,experiment,kind,evidence,status\nC1,full model vs base,main,E1,done\n")
    assert checks.experiment_plan(paper, []) == []
    brief.write(paper, kind="method")
    assert checks.experiment_plan(paper, []) == ["experiment_plan.csv: claim C1 has no ablation"]
    write(paper, "experiment_plan.csv", "claim,experiment,kind,evidence,status\nC1,a,main,E9,done\nC1,b,ablation,,planned\n")
    assert checks.experiment_plan(paper, []) == ["experiment_plan.csv row 1: done, but evidence E9 is not in evidence.csv"]



def test_a_preregistered_plan_shows_what_changed_after_the_lock(paper):
    prereg = importlib.import_module("auto_research.prereg")
    spec = importlib.util.spec_from_file_location("ar_backend_for_prereg",
                                                  Path(__file__).resolve().parents[1] / "app" / "backend.py")
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    backend.workflows = lambda: (registry, stages, wf, None, None, None)
    head = "claim,experiment,kind,evidence,status\n"
    write(paper, "experiment_plan.csv", head + "C1,full vs base,main,,planned\n")
    ctx = type("C", (), {"project_path": str(paper)})()
    assert backend.preregister({"project": str(paper)}, ctx)["locked"] == 1
    assert checks.experiment_plan(paper, []) == []
    write(paper, "experiment_plan.csv", head + "C1,full vs base on 3 seeds,main,,planned\nC1,extra probe,robustness,,planned\n")
    assert checks.experiment_plan(paper, []) == [
        'experiment_plan.csv: C1 main "full vs base" changed after the plan was preregistered on '
        f'{prereg.load(paper)["at"][:10]}; lock the plan again and say why']
    since = prereg.compare(paper, backend.plan_rows(paper))
    assert [r["experiment"] for r in since["exploratory"]] == ["extra probe", "full vs base on 3 seeds"]
    backend.preregister({"project": str(paper), "reason": "three seeds"}, ctx)
    assert checks.experiment_plan(paper, []) == []
    assert prereg.load(paper)["history"][0]["replaced_because"] == "three seeds"


def test_baselines_are_run_and_their_status_follows_the_tolerance(paper):
    assert checks.baselines(paper, []) == ["baselines.csv: no baseline"]
    (paper / "logs").mkdir()
    (paper / "logs" / "a.log").write_text("ok")
    write(paper, "baselines.csv", "name,source,setting,reported,reproduced,tolerance,status,log\n"
                                  "A,smith2024,x,80.0,79.8,0.5,reproduced,logs/a.log\n"
                                  "B,lee2025,x,70.0,60.0,0.5,reproduced,logs/b.log\n"
                                  "C,wu2023,x,50,,1,planned,\n")
    assert checks.baselines(paper, []) == [
        "baselines.csv B: 60.0 vs 70.0 within 0.5 is a gap, not reproduced",
        "baselines.csv B: log logs/b.log does not exist",
        "baselines.csv C: not run yet (status must be one of reproduced, gap, failed)"]


def test_findings_need_every_field_and_a_known_verdict(paper):
    assert checks.findings(paper, []) == ["findings.md: no finding recorded"]
    write(paper, "findings.md", "# Findings\n\n## F1: lr\nHypothesis: smaller lr helps\nRun: abc\nResult: 0.9\n"
                                "Verdict: supported\n\n## F2: width\nHypothesis: wider helps\nRun:\nResult: same\nVerdict: maybe\n")
    assert checks.findings(paper, []) == ["findings.md F2: width: Run is empty",
                                          "findings.md F2: width: Verdict must be one of supported, refuted, inconclusive"]


def test_claims_are_set_against_two_works_with_quotes(paper):
    write(paper, "claim_support.csv", "claim,work,stance,quote,note\nC1,smith2024,supports,They show it.,\n"
                                      "C1,nobody,contradicts,,\n")
    assert checks.claim_support(paper, []) == [
        "claim_support.csv row 2: nobody is not in the .bib files", "claim_support.csv row 2: no quote",
        "claim_support.csv row 2: contradicts the claim; say in note how the paper answers it"]
    write(paper, "claim_support.csv", "claim,work,stance,quote,note\nC1,smith2024,supports,They show it.,\n")
    assert checks.claim_support(paper, []) == ["claim_support.csv: claim C1 is set against fewer than two works"]
    write(paper, "claim_support.csv", "claim,work,stance,study,quote,note\nC1,smith2024,supports,position,They argue it.,\n"
                                      "C1,lee2025,related,survey,They list it.,\nC1,wu2023,supports,anecdote,x,\n")
    assert checks.claim_support(paper, []) == [
        "claim_support.csv row 3: study must be one of empirical, theoretical, survey, position",
        "claim_support.csv: claim C1 is supported only by position or survey works; find an empirical or theoretical one"]
    assert checks.consensus(paper) == {"C1": {"supports": 2, "contradicts": 0, "related": 1}}


def test_a_literature_table_compares_three_works_with_every_cell(paper):
    assert checks.literature_table(paper, [])[0].startswith("literature_table.csv: 0 works compared")
    write(paper, "literature_table.csv", "key,data,result\nsmith2024,A,1\nlee2025,B,not reported\nwu2023,,3\n")
    assert checks.literature_table(paper, []) == [
        'literature_table.csv wu2023: empty data (write "not reported" if the work does not say)']


def test_a_taxonomy_places_every_surveyed_work(paper):
    write(paper, "literature.md", "# Literature\n\n- smith2024: x\n- lee2025: y\n")
    write(paper, "taxonomy.md", "# Taxonomy\n\n## Memory\nWorks: smith2024, ghost\n\n## Empty\n")
    assert checks.taxonomy(paper, []) == ["taxonomy.md Memory: ghost is not in the .bib files",
                                          "taxonomy.md Empty: no works",
                                          "taxonomy.md: surveyed work lee2025 is in no category"]


def test_a_rebuttal_answers_every_concern_within_the_limit_and_is_approved(paper):
    found = checks.rebuttal(paper, [])
    assert "rebuttal/reviews.md: paste the reviews" in found
    assert "rebuttal/concerns.csv: no concern listed" in found
    write(paper, "rebuttal/reviews.md", "# Reviews\n\n## R1\nThe gains may be noise.\n")
    write(paper, "rebuttal/concerns.csv", "id,reviewer,concern,type,answer,evidence,promise\n"
                                          "Q1,R1,gains may be noise,experiment,,,\nQ2,R1,x,clarify,See 3.2,E7,\n")
    write(paper, "rebuttal/response.md", "# Response\nApproved: no\nLimit: 10\n\n## R1\nWe thank the reviewer warmly.\n")
    assert checks.rebuttal(paper, []) == [
        "rebuttal/concerns.csv Q1: not answered",
        "rebuttal/concerns.csv Q1: asks for an experiment; cite its evidence or state the promise",
        "rebuttal/concerns.csv Q2: evidence E7 is not in evidence.csv",
        "rebuttal/response.md R1: 29 characters, over the limit of 10",
        "rebuttal/response.md: not approved by the author"]


def test_a_datasheet_has_license_location_and_every_part(paper):
    found = checks.datasheet(paper, [])
    assert found[:2] == ["datasheet.md: License is empty", "datasheet.md: Location is empty"]
    text = stages.template("datasheet.md").replace("License:", "License: CC-BY-4.0").replace("Location:", "Location: data.csv")
    for part in checks.DATASHEET_PARTS:
        text = text.replace(f"## {part}\n", f"## {part}\nWritten.\n")
    write(paper, "datasheet.md", text)
    assert checks.datasheet(paper, []) == []


def test_a_template_in_a_subfolder_is_not_a_start(paper):
    stages.scaffold(paper, "rebuttal/reviews.md", [], target="rebuttal/reviews.md")
    assert not stages.started(paper, "rebuttal/reviews.md")
    write(paper, "rebuttal/reviews.md", "# Reviews\n\n## R1\nWeak.\n")
    assert stages.started(paper, "rebuttal/reviews.md")


def test_the_watch_judges_only_unseen_papers_and_keeps_overlaps_open(paper, monkeypatch):
    brief.write(paper, claim="Editing memory cuts errors.")
    calls = []
    monkeypatch.setattr(watch, "write_queries", lambda p, c, m, t: calls.append("queries") or ["agent memory editing"])
    found = [watch.search.candidate("Memory for agents", date="2026-01-02", arxiv="2601.1"),
             watch.search.candidate("Editing agent memory", date="2026-02-03", arxiv="2602.2"),
             watch.search.candidate("Cooking", date="2025-05-05", arxiv="2505.3")]
    monkeypatch.setattr(watch.search, "search_all", lambda q, since, n, check: (found, {}))

    def judge(fresh, claims, main, trail):
        calls.append([c["title"] for c in fresh])
        return [{"id": c["id"], "title": c["title"], "url": c["url"], "date": c["date"],
                 "claim": "C1" if "Editing" in c["title"] else "", "verdict": "overlap" if "Editing" in c["title"] else "unrelated",
                 "reason": "同一主张", "handled": ""} for c in fresh]
    monkeypatch.setattr(watch, "judge", judge)
    result = json.loads(watch.related_work_watch(str(paper)))
    assert calls == ["queries", ["Editing agent memory", "Cooking"]]     # the bibliography's paper is skipped
    assert result["status"] == "FAIL" and result["new"] == 2
    assert watch.open_overlaps(paper) == [
        "watch.csv: Editing agent memory overlaps C1; write in handled what the paper does about it"]
    rows = watch.load(paper)
    rows[0]["handled"] = "cited; ours edits, theirs only reads"
    watch.save(paper, rows)
    monkeypatch.setattr(watch, "read_queries", lambda p: ["agent memory editing"])
    result = json.loads(watch.related_work_watch(str(paper)))
    assert result == {**result, "status": "PASS", "new": 0} and calls[-1] == ["Editing agent memory", "Cooking"]


def test_the_author_approves_a_task_file_from_the_app(paper):
    spec = importlib.util.spec_from_file_location("ar_backend_for_approve",
                                                  Path(__file__).resolve().parents[1] / "app" / "backend.py")
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)

    class Context:
        project_path = str(paper)
    write(paper, "claims.md", CLAIMS.replace("yes", "no"))
    backend.workflows = lambda: (registry, stages, wf, None, None, None)
    assert backend.approve({"task": "idea"}, Context()) == {"approved": "workflow/claims.md"}
    assert "Approved: yes" in (paper / "workflow" / "claims.md").read_text()
    with pytest.raises(ValueError, match="choose the section"):
        backend.approve({"task": "plan"}, Context())
    with pytest.raises(ValueError, match="nothing to approve"):
        backend.approve({"task": "survey"}, Context())
