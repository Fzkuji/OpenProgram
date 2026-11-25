# venue tools

Four tools for the steps around a venue (`tasks/venue/`): the camera-ready check, the anonymous supplementary bundle, moving to another venue's template, and the submission-form fields. Each takes the project's Git repository as `paper_dir` (anything else raises `ValueError`), writes into `<out_dir>/<timestamp>/` with a trail (`run.json`, `run.md`) and returns JSON with `status` and `report`. The main `.tex` and the files it reaches come from `tasks/submission/common.py`. Only `submission_metadata` makes a model call.

Learned from ACM TAPS and IEEE PDF eXpress (final-version checks), Anonymous GitHub (scrubbing a repository for double-blind review), Typeset/SciSpace Formatter (moving between templates) and the OpenReview and ARR submission forms (TL;DR, keywords, primary area).

`workflow/venue.md` fields the tools read, one `Key: value` line each (a list bullet is allowed): `Name:` (venue family), `Camera-ready pages:`, `Funding:` (`;`- or `,`-separated, grant numbers as written), `Authors:`, `Affiliations:`.

## camera_ready_check

| Item | Contract |
| --- | --- |
| Signature | `camera_ready_check(paper_dir, out_dir="")` |
| Purpose | Catch what a publisher's production check returns on a de-anonymized final version. |
| Input | The main `.tex` and the `.tex` it inputs, `<main>.pdf`, `workflow/venue.md`. The family comes from the style the source loads, else venue.md `Name:`. |
| Errors | `\author` missing, empty or Anonymous. Review switches left on: `review`, `anonymous`, `peerreview`, `submission` in `\documentclass` or a venue style; NeurIPS without `final`; ICML or TMLR without `accepted`; ICLR without `\iclrfinalcopy`. `\linenumbers`. A `Funding:` item whose grant numbers (or name, when it has none) are not in the acknowledgments. acmart: no `\begin{CCSXML}`, no `\ccsdesc`, no `\setcopyright`. ACM figures without `\Description{}`. `Anonymous` left in the text. `TODO`, `FIXME`, `XXX`, `\todo`, `\fixme`, `\missingfigure`, `\red{}`, `\hl{}`, `\textcolor{red}`, `\color{red}` outside macro definitions. More PDF pages than `Camera-ready pages:` unless the references start within the limit. |
| Notes | No acknowledgments section. NeurIPS `preprint`. `todonotes` without `disable`. acmart without `\acmDOI`/`\acmConference` or `\keywords`. Figures without alt text outside ACM: `\includegraphics[alt={...}]`, `\alt{}` or a `% alt:` comment count. No PDF, no page counter, or no `Camera-ready pages:`. |
| Output | `FAIL` on any error, `PASS_WITH_NOTES` on notes, else `PASS`. JSON: `findings`, `errors`, `notes`, `family`, `pages`, `page_limit`. Files: `report.md` (Chinese, `file:line` for each finding), `camera_ready.json`. Default folder `drafts/camera_ready_check/`. |

## anonymous_package

