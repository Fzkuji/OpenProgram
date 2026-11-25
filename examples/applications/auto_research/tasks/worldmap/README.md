# research_map task

The `research_map` workflow of auto_research (`tasks/worldmap/`). It draws the project's argument map and keeps a persistent world model of its research in `workflow/world.json`. Learned from Kosmos's structured world model, Co-STORM's mind map and AgentRxiv's shared preprint server.

| Item | Contract |
| --- | --- |
| Purpose | Show how the claims, evidence, experiments, findings and sections hold together, where the argument has gaps, and what the project knows and does not know yet. |
| Input | `paper_dir`: the project's Git repository. `other_projects`: comma-separated paths of other project repositories whose findings are shared. `out_dir`: the report folder (default `<paper_dir>/drafts/research_map`). A missing repository raises `ValueError`. |
| Argument map | Plain code, `argument_map(paper)`: nodes claim (`C` ids of `claims.md`), evidence (`E` ids of `evidence.csv`), experiment (`X1`, `X2`, ... rows of `experiment_plan.csv`), finding (`F` ids of `findings.md`), section (`S:<stem>` of `plans/*.md`); edges `supports` (evidence → claim, supported finding → claim), `planned_for` (experiment → claim), `argued_in` (claim or evidence → section), `refutes` (refuted finding → the claim its hypothesis names). Gaps: claims without evidence (marked when no experiment is planned either), evidence for unknown claims, claims no section argues, experiments still planned or running. Template rows are not work. |
| Shared findings | The `findings.md` entries of the `other_projects` repositories and of the projects OpenProgram knows (`list_projects`), with the project's name. This project and folders that are not Git repositories are skipped. |
| Output | JSON with `status`, `report`, `map`, `world`, `run`, `gaps`, `open_questions`, `proposals`, `shared_findings`. `FAIL`: a claim has no evidence and no planned experiment. `PASS_WITH_NOTES`: other gaps, or relations dropped for their evidence. `PASS`: neither. Files in `<out_dir>/<timestamp>/`: `map.html` (argument map and world model, inline SVG, no script), `report.md` (Chinese: gaps, open questions, proposed next tasks), `run.json`, `run.md`. |
| Effects | Reads `workflow/` of this and the other projects, the project digest (`progress.digest(paper, full=True)`). Writes `workflow/world.json` (`entities`, `relations`, `open_questions`, `proposals`, `at`, `head`) and the report folder. |
| Model calls | `cartographer`: one call with the project digest, the argument map, the shared findings, the task list (`registry.TASKS` names and titles) and, on a later run, the previous world model to update. It returns entities (concept, method, dataset, metric, result, open_question), relations (improves, hurts, measured_by, evaluated_on, explains, contradicts, depends_on) each with evidence, open questions with why they matter and the cheapest next experiment, and up to five proposed tasks. |
| Checks | A relation is kept only when its endpoints are entities and every evidence item is an existing `C`/`E`/`F` id or `file:line` (or `file:a-b`) of a file in the project with that many lines; the others are dropped and counted. An entity or open question named as in the previous world model keeps its old id. A repeated entity id, an unknown entity type or a proposed task that does not exist raises. |
| Tests | `tests/worldmap/test_workflow.py`: the argument map and its gaps without a model, template files, a scripted cartographer, dropped relations, stable ids on a second run, shared findings from other repositories. |
| Usage | `from auto_research.tasks.worldmap import research_map, argument_map` |

## Not covered

- Judging whether evidence is strong: the map shows that a link exists, not that it convinces.
- Findings of other projects as evidence: they are context for the cartographer; a relation cites this project's ids or files.
