# paper_check task

The `paper_check` task of auto_research (`tasks/revision/`). auto_research calls `paper_revision_check()` with the section, `draft_dir`, `baseline`, `scope`, `phases` and the project's plan cards in `workflow/plans/`.

Every proposed change to the GLEE IAB camera-ready paper runs through this Workflow before the author sees it or it is written into the paper. It compares a draft with a committed baseline, checks every item of the AML writing guide, the venue rules and the author's own requirements, and saves a report with a red/green comparison PDF.

| Item | Contract |
| --- | --- |
| Purpose | Catch rule violations in a paper revision and list the manual checks still to do. |
| Input | `paper_dir`: the paper's Git repository. `draft_dir`: folder with the proposed revision; empty checks the working tree of `paper_dir`. `baseline`: Git revision to compare against (default `HEAD`), or `WORKTREE` for the current working tree of `paper_dir` with its uncommitted edits. `approved_removals`: float labels the author agreed to remove, comma-separated. `scope`: `changes` (default) sends added paragraphs to the reviewer, `paper` sends every paragraph. `plan_dir`: folder of section plan cards (`steps/prepare/plans.py`); each paragraph goes to the reviewer with the approved card of its section. `phases`: checklist phases by number, comma-separated (`4,6`); empty runs all eight (`rules/checklist.py`: 1 process, 2 integrity, 3 structure, 4 wording, 5 expression, 6 figures and tables, 7 LaTeX, 8 layout and submission). Only the chosen rows are checked and reported, and the reviewer reads only their rules. `sections`: section titles or their starts, comma-separated; the paragraph checks and the reviewer read only those sections, while the paper-wide checks follow `phases` alone. An unknown phase or section raises `ValueError` and lists the valid ones. A missing repository, main file or plan folder, or another scope, raises `ValueError`. |
| Output | JSON with `status` (`PASS`, `PASS_WITH_NOTES`, `FAIL`), the report path, the comparison PDF path, finding counts and `manual_items`. Files in `<paper_dir>/drafts/checks/<timestamp>/`: `report.md`, `report.json`, `draft.pdf`, `0main_appendix.pdf`, `diff.pdf`. |
| Effects | Builds copies in a temporary folder. Writes only into `drafts/checks/`. Never edits the paper sources. One `reviewer` model call per 15 paragraphs under review. |
| Failure and cancellation | A failed LaTeX build is a `FAIL` finding, not an exception. A comparison PDF that does not compile is a `WARN`. A malformed reviewer reply raises. Temporary copies are removed on exit; a partial report folder may remain. Rerunning is safe. |
| Dependencies | TeX Live with `latexmk`, `latexdiff`, `lualatex`, `lua-ul`; Poppler `pdftotext` and `pdffonts`; Git. |
| Tests | `tests/revision/`: one test file per step and per check group; `test_rules.py` checks that the checklist names only existing checks and rules; `test_workflow.py` runs the entry with the LaTeX tools replaced and a scripted reviewer. |
| Usage | `openprogram programs run auto_research -a paper_dir=<repo> -a task=paper_check -a draft_dir=<draft> -a scope=changes`; one section's wording only: `-a scope=paper -a phases=4,5 -a section="Workflow Evolution"` |

## Process

1. Plan the section first (the `auto_research` plan stage): what it must show, from which evidence, and what it cannot claim. Write only after the author approves the plan card.
2. Make the change in a draft copy, never in the paper.
3. Run this Workflow on the draft with `plan_dir`. Fix every `FAIL`; fix or explain every `WARN` and `REVIEW`; do the `TODO` items.
4. Show the author the comparison PDF and the remaining notes.
5. Write the change into the paper only after the author accepts it.

## Layout

| Path | Contents |
| --- | --- |
| `workflow.py` | The `reviewer` role, the review step and the entry. |
| `rules/` | What a revision must satisfy: venue, writing and LaTeX rules, and the item checklist. |
| `steps/prepare/` | Step 1: copy, build and read both versions into one `Paper` object. |
| `steps/checks/` | Step 2: deterministic checks on the `Paper`. |
| `steps/report/` | Step 4: one row per checklist item, the verdict, the saved report. |

Step 3, the review, lives in `workflow.py` because it calls the model.
