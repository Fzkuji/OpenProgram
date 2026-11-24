# auto_research

The entry for a research project. Its tasks are declared as data in `registry.py`, and the entry, the status view and the app all read that table. Each task does one thing: it checks one set of artifacts, or it runs one workflow. Tasks are not grouped into larger steps such as "revise" or "submit". They do not depend on each other: any task runs on its own and in any order, and it checks only what it reads. A new need becomes a new row when it arises; nothing is built ahead of it. The phase column is only a label the app groups rows by.

| Item | Contract |
| --- | --- |
| Purpose | Check one task's own inputs and run that task's workflow. |
| Input | `paper_dir`: the project's Git repository. `task`: `status` (default) or a task name from the registry. `section`, `draft_dir`, `baseline`, `scope`, `phases`, `direction`, `reader_level`, `purpose`, `our_work`, `preset` and `reviewer_model` go only to the tasks that declare them. A required input that is empty, an unknown task or a missing repository raises `ValueError`. |
| Output | For a task: JSON with `task`, `status` (`PASS`, `BLOCKED`, or the workflow's status), `problems`, `blocked_by` (the first gate that failed), `created`, `next`, `run`, and `check` (the workflow's result). For `status`: every task as the app shows it (`name`, `title`, `phase`, `gates`, `artifacts`, `inputs`, `required`, `workflow`, `state`, `blocked_by`, `problems`, `next`, `done`, `last_runs`) and the plan cards. |
| Effects | A task run copies its missing artifacts from `templates/` and never overwrites them; `status` writes nothing. For each task run it writes a run record to `drafts/auto_research/<timestamp>/` and the latest result to `drafts/auto_research/state.json`, keyed by task, or by task and section, with its inputs and the commit it ran at. `status` only reads them. It runs each gate once, and skips gates that read an input (the plan card) or the network (the code release). A task whose artifacts are missing or still the template, and that never ran, reads `not_started` and is not judged. It never edits the paper. |
| Model calls | None of its own. Workers are plain code that fill every argument of the child workflow, and model calls happen only inside the child. citation_check runs with `investigate=off`, which makes no model calls. field_survey runs research_survey, whose roles make 50 to 90 calls for a field. idea_generation makes two calls (ideator, assessor) and searches the public APIs of arXiv, Semantic Scholar and OpenAlex. paper_review makes one reviewer call, on `reviewer_model` when one is named. figures makes none and runs the paper's plotting scripts. The code_release gate reads GitHub's public API (`GH_TOKEN` raises the rate limit). |
| Failure and cancellation | A failed gate is `BLOCKED`, not an exception. Workflow errors propagate. Rerunning is safe. |
| Tests | `tests/test_workflow.py`: the registry, every gate, independent tasks, the argument hand-off, the status view with last runs and the run record. `tests/test_trail.py`: the run record. `tests/<task>/`: each task workflow's own suite (`citations`, `figures`, `ideas`, `review`, `revision`, `survey`). |
| Usage | `openprogram programs run auto_research -a paper_dir=<repo> -a task=paper_check -a section="Workflow Evolution (RQ2)"` |

## Tasks

| Task | Title | Phase | Gates | Workflow | Passes when |
| --- | --- | --- | --- | --- | --- |
| survey | 调研材料 | research | survey | none | venue.md, rules.json and literature.md are filled; surveyed keys are in the .bib |
| field_survey | 领域综述 | research | none | research_survey (`tasks/survey/`) | the survey of `direction`, within the tracks of `preset` when one is named (`llm_knowledge`), reaches `min_papers` core papers with no audit issue left; the page and records go to `workflow/surveys/<direction>/` |
| idea_generation | 想法生成 | research | gaps | idea_generation (`tasks/ideas/`) | at least one proposed idea is novel against the records its search found; the ideas come from the open directions of the field surveys (of `direction` when given) and the works in literature.md, and go to `drafts/idea_generation/` |
| idea | 想法与研究问题 | research | idea | none | every claim names its RQ and the author approved them |
| experiments | 实验证据 | research | experiments | none | every number has an existing source, and every claim has evidence |
| plan | 章节计划 | writing | plan | none | the section's plan card is filled and approved |
| paper_check | 论文检查 | writing | plan, when a section is given | paper_revision_check (`tasks/revision/`) | the check of the section, or of the whole paper, has no FAIL |
| citation_check | 参考文献核查 | writing | none | citation_check (`tasks/citations/`) | every entry of every .bib matches a published record |
| figures | 论文图表 | writing | figures | paper_figures (`tasks/figures/`) | every script named by `figure_scripts` in rules.json follows the figure rules and runs; the figures are redrawn, and the reports go to `drafts/paper_figures/` |
| paper_review | 论文评审 | writing | none | paper_review (`tasks/review/`) | the review of the paper, or of `draft_dir`, scores 7 or more with no fatal weakness; reviews go to `drafts/paper_review/`, and each says which weaknesses of the last one are resolved |
| code_release | 代码发布 | release | code | none | `code.repo` in rules.json is empty, or it is public, has a README, is linked from the paper and has only allowed contributors |
| deliverables | 海报与报告材料 | release | deliverables | none | every `{name, path}` in `deliverables` of rules.json exists |
| venue_checklist | 会议清单 | release | checklist | none | every item of the checklist that `version` names is ticked; it lists only what no task checks |

