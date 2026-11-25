# submission tools

Three plain-code tools for the step before a paper goes out (`tasks/submission/`). None makes a model call. Each takes the project's Git repository as `paper_dir` (anything else raises `ValueError`), writes into `<out_dir>/<timestamp>/` with a trail (`run.json`, `run.md`), and returns JSON with `status`, `report` and `run`. The main `.tex` is `main_tex` from `workflow/rules.json`, or the one top-level `.tex` with `\documentclass`.

Learned from Overleaf's error assist, Paperpal's pre-submission check, venue format checkers, Google's arxiv_latex_cleaner and the Agents4Science AI-involvement checklist.

## submission_check

| Item | Contract |
| --- | --- |
| Purpose | Stop what a venue's format check or a desk reject would catch, before the paper is uploaded. |
| Input | `paper_dir`, `out_dir` (default `<paper_dir>/drafts/submission_check`). Reads `<main>.pdf` and `<main>.log` next to the main `.tex`, the `.tex` files it inputs, `workflow/venue.md` and `rules.json` notes. |
| Findings | Each `{kind, level, where, problem, fix}`, problem and fix in Simplified Chinese. From the log: LaTeX errors with `file:line` (`! ...` plus its `l.<n>` line, or `-file-line-error` lines), undefined references and citations, multiply defined labels (note), overfull hboxes over 10pt (note). Page count (pypdf, else `pdfinfo`, else a note) against the limit in venue.md (`8 pages`, `page limit: 9`; else rules.json `body_page_limit` when the paper sets it). Body and references cannot be separated reliably: the total is compared, and when it exceeds the limit the page with the References heading decides between a note and an overrun. When venue.md or the rules say double-blind or anonymous submission: `\author` with names outside an anonymous style mode, an acknowledgements section, GitHub/GitLab or personal homepage links, first-person references to the authors' earlier work, a non-empty PDF Author field. Fonts not embedded (`pdffonts`, note). PDF over 50 MB, a top-level `.zip` over 100 MB (notes). |
| Output | `FAIL`: a LaTeX error, an undefined reference or citation, a missing PDF, a page overrun or an anonymity breach. `PASS_WITH_NOTES`: notes or skipped checks. `PASS` otherwise. JSON also has `findings`, `errors`, `notes`, `pages`, `page_limit`, `double_blind`, `tools`. Files: `report.md` (Chinese), `submission.json`. |
| Effects | Runs `pdfinfo` and `pdffonts` when on PATH. Writes only the report folder. |

## arxiv_package

| Item | Contract |
| --- | --- |
| Purpose | A clean source bundle for arXiv or the camera-ready upload. |
| Input | `paper_dir`, `out_dir` (default `<paper_dir>/drafts/arxiv_package`). |
| Process | Follows the main `.tex` through `\input`, `\include`, `\includegraphics` (with `\graphicspath` and extension lookup), `\lstinputlisting`, `\bibliographystyle`, `\usepackage`, `\documentclass` to files in the repository; packages not in the repository belong to the TeX installation. Adds `<main>.bbl`. Strips comments from the `.tex` files: whole-line comments go, a trailing comment keeps its `%`, `\%` and `%` inside `\url{}` stay, verbatim-like environments stay as written, `\begin{comment}` blocks go. `.bib`, `drafts/`, `workflow/`, `.git` and unused files are left out. |
| Output | `FAIL` (and no zip): `<main>.bbl` is missing or a referenced file is missing. `PASS_WITH_NOTES`: the files total over 50 MB or a figure is over 10 MB, each with a compression hint. JSON also has `zip`, `manifest`, `included`, `dropped`, `total_bytes`, `problems`, `warnings`. Files: `arxiv.zip`, `manifest.md` (Chinese), `manifest.json` (included and dropped files with sizes). |
| Effects | Writes only the report folder. |

## ai_disclosure

| Item | Contract |
| --- | --- |
| Purpose | A draft of the AI-use statement venues now ask for, for the author to edit. It never writes into the paper. |
| Input | `paper_dir`, `out_dir` (default `<paper_dir>/drafts/ai_disclosure`). Reads `drafts/**/run.json` (workflow, finished, roles, model stages, usage) and the Git log: commits whose `Co-Authored-By:` trailer names an AI tool, sorted into writing (`.tex`, `.bib`), code and figures by the files they touched. |
| Stages | `idea_generation`, `research_survey`, `related_work_watch` and other survey tasks: ideation and literature. Experiment, baseline and finding workflows: experiments. `paper_review`, `paper_proofread`, `paper_revision_check`: writing and review. `paper_figures`: figures. `citation_check`: references. |
| Output | Always `PASS`. JSON also has `stages`, `runs`, `ai_commits`, `model_calls`, `cost`. File: `ai_disclosure.md` with one English paragraph per stage (what the tools did, that the authors verified the results), a Chinese summary table, the model calls and cost, and the run records it read. |
| Effects | Runs `git log`. Writes only the report folder. |

## Tests

`tests/submission/test_submission.py`: a tiny repository with a fake `.log`, a PDF made with pypdf (those asserts skip without it) and a venue.md asking for 8 pages and double-blind review.

## Usage

`from auto_research.tasks.submission import submission_check, arxiv_package, ai_disclosure`

## Not covered

- Rendering problems the log does not report (a figure that looks wrong, text cut off by a float).
- Names in the PDF text itself when the author block is hidden by the style; the source is checked.
- Venue-specific forms (checklists in the submission system, OpenReview fields).
