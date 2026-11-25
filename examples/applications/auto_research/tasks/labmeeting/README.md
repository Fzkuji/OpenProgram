# labmeeting

The `lab_meeting` task of auto_research (`tasks/labmeeting/`). The author, as PI, puts a question to a small team of agents: specialists the question needs and a fixed Scientific Critic. They discuss it over the project's own files for a few rounds, and a PI assistant writes what was decided, what is still disputed and who does what next. Every fact a member states cites a file line or a row id, and code checks it.

Learned from Stanford's Virtual Lab (a human PI with an agent team that holds team meetings over several rounds with a scientific critic, and summaries that later meetings build on).

## lab_meeting

| Item | Contract |
| --- | --- |
| Signature | `lab_meeting(paper_dir, agenda, rounds="2", members="", out_dir="")` |
| Purpose | The author gets a discussed, cited answer to one research question and a list of next steps mapped to tasks or experiment-plan rows. |
| Input | `paper_dir`: the project's Git repository. `agenda`: the question (required). `rounds`: 1 to 5, empty is 2. `members`: empty (the planner picks three specialists) or a comma list of 1 to 5 role names; "Scientific Critic" in it is ignored, as the critic always joins. `out_dir`: default `<paper_dir>/drafts/lab_meeting`. An empty agenda, rounds out of range, more than five roles, a role over 60 characters, a folder that is not a Git repository, or a `workflow/` with none of the files below raises `ValueError`. |
| Gather | `workflow/` `project.md`, `claims.md`, `evidence.csv`, `results.tsv`, `experiment.md`, `experiment_plan.csv`, `findings.md`, `baselines.csv` (30k characters each), the files experiment.md's `Edit` names (code and configs, at most 5, 20k each, no `..` or absolute paths), the main .tex (60k), and the newest file of `workflow/meetings/` (20k), all with line numbers. Row ids: claims `C1`, evidence ids, results.tsv commits, `X1`... for experiment_plan.csv rows in order, `F1`... for findings. Task names and titles come from the task table of the program's README. |
| Process | `planner` chooses the team (role, expertise, goal). Each round every member speaks in order and the critic last; each speaker sees the agenda, its own persona, the team, the files, the previous meeting's Summary and the checked transcript so far. A speech is 1 to 8 statements, each `fact`, `opinion` or `proposal`, with `cites` and an optional `quote`. The `secretary` (PI assistant) then writes the answer, decisions with cites, open disagreements with each member's position, and 1 to 10 action items with an owner, a task name and/or a proposed experiment_plan.csv row (`claim`, `experiment`, `kind`). |
| Check (code) | A citation (`path:line`, `path:start-end` of at most 30 lines, a unique file name instead of the path, or a row id) that points nowhere is dropped and flagged. A quote not found in the cited lines (whitespace, `~`, `\` and case ignored) is dropped. A fact with no valid citation is marked. In facts, opinions and decisions, a number that the cited lines or the agenda do not hold (equal, or equal after rounding the source to the stated digits) is replaced by `[number removed]`; proposals keep their numbers (they are settings to try). The answer's numbers must occur somewhere in the files. An unknown task is dropped; a plan row needs an experiment and a kind of `checks.PLAN_KINDS`, its claim is an existing id or empty (a claim the project does not have yet), and a row already in experiment_plan.csv is not proposed again. |
| Output | JSON with `status`, `report`, `meeting`, `run`, `members`, `statements`, `flagged`, `decisions`, `actions`, `plan_rows`. `FAIL`: facts were stated and none had a valid citation. `PASS_WITH_NOTES`: a flagged statement or decision, or a summary note. `PASS` otherwise. Files: `workflow/meetings/<date>-<slug>.md` (English: team, transcript by round, Summary with answer, decisions, disagreements, action items and the proposed rows as CSV; `-2`, `-3` when the name is taken), and in `<out_dir>/<timestamp>/` `report.md` (Chinese), `meeting.json`, `run.json`, `run.md`. |
| Effects | Writes the meeting file under `workflow/meetings/`; the next meeting reads its Summary. Never writes experiment_plan.csv or any other project file. |
| Model calls | `planner` 1, `member` rounds × specialists, `critic` rounds, `secretary` 1: 10 calls with the defaults. |
| Failure | A reply that is not the right shape (wrong number of members, a changed requested role, a repeated role or a critic in the team, a speech without statements, a summary without answer or actions) raises. Rerunning is safe; each run adds a meeting file. |
| Tests | `tests/labmeeting/test_meeting.py`: scripted roles; the context each speaker sees (files with line numbers, Edit files, the critic hearing the round), every citation, quote and number check, plan-row mapping, the meeting file and its name, the next meeting reading the last Summary with named roles, input errors, an ungrounded meeting failing, and citation resolution. |
| Usage | `from auto_research.tasks.labmeeting import lab_meeting` |

## Not covered

- Individual meetings (one agent and the critic iterating on one piece of work) and parallel meetings merged into one, which Virtual Lab also has.
- Members running code or searching the literature during the meeting; they read the files only.
- Writing the proposed plan rows; the author adds them.
