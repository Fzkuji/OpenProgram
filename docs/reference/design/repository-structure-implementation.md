# Repository structure implementation ledger

This ledger records implementation evidence for
[`repository-structure.html`](repository-structure.html). The HTML page is the
current design contract; this file is operational history.

## Scope

- Migrate from the historical top-level `web/`, `desktop/`, `cli/` and
  `openprogram/webui/` layout to a core-first monorepo:
  `openprogram/` remains the Agent core and SDK, while runnable products live
  under `apps/{server,web,desktop,cli}`.
- Preserve the single-port `18100` runtime, HTTP/WS contracts, user data,
  browser/desktop behavior, console commands, and required compatibility
  imports throughout the migration.
- Keep generated Web output out of the core source package. Stage it only for
  source execution, wheel construction, or Electron packaging.
- Remove the confirmed-unused legacy static UI and dual-port Next.js process
  only after current callers and release paths use the new application roots.
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

## Approved migration sequence

| Batch | Target | Required compatibility and evidence |
|---|---|---|
| A | Canonical design and repository contract | HTML design names the core/application boundary; repository contract fails while old roots remain. |
| B1 | Ink TUI `cli/` workspace to `apps/cli/` | Node typecheck/tests/build, Python bundle resolution and the real Node auth client use the new path. |
| B2 | Python CLI application assembly to `apps/cli/python/openprogram_cli/` | Existing console scripts, `python -m openprogram`, `openprogram.cli.build_parser`, Ink TUI launch and parser snapshots remain valid through compatibility imports. |
| C | `openprogram/webui/` to `apps/server/openprogram_server/` | FastAPI app, REST/WS paths, owner auth, worker startup and package imports remain valid through bounded compatibility modules. |
| D | `web/` to `apps/web/` | Node checks, TypeScript, static export, Python static serving and release asset staging use the new root. |
| E | `desktop/` to `apps/desktop/` | Electron checks, runtime packaging, updater, embedded browser and installed-App scripts use the new root. |
| F | Legacy deletion and compatibility exit | Old static UI, unused dual-port launcher and expired import shims are absent; complete Python/Web/Desktop/docs/package gates pass. |

Each batch is move-only unless a directly exposed path or import must be
adapted. Large-file decomposition is not part of the directory migration.

## Current migration audit

- Base head observed before batch B1: `e0a5fa41de3d390764d557694178426d8570cbdb`.
- The checkout contains unrelated in-progress changes in
  `openprogram/webui/routes/agents.py`, `apps/web/components/agents/`,
  `apps/web/scripts/check-agent-tool-configuration.mjs`,
  `apps/desktop/build/icon.icns`, runtime design pages, and promotional assets.
- Batch D/E preserved those worktree versions while staging their prior Git
  baselines at the new application paths; none of the unrelated content entered
  the migration commit.
- Batch C1 moved the FastAPI application assembly to
  `apps/server/openprogram_server/server.py`. The existing
  `openprogram.webui.server` path is a module-identity compatibility alias, not
  a second implementation. Routes and WebSocket handlers remain under
  `openprogram/webui/` until the unrelated `routes/agents.py` work is committed;
  this is an explicit partial boundary, not completion of Batch C.
- Batch F1 removed the unreferenced `openprogram/webui/static/` interface after
  confirming that runtime and release paths serve only the `apps/web` export.
  This removes the duplicate legacy UI without changing the current Web app.