| Item | Contract |
| --- | --- |
| Signature | `anonymous_package(paper_dir, include="", out_dir="", fix="list")` |
| Purpose | A supplementary.zip of the code and extra files that does not identify the authors. |
| Files | Working-tree files from `git ls-files --cached --others --exclude-standard` (no Git history, `.gitignore` honoured). `include`: comma-separated globs, a folder name takes everything under it. Default: everything except `drafts/`, `workflow/`, the files the main `.tex` uses, the main PDF, `.tex`/`.bib`/`.bbl`/`.sty`/`.cls`/`.bst` and LaTeX build files. Always dropped: `.DS_Store`, `__pycache__`, `.pyc`, `.ipynb_checkpoints`, `.env*`, `.netrc`, `.pypirc`, SSH keys, `.pem`/`.key`/`.p12`, credentials and secrets files, `.idea`, `.vscode`. |
| Scrubbing | Notebooks: outputs, execution counts and cell metadata cleared, kernelspec reset to `python3`, `language_info` reduced to its name. Text files are scanned line by line for author names (rules.json `required_authors`, venue.md `Authors:`, `git log` names), emails, venue.md `Affiliations:`, `/Users/<name>/`, `/home/<name>/`, `C:\Users\<name>\`, and GitHub, GitLab and Hugging Face accounts (from URLs, the remote and noreply emails). Binary files are included and listed for a manual look. Zip entries carry no timestamps. |
| fix | `list`: each finding with `file:line`, `FAIL`, no zip. `replace`: names, institutions and accounts become `Anonymous`, emails `anonymous@example.com`, home paths `/Users/anonymous`; `PASS_WITH_NOTES`. |
| Output | JSON: `zip`, `manifest`, `included`, `removed`, `findings`, `blocked`, `notebooks_cleaned`. Files: `supplementary.zip`, `manifest.md` (Chinese), `manifest.json`. Default folder `drafts/anonymous_package/`. The repository is not changed. |

## venue_move

| Item | Contract |
| --- | --- |
| Signature | `venue_move(paper_dir, target, out_dir="", write_venue="no")` |
| Purpose | A migration plan after a rejection; the page agent edits the paper and then runs `submission_check`. |
| Targets | `facts.FAMILIES`: iclr, neurips, icml, arr (acl, emnlp, naacl, eacl), cvpr (iccv), eccv, aaai, ijcai, acm (kdd, www, sigir, cikm, wsdm, chi, mm), ieee, tmlr, jmlr, arxiv. Each has the submission and camera-ready preamble, bib style, main and camera-ready page limits, how references and appendices count, anonymity, required sections, checklist, notes and the official template URL. An unknown target raises `ValueError`. |
| Plan | Preamble lines to remove (the current template's) and to add; `\bibliographystyle` change; sections to add (NeurIPS Paper Checklist, ARR Limitations, ICML Impact Statement, ICLR statements, ACM CCS concepts and keywords, AAAI reproducibility checklist) and to drop (the old venue's, acknowledgments for an anonymous target); page budget from the PDF (body = pages before the References heading, else the total) against the target limit; anonymity switch with its to-do list; numbered steps. |
| write_venue | `yes`: writes a new `workflow/venue.md` for the target (name, `page limit: N pages`, double-blind or single-blind, `Camera-ready pages:`, requirements, checklists; `Authors:`, `Affiliations:`, `Funding:` carried over), the old one renamed `venue.<old family>.md`. Nothing else in `workflow/` changes; the paper is never edited. |
| Output | Always `PASS`. JSON: `plan`, `from`, `to`, `pages`, `steps`, `venue_md`. Files: `plan.md` (Chinese), `plan.json`. Default folder `drafts/venue_move/`. |

## submission_metadata

| Item | Contract |
| --- | --- |
| Signature | `submission_metadata(paper_dir, out_dir="")` |
| Purpose | The form fields besides the PDF, drafted for the author to paste. |
| Roles | `metadata_writer` (one call): TL;DR ≤ 250 characters, 5–8 keywords, primary area, one-sentence lay summary; the area is an enum when the venue has a list. |
| Areas | `areas.json`: ICLR, NeurIPS, ICML and ARR primary-area lists; other venues are free text. |
| Checks | Plain code: TL;DR length, every number in the TL;DR occurs in the paper source, 5–8 distinct keywords, area on the list, lay summary one sentence ≤ 300 characters. |
| Output | `FAIL` when a check fails, else `PASS`. JSON: `report`, `metadata`, `venue`, `problems`. Files: `workflow/submission_metadata.md` (overwritten), and `metadata.json` with the trail in `drafts/submission_metadata/<timestamp>/`. |

## Tests

`tests/venue/test_venue.py`: fixture repositories with ACM, NeurIPS, ICML, ICLR, ACL, CVPR and TMLR snippets; a code folder with a name, an email, a home path, a GitHub URL, a notebook with outputs and secrets; a NeurIPS paper moved to EMNLP and JMLR; a scripted `metadata_writer`. PDF asserts skip without pypdf.

## Usage

`from auto_research.tasks.venue import camera_ready_check, anonymous_package, venue_move, submission_metadata`

## Not covered

- Rendering problems in the PDF (fonts, image resolution) that PDF eXpress reports: `submission_check` covers fonts.
- Names inside binary files (PDF metadata, image EXIF) of the supplementary bundle; they are listed for a manual look.
- Editing the paper for a venue move, and filling in the submission system itself.
- Template years and page limits change each cycle: the plan points at the current kit.
