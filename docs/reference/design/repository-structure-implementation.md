# Repository structure implementation ledger

This ledger records implementation evidence for
[`repository-structure.html`](repository-structure.html). The HTML page is the
current design contract; this file is operational history.

## Scope

- Move the existing top-level CLI parser out of `openprogram/cli.py` without
  changing the public `openprogram.cli.build_parser` import or command grammar.
- Keep `docs/superpowers/` in Git but remove it from the public documentation
  build. Preserve linked `reference/design/plans/` pages in a separate design
  section.
- Group the existing UI design pages in navigation without moving their source
  files.
- Record, but do not perform, Desktop and Web bridge splits while those areas
  have active product changes.
- Keep the root and workspace READMEs aligned with the current Programs,
  packaged-skill, Web, and TUI ownership boundaries.
- Remove the Web Programs re-export directory when the public route can import
  the existing implementation directly.
- Consolidate Python CLI parser, handlers, REPL, Ink launcher, and setup
  sections under the stable `openprogram.cli` package without changing the
  public command tree.

## Workstreams

| Workstream | Branch / commit | Status | Integration note |
|---|---|---|---|
| Earlier repository inventory | `codex/file-structure-20260816` / `373af35c` | superseded | Useful inventory and repository contract remain review input; do not merge the old branch wholesale. |
| CLI parser split | `codex/code-doc-structure-20260817` / `e54bd425` | implemented | Move-only split; `openprogram.cli.build_parser` remains the public import. |
| Documentation information architecture | `codex/code-doc-structure-20260817` / `d37d9bab` | implemented | Adds the HTML design, implementation ledger, shared navigation rules, and five UI design groups. |
| Review repairs | `1be7295f`, `df3df11d` | implemented | Preserve the Design landing and make build/language checks share the public-source exclusion. |
| Main-branch synchronization | `2e3cda14` | integrated | Merges committed `main` at `acf13f8c`; no structural-file conflict. |
| Test and top-level structure maintenance | `codex/test-structure-20260817` / `dbe752eb` | implemented | Declares tracked top-level directories, relocates two root scripts, refreshes generated package READMEs, and removes obsolete active-guide paths. |
| Post-merge suite repair | `codex/test-structure-20260817` / `d04aa050` | implemented | Moves two real workflow execution tests to component, updates their package schema, and repairs merged documentation, distribution, and optional-dependency regressions. |
| Documentation entry points and Programs alias | `codex/repository-structure-batch1-20260817` / `d9cc9933` | implemented | Refreshes the bilingual project map and workspace READMEs; removes only the confirmed re-export and unused stylesheet. |
| Documentation-entry main integration | `29f8186d` | integrated | Two-parent merge with current `main` at `e2041770`; unrelated local icon and output files remain outside Git. |
| Python CLI package consolidation | `codex/python-package-layout-cli-20260817` / `d2ae71cc` | implemented; review repair pending | Replaces three first-level internal CLI directories and four implementation files with one `openprogram/cli/` package; keeps `openprogram.cli`, the console script, and both module entry points. |

## Verification record

```text
5 passed — focused CLI parser and documentation navigation contracts
207 passed — post-main-merge CLI component/unit tests plus focused structure contracts
158 passed, 2 skipped — independent quality selection of CLI-named and docs IA tests
497 pages — python -m tools.docs_site.build
0 broken links — python -m tools.docs_site.checklinks docs/_site
ok — python -m tools.docs_site.check_landing
0 Chinese lines — python -m tools.docs_site.checklang
pass — Ruff and git diff --check
pass — desktop render at 1280 × 720, no horizontal overflow
pass — narrow render at 390 × 844, no page overflow; wide tables scroll locally
pass — independent specification review at df3df11d
pass — independent quality review at df3df11d, including wheel-content and isolated-import probes
442 passed, 1 skipped — current repository contracts
5699 passed, 16 skipped, 1 xfailed — fixed -n 4 required selection, repeated three times with 0 worker crash
2560 passed, 3 skipped; 42.402765% — Python 3.12.13 serial unit branch coverage, repeated twice
503 pages; 0 broken links; landing and language checks pass — current documentation build
pass — Desktop full npm run check after adding update-service.js to build.files
0 new, 0 refreshed — second package README generator run
pending — independent specification and quality review for dbe752eb..d04aa050
444 passed, 1 skipped — complete repository contract suite for the documentation-entry batch
43 passed — repository-layout and documentation-information-architecture selection
pass — Web full check, 10 unit tests, TypeScript no-emit check, and 31-page production build
505 pages; 0 broken links; landing and language checks pass — documentation-entry candidate
0 new, 0 refreshed — second package README generator run for the candidate
pass — independent specification review for d9cc9933
pass — independent quality review for d9cc9933, including distribution, Web build, README fact, and HTML structure probes
10 passed — post-merge repository-layout and documentation-information-architecture selection
pass — post-merge Web full check, 10 unit tests, TypeScript no-emit check, and 31-page production build
505 pages; 0 broken links; landing and language checks pass — post-merge documentation build
388 passed, 4 skipped — all tests directly changed by the Python CLI package consolidation
5664 passed, 11 skipped, 1 xfailed — complete contracts, unit, and component suites for `d2ae71cc`
pass — Ruff on every changed Python file and `git diff --check`
pass — wheel contains the new CLI package paths and no removed implementation paths; isolated import and `python -m openprogram.cli --help` succeed
506 pages; 0 broken links; landing and language checks pass — Python CLI package consolidation documentation build
changes required — first independent specification review of `d2ae71cc` found only current-document path and evidence-record repairs; code and package compatibility passed
```

## Deferred boundaries

- `desktop/main.js`: extract existing window lifecycle, native WebView, tab
  transfer, and menu responsibilities after the current Desktop work lands.
- `web/lib/desktop-bridge.ts`: establish executable state/transfer tests before
  moving types, view state, and transfer coordination.
- Long cohesive Python state machines remain intact until a separately tested
  responsibility is identified; line count alone is not an implementation task.