A gate that reads another task's artifact uses only its ids. It does not judge that artifact: unapproved claims block the idea task, not the plan.

## Adding a task

1. When the task needs a new check, add a `Gate` to `registry.GATES`. It holds a function `(paper, created, inputs) -> problems` and the instruction to show when it fails. `needs` names the inputs it reads (it is skipped while they are empty); `live=True` marks a gate that calls the network, which the status view skips.
2. Add a `Task` row to `registry.TASKS` with its gates, artifacts, inputs and required inputs. When a workflow does the work, give it one `Worker`, whose `run(paper, inputs)` builds every argument of that workflow in code. Something that does two things is two rows.
3. Add the artifact templates to `templates/` and a test. `check_registry()` must stay empty.

Plans and claims are shown to the author in Chinese. A plan states only what the figures, tables and data contain.

## Layout

| Path | Contents |
| --- | --- |
| `workflow.py` | The entry runs the task's own gates and then its workflow. It also saves the last result and builds the status view. |
| `registry.py` | Gates, workers and the task table. |
| `stages.py` | The gate checks on the artifacts, and the saved state, in plain code. |
| `release.py` | The code release and deliverables checks. |
| `trail.py` | The run record every task and workflow here writes (`run.json` and `run.md`); see below. |
| `tasks/survey/` | The research_survey workflow: a field surveyed into one offline HTML page. See its README. |
| `tasks/revision/` | The paper_revision_check workflow: a draft checked against a committed baseline, with the red/green PDF. See its README. |
| `tasks/ideas/` | The idea_generation workflow: ideas from the surveyed gaps, checked against the literature and ranked. See its README. |
| `tasks/review/` | The paper_review workflow: the paper reviewed as its venue would, round after round. See its README. |
| `tasks/figures/` | The paper_figures workflow: the plotting scripts checked against the figures4papers skill and run. See its README. |
| `tasks/citations/` | The citation_check workflow: references checked against free sources. See its README. |
| `templates/` | The starting artifacts. |
| `app/` | The Auto Research application: each task as a page with its files and its own conversation, where the author's agent does the work in the paper's folder. See `app/README.md`. |

## Run record

`trail.py` records each run: the roles it used, every stage's context and output, and the files it produced. It makes no model call and does not change what the model sees.

| Item | Contract |
| --- | --- |
| Input | `Trail(workflow, args, roles)`, where `roles` maps a name to an `Agent`. Code stages call `trail.step(name, **facts)`. A model stage wraps its role call in `with trail.call(name, role, function, ctx, schema) as rec:` and sets `rec.output`. |
| Output | `trail.save(out, **files)` writes `run.json` (schema 1) and `run.md` into the run's output folder. |
| Live progress | `with trail.listen(callback):` receives every `step` and every model call's start and end while the block runs. |
| Failure | An exception inside `trail.call` is recorded as `error` and raised again. |
| Tests | `tests/test_trail.py` |

| run.json key | Contents |
| --- | --- |
| `schema` | `1`. Raise it on any breaking change; readers check it. |
| `workflow`, `started`, `finished` | The entry name and ISO times. |
| `args` | The entry's arguments. |
| `roles` | For each role: `instructions`, `effort`, `model` ("caller's model" when not set) and `tools`. |
| `stages` | In order. A code stage holds `stage`, `kind: "code"`, `at` and its facts. A model stage holds `stage`, `kind: "model"`, `role`, `function`, `instructions` (the function docstring the framework sends), `context` (every Context block as resolved), `schema`, `output`, `seconds` and `error` when it failed. |
| `outputs` | The files the run produced, by name. |

Text longer than 200,000 characters is cut and marked.
