# outreach tools

Six tools for after a paper is accepted or released (`tasks/outreach/`): the dataset's Croissant metadata, the project page with a social thread, and the Lean formalization of the paper's theorems. Each takes the project's Git repository as `paper_dir` (anything else raises `ValueError`), writes into `<out_dir>/<timestamp>/` with a trail (`run.json`, `run.md`), and returns JSON with `status` (`PASS`, `PASS_WITH_NOTES`, `FAIL`), `report`, `json` and `run`. Reports are in Simplified Chinese. The main `.tex` is `main_tex` from `workflow/rules.json`, or the one top-level `.tex` with `\documentclass`.

Learned from MLCommons Croissant and its RAI vocabulary, Hugging Face dataset cards, Paper2Web and AutoPage, and the Lean/mathlib provers; lean_prove from ByteDance Seed-Prover, DeepSeek-Prover and Harmonic's Aristotle (a model proposes, Lean checks, Lean's errors drive the next attempt).

## croissant_check

| Item | Contract |
| --- | --- |
| Purpose | The dataset's Croissant metadata passes what NeurIPS Datasets & Benchmarks and dataset hubs check. |
| Input | `paper_dir`, `path` (default `workflow/croissant.json`; relative to `paper_dir` or absolute), `out_dir` (default `<paper_dir>/drafts/croissant_check`). |
| Validation | With `mlcroissant` importable: `mlcroissant.Dataset(jsonld=...)`; each line of its error is an error, its warnings are notes. Otherwise plain code against Croissant 1.0: `@context` mapping `cr` and `sc` (or `@vocab`) and `rai` when `rai:` fields are used; `@type` `sc:Dataset`; `conformsTo` `http://mlcommons.org/croissant/1.x`; `name`, `description`, `license`, `url`; a `distribution` of `cr:FileObject` (`contentUrl`, `encodingFormat`, `sha256` or `md5` unless `containedIn`) and `cr:FileSet` (`containedIn`, `includes`, `encodingFormat`); unique `@id`s; `containedIn` naming a known file; `recordSet`s of `cr:RecordSet` whose `cr:Field`s have `dataType` and a `source` naming a known file object, file set or record set; the sha256 of local `contentUrl`s (resolved from the JSON's folder, the paper, `workflow/`). `creator`, `datePublished`, `version`, `keywords`, `citeAs` are notes. In both modes: RAI fields `rai:dataCollection`, `rai:dataBiases`, `rai:personalSensitiveInformation`, `rai:dataUseCases`, `rai:dataLimitations` are required; `rai:dataCollectionType`, `rai:dataSocialImpact`, `rai:dataPreprocessingProtocol`, `rai:dataAnnotationProtocol`, `rai:dataReleaseMaintenancePlan` are notes. A value containing `TODO` counts as missing. |
| Output | `FAIL` on any error (missing or invalid file included), `PASS_WITH_NOTES` on notes. JSON also has `validator`, `errors`, `notes`. Files: `report.md`, `croissant.json` (findings `{level, where, problem, fix}`). |
| Effects | Writes only the report folder. |

## croissant_draft

| Item | Contract |
| --- | --- |
| Purpose | A first `workflow/croissant.json` from the datasheet, so only the gaps are left to write. |
| Input | `paper_dir`, `out_dir` (default `<paper_dir>/drafts/croissant_draft`). Reads `workflow/datasheet.md` (the template when missing): `License`, `Location`, optional `Name`, `Description`, `URL`, `Creator` (comma-separated), `Version`, `Date`; parts Motivation, Collection process, Preprocessing, Uses, Maintenance and any part named for biases, sensitive/personal information, limitations or impact. |
| Process | Files: `Location` when a local file or folder (up to 200 files), paths in backticks or list items of the Distribution part that exist, URLs there. Local files get `sha256`, `contentSize`, `encodingFormat` by extension and `contentUrl` relative to `workflow/`; CSV/TSV/JSONL files get a record set with one field per column, `dataType` guessed from the first row. The name falls back to the paper's `\title`. Unknown values are `TODO: ...` strings. |
| Output | `PASS_WITH_NOTES` while TODOs remain (or without a datasheet), else `PASS`. JSON also has `croissant` (the draft), `workflow_file` (set when it was written), `todo` (count). Files: `croissant.json`, `report.md` (files and TODO paths), `draft.json`. |
| Effects | Writes `workflow/croissant.json` only when it does not exist. |

## project_page

| Item | Contract |
| --- | --- |
| Purpose | A reviewed project page and announcement thread whose every number is the paper's. |
| Input | `paper_dir`, `out_dir` (default `<paper_dir>/drafts/project_page`), `publish` (`"no"` default; `"yes"` copies `docs/` into `<paper_dir>/docs/`). |
| Role | One `webwriter` call (`write_page`, schema `project_page`) with the title, abstract, sections and figure captions: TL;DR, summary (4-6 sentences), 3-5 results (headline, detail), method, up to four figures by id with captions, 3-5 posts. |
| Grounding | Every number with two or more digits or a decimal point must occur in the paper source. TL;DR, a result, a caption or a post that breaks this is dropped whole; summary and method lose the sentence. A post over 280 characters is dropped. A dropped caption falls back to the paper's. All recorded in `dropped`. |
| Render | Self-contained `docs/index.html` (inline CSS, light and dark): title; authors and affiliations from `\author` (split at `\and`, first line the name, e-mails left out), or placeholders when anonymous (an `anonymous`/`review` class option, a NeurIPS/ICML style without its final option, a double-blind venue, "Anonymous" in `\author`); link buttons for the compiled PDF (copied as `assets/paper.pdf`, or notes `paper_pdf` URL), arXiv (notes `arxiv` or any arxiv.org URL in notes), code (`code.repo`) and existing deliverables (copied to `assets/`); figures from the paper's `figure` environments (PNG/JPG/SVG copied, PDF converted with `pdftoppm -png -r 150 -singlefile`); a BibTeX block. `thread.md` holds the posts. |
| Links | Every `href`/`src`: a local file must exist (error); an http link gets a HEAD request (GET when HEAD is refused), timeout 10 s; an unreachable one is a note. |
| Output | `FAIL`: a broken local link, or no TL;DR, summary or result survived. `PASS_WITH_NOTES`: dropped content, notes (unreachable links, missing pdftoppm, unconvertible figures) or an anonymous paper. JSON also has `page`, `thread`, `published`, `dropped`, `figures`. Files: `docs/` (page, `figures/`, `assets/`), `thread.md`, `page.json`, `report.md`. |
| Effects | Runs `pdftoppm`, HEAD requests. Writes the report folder, and `<paper_dir>/docs/` only with `publish="yes"` and no FAIL. |

## lean_check

| Item | Contract |
| --- | --- |
| Purpose | Which of the paper's theorems are formally proved in Lean 4. |
| Input | `paper_dir`, `out_dir` (default `<paper_dir>/drafts/lean_check`). Reads theorem, lemma, proposition, corollary, claim and conjecture environments (and `\newtheorem` names with such titles) with their `\label`, and `workflow/lean/**/*.lean` (`.lake/` and the lakefile skipped). |
| Mapping | A `-- paper: thm:label` comment (several labels comma-separated) in the comment/attribute lines right above a declaration maps it to the paper. |
| Build | With `lake` on PATH: `lake build` in `workflow/lean/`, timeout 30 minutes. Each `file:line:col: error\|warning: message` (with or without lake's prefix) goes to the declaration whose lines hold it: an error fails it, `declaration uses 'sorry'` marks it. An error outside any declaration fails that file's declarations and is a build error; a nonzero exit without any Lean error fails everything. Without lake: the source is read only (a textual `sorry` still shows), and the report gives the elan install command. |
| Output | Per theorem: `proved`, `sorry`, `fails`, `not_formalized`, `unchecked`. `FAIL`: a theorem fails or the build failed or timed out. `PASS_WITH_NOTES`: sorry, unformalized or unchecked theorems, unlabeled theorem environments, `-- paper:` labels the paper lacks, no lake or no `workflow/lean/`. JSON also has `counts`, `built`. Files: `report.md`, `lean.json`, `build.log`. |
| Effects | Runs `lake build`. Writes only the report folder. |

## lean_draft

| Item | Contract |
| --- | --- |
| Purpose | The Lean 4 statement of one theorem, for the author or an agent to prove. |
| Input | `paper_dir`, `label` (`thm:main`, or `main` when only one label ends so), `out_dir` (default `<paper_dir>/drafts/lean_draft`). |
| Role | One `formalizer` call (`write_statement`, schema `lean_statement`) with the statement, the paper's definition environments and the surrounding source: imports, code (definitions and the theorem, its proof `sorry`), assumptions (interpretations it had to choose). |
| Checks | The reply has a theorem (the named one, else the last), its body ends in `sorry` or `by sorry`, and no `axiom`; otherwise `ValueError`. Imports inside the code are moved out; a model-written `-- paper:` comment is replaced. |
| Output | `workflow/lean/<Label>.lean` (Label in CamelCase) with `-- paper: <label>` above the theorem (its doc comment kept attached); an existing file is not overwritten. Without a lakefile: `lakefile.toml` requiring mathlib with lib `Paper`, `.gitignore` for `.lake/`; a lakefile this tool wrote gets the module added to `roots`. `PASS_WITH_NOTES` with assumptions or notes (file kept, lake missing). JSON also has `file`, `draft`, `module`, `lakefile_created`. Files: the `.lean` draft, `report.md` (code, assumptions, first-build commands), `draft.json`. |
| Effects | Writes `workflow/lean/` as above and the report folder. |

## lean_prove

| Item | Contract |
| --- | --- |
| Signature | `lean_prove(paper_dir, theorem="", attempts="4", out_dir="")` |
| Purpose | The `sorry`s of the Lean formalization replaced by proofs Lean accepts, without touching the statements. |
| Input | `paper_dir`; `theorem` (default all: every theorem/lemma under `workflow/lean/` whose proof has `sorry`; else comma-separated declaration names or `-- paper:` labels, `sum` finds `thm:sum`; an unknown name raises `ValueError`); `attempts` (prover rounds per declaration, default 4, clamped to 1..10); `out_dir` (default `<paper_dir>/drafts/lean_prove`). |
| Process | `lake build` once (imports). Per declaration in file order, proofs accepted earlier staying in the working copy: (1) automation in one compile, `first \| (rfl; done) \| (decide; done) \| (omega; done) \| (simp; done) \| (simp_all; done) \| (simp_arith; done)` plus `norm_num`, `linarith`, `nlinarith`, `positivity`, `ring`, `aesop` with mathlib, then the first single tactic that passes; its `unsolved goals` error gives the goal. (2) up to `attempts` calls of the `prover` role (`propose`, schema `lean_proof`: `kind` tactic/term, `proof`, `idea`) with the declaration, the file's imports and earlier declarations, the goal, and every earlier attempt with Lean's full error messages. |
| Acceptance | The proof replaces only the text after the declaration's top-level `:=` (comments, attributes and commands after it are kept) in a scratch copy, compiled with `lake env lean` (10 minutes) and `#print axioms <qualified name>` appended. Accepted only when: no error and no `declaration uses sorry` inside the declaration; no error elsewhere in the file that the original lacked (compared as declaration + message); no `sorry`, `admit`, `sorryAx`, search tactic (`exact?`, `apply?`, ...), declaration or command in the proof, no `native_decide` unless the project already uses it; the normalized statement before `:=` unchanged; axioms only `propext`, `Classical.choice`, `Quot.sound` (plus `Lean.ofReduceBool` when `native_decide` is allowed). |
| Output | `PASS` every target proved; `PASS_WITH_NOTES` some remain, or nothing ran (no lake, no `workflow/lean/`, no target); `FAIL` when the written project had a new error and was restored. JSON also has `proved`, `remaining`, `written`. Files: `report.md` (per declaration: proved or not, how, model rounds, the proof, the last error), `prove.json`, `scratch/` (compiled copies and logs), `backup/<file>.bak`. |
| Effects | Runs `lake build` and `lake env lean`. Writes the proved files in `workflow/lean/` (originals in `backup/`), then runs `lake build` and compiles each changed file again; any error not there before restores every file. |
| Tests | `tests/outreach/test_prove.py`: Lean replaced by a fake that answers like `lake env lean` (sorry warnings, unsolved goals, header-less `#print axioms` lines), prover scripted; automation, error-driven second attempt, rejected proofs, non-standard axioms, restore on a new build error, pre-existing errors elsewhere; one real-Lean test when `lake` works. |
| Not covered | Whether the statement says what the paper means; proofs of `def`s and `instance`s; declarations proved by pattern-matching equations (no `:=`). |

## Tests

`tests/outreach/`: `test_croissant.py` (fixtures `croissant_valid.json`, `croissant_invalid.json`, `data.csv`; a stand-in `mlcroissant`), `test_page.py` (scripted webwriter, `pdf_to_png` and `head` replaced), `test_lean.py` (`lake build` output through a monkeypatched `subprocess.run`, scripted formalizer), `test_prove.py` (fake Lean, scripted prover).

## Usage

`from auto_research.tasks.outreach import croissant_check, croissant_draft, project_page, lean_check, lean_draft, lean_prove`

## Not covered

- Hosting the page (GitHub Pages settings) and posting the thread.
- Whether a Lean statement says what the paper means; the assumptions list is for the author to check.
- Croissant record sets for formats other than CSV, TSV and JSONL.