- System Git is blocked by the host's unaccepted Xcode licence. The bundled
  fallback Git executable is available for status, diff, staging and commits;
  the migration does not change host licence state.

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
| Python CLI package consolidation | `codex/python-package-layout-cli-20260817` / `d2ae71cc`, `5bd809e8`, `4051edaf` | implemented; reviewed | Replaces three first-level internal CLI directories and four implementation files with one `openprogram/cli/` package; keeps `openprogram.cli`, the console script, and both module entry points; repairs current documentation references and adds the executable interactive-import contract required by review. |
| Python CLI package main integration | `f5878426` | integrated | Two-parent merge with current `main` at `c45617a4`; unrelated local icon, runtime-design, promo, and output changes remain outside the integration tree. |
| Apps migration B1: Ink TUI workspace | `f4a3ea0e`, `669f5445` | implemented; reviewed | Moves the existing Node workspace from `cli/` to `apps/cli/`; updates the Python launcher, rescue probe, source installer, CI cache/working directories, repository contracts and current documentation paths. No UI or protocol behavior changes. |
| Apps migration D/E: Web and Desktop workspaces | `44765788`, `f4e44429`, `e0a4825e` | implemented; reviewed | Moves the complete Next.js and Electron workspaces to `apps/web/` and `apps/desktop/`; updates runtime discovery, CI, packaging, release scripts, cross-workspace checks, tests and current documentation references. Existing UI and protocol behavior are unchanged. |
| Apps migration C1: Server application assembly | `6b981b32`, `6dc2f1e6`, `34966b16`, `c09b7933` | implemented; reviewed | Moves the FastAPI assembly into the installable `openprogram_server` application package. The legacy import resolves to the same module object, source checkouts reject an already-loaded foreign package, and the release probe imports only from the built wheel outside the checkout. Route and WebSocket modules remain for C2 because one route has unrelated active changes. |
| Legacy cleanup F1: static Web interface | `23ac60c2` | implemented; reviewed | Removes the unreferenced 9,227-line static HTML/CSS/JS interface and its obsolete settings-page test. Current source, package and runtime paths continue to serve the `apps/web` build. |

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
pass — independent specification re-review at `5bd809e8`
changes required — first independent quality review of `5bd809e8` found only the missing executable TTY library-import regression; the implementation's dynamic no-side-effect probe passed
15 passed — CLI entrypoint recognition, interactive library-import side-effect, parser, and repository-layout contracts after the quality repair
pass — independent specification confirmation at `4051edaf`
pass — independent quality re-review at `4051edaf`, including a mutation-sensitive interactive-import probe
166 passed — post-merge CLI parser, repository, formal release, distribution, upgrade, and runtime HTTP catalog selection
506 pages; 0 broken links; landing and language checks pass — post-merge documentation build
pass — post-merge Ruff and `git diff --check`
140 passed, 2 skipped — `apps/cli` Vitest suite after typecheck
pass — `apps/cli` production bundle build
6 passed — apps-layout contract and real Node owner-auth paths
15 passed, 1 deselected — apps-layout, exact CI path and repository contracts after review repair; the deselected generated-README check is affected by unrelated in-progress Python docstring edits
pass — independent specification re-review at `669f5445`
pass — independent quality re-review at `669f5445`
pass — staged-candidate Web full check and TypeScript no-emit check for `44765788`
14 passed — Web unit tests after the application-root migration
pass — Web 31-page production build from `apps/web`
pass — staged-candidate Desktop full npm check for `44765788`
37 passed — static frontend, upgrade, Desktop harness, packaged-file and Memory cross-language tests
18 passed, 1 deselected — apps-layout, CI-layer and repository-layout contracts; generated README check excluded because unrelated Python docstrings are in progress
21 passed; 0 broken links — workspace-command repair and documentation checks for `f4e44429`
9 passed; pass — executable Node-missing hint regression and corrected TypeScript command for `e0a4825e`
pass — independent specification and quality re-review for the D/E migration and command repairs
11 passed — Server package identity, health endpoint and built-frontend checks for `6b981b32`
80 passed — Server state, route, WebSocket and session compatibility selection
94 passed — release, distribution, packaged-file and apps-layout checks
35 passed — Server application ownership, source-package precedence, foreign regular/namespace-package conflict, and release-probe contracts
pass — external-directory wheel probe imports Agent Core, canonical Server, compatibility Server and packaged frontend only from the built wheel
pass — independent specification and quality review for Server C1 through `c09b7933`
47 passed, 1 deselected — repository layout, release config, health, frontend asset and built-frontend CSP checks for `23ac60c2`
pass — independent specification and quality review for legacy static UI removal
```

## Deferred boundaries

- `apps/desktop/main.js`: extract existing window lifecycle, native WebView, tab
  transfer, and menu responsibilities after the current Desktop work lands.
- `apps/web/lib/desktop-bridge.ts`: establish executable state/transfer tests before
  moving types, view state, and transfer coordination.
- Long cohesive Python state machines remain intact until a separately tested
  responsibility is identified; line count alone is not an implementation task.
