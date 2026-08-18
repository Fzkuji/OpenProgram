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
- Split Desktop and Web bridge code only at independently executable boundaries;
  preserve orchestration in place until its own behavior has direct coverage.
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
  a second implementation. Batch C2 moved the Server-owned routes, WebSocket
  actions, owner auth, frontend mounting and helpers under the application
  package. Only the independently edited `routes/agents.py` remains at the
  compatibility path until that work is committed; this is the final partial
  boundary before Batch C completes.
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
| Apps migration C2: Server transport implementation | `bd8a9e30`, `b294256b`, `72f4cbf4`, `7e55a38e` | implemented; reviewed | Moves Server-owned routes, WebSocket actions, owner auth, frontend mounting and helpers into `openprogram_server/_webui`; preserves legacy module names through one shared package path. Review added the missing ignore contract for the generated release staging tree. The independently edited `routes/agents.py` remains the only temporary exception. |
| Legacy cleanup F1: static Web interface | `23ac60c2` | implemented; reviewed | Removes the unreferenced 9,227-line static HTML/CSS/JS interface and its obsolete settings-page test. Current source, package and runtime paths continue to serve the `apps/web` build. |
| Apps migration B2: Python CLI application | `2a790501`, `91be4353`, `51303ea8`, `09eb3732`, `252fb4fa`, `a459c443` | implemented; reviewed | Moves parser, dispatch, Rich fallback, Ink launcher and setup flows into the installable `openprogram_cli` application package. `openprogram.cli` remains a bounded compatibility loader; a code-free root module supports raw source checkouts; editable and wheel installs use the application package. |
| Legacy cleanup F2: profile state | `f0e07310`, `fb490561`, `2849fd85` | implemented; reviewed | Removes tracked mutable tool-profile state from the core package. One-time migration resolves the actual legacy compatibility package even though the Server module now lives under `apps/server`; package-data exclusion remains enforced and both legacy state paths remain ignored. |
| Browser v0.7.0 policy closure | `bf7dd2ad` through `537b20aa` | implemented; reviewed; released | Removes the post-release experimental browser-extension installer and management surface, preserves the released Browser, profile import and Agent WebTab capabilities, rejects obsolete persisted Extensions tabs, and records the unsupported-extension boundary in the product and design documentation. The published v0.7.0 assets predate the experimental installer and already match this boundary. |
| Post-migration CI repair | `15fd5e1f`, `c7a494ef`, `eaf34a1b`, `63c65ad8`, `35d7edc2`, `2f40f948` | implemented; reviewed | Aligns landing-page checks, migrated Server test paths, documentation navigation, runtime HTTP inventory and doctor checks with `apps/`; keeps App package locks portable; moves subprocess coverage to component; and verifies macOS installer behavior on a native runner rather than Ubuntu. |
| Desktop menu geometry extraction | `ec8a988f`, `ab2fef3b` | implemented; reviewed | Moves three pure placement functions to a directly executable CommonJS module without changing menu behavior; includes the module in packaged and local-refresh file closures and makes the refresh test verify the staged file. |
| Desktop bridge WebTab type extraction | `9bdc4a93` | implemented; reviewed | Moves the five public WebTab contracts to a type-only module, preserves re-exports from the established bridge entry and leaves all renderer state and transfer runtime code unchanged. |
| Desktop bridge service type extraction | `f6ede2a7` | implemented; reviewed | Moves history, downloads, updates, browser import/data, terminal and menu contracts to the same type-only module; direct and compatibility type consumers compile while transfer and runtime coordination remain in place. |
| Desktop bridge transfer type extraction | `2d2ec81a`, `b8c97d41` | implemented; reviewed | Moves the preload-facing transfer receipt and API contracts to a dedicated type-only module, preserves the established bridge re-exports and leaves the journal, aggregate bridge and transfer runtime unchanged. |

## Implemented task brief: Legacy cleanup F2

- Approved source: `repository-structure.html`; base: `457539c5`.
- Remove the tracked package-local `functions_meta.json`; mutable tool profiles
  remain profile state under `~/.openprogram[-profile]/` and new wheels continue
  to exclude this file.
- Preserve one-time migration from an actual older
  `openprogram/webui/functions_meta.json`. Resolve that location from the legacy
  compatibility package, not from the Server application module that now lives
  under `apps/server`.
- RED/GREEN boundaries: a physical repository ownership contract, a migration
  test whose Server module points elsewhere, profile isolation and atomic state
  writes.
- Exclude changes to profile contents, tool selection behavior, Server routes,
  Web UI and active Agent configuration work.

## Implemented task brief: Apps migration B2

- Approved source: `repository-structure.html`; base: `2ba6f078`.
- Move the Python CLI application files from `openprogram/cli/` to the
  installable `apps/cli/python/openprogram_cli/` package. Keep only bounded
  compatibility entry files under `openprogram/cli/`.
- Preserve `openprogram`, `python -m openprogram`, `python -m openprogram.cli`,
  `openprogram.cli.build_parser`, and existing `openprogram.cli.*` imports.
- Preserve command grammar, output, profile/state paths, TUI selection and the
  existing Node launcher. Do not redesign commands or add dependencies.
- RED/GREEN boundaries: repository ownership contract, parser snapshot and
  dispatch tests, module-entry subprocesses, source-checkout precedence, wheel
  contents, isolated wheel imports and CLI `--help`/`--version` execution.
- Exclude Server route migration, CLI behavior changes, UI changes and
  decomposition of cohesive command handlers from this batch.

## Implemented task brief: Desktop menu geometry extraction

- Approved source: `repository-structure.html`; base: current `main` after the
  browser and CI closure.
- Move only the pure menu placement functions from `apps/desktop/main.js` to
  `apps/desktop/menu-geometry.js`: requested horizontal placement, bounded
  context-menu placement and cascading bookmark-menu host geometry.
- Keep `openMainMenu`, `resizeMenuOverlay`, Electron view ownership, timers,
  IPC registration, constants and renderer behavior in `main.js`.
- Preserve every current numeric result for start/end alignment, zoom,
  negative coordinates, near-bottom anchors and anchors outside the window.
- Make the existing Desktop checks execute the exported module directly;
  package and local-App refresh lists must include the new runtime file.
- RED boundary: the public CommonJS module import and package-file contract fail
  before the module exists. GREEN boundary: geometry checks, WebTab navigation,
  packaged-file tests, local refresh file-list contract and full Desktop checks.
- Exclude visual changes, new menu behavior, Browser toolbar changes,
  `openMainMenu` orchestration and the unrelated active App icon work.

## Implemented task brief: Desktop bridge type-contract extraction

- Approved source: `repository-structure.html`; base: current `main` after the
  Desktop menu geometry extraction.
- Move only the WebTab state, find-result, bounds, visible-view and WebTab API
  contracts from `apps/web/lib/desktop-bridge.ts` to
  `apps/web/lib/desktop-bridge-types.ts`. Leave transfer, history, download,
  update, import, terminal and menu contracts in place for separate batches.
- Keep `desktopBridge()`, renderer bookkeeping, surface inventory, menu event
  handling and tab-transfer coordination in `desktop-bridge.ts`.
- Re-export the moved WebTab types from `desktop-bridge.ts` so current imports
  remain source compatible; the runtime module must import only the types it
  uses.
- RED boundary: a production type consumer imports the not-yet-created module
  and TypeScript fails resolution. GREEN boundary: TypeScript, executable Web
  split checks and the complete Web check.
- Exclude runtime behavior, store changes, transfer protocol changes, UI/CSS
  changes and unrelated active Agent configuration work.

## Implemented task brief: Desktop bridge service-contract extraction

- Approved source: `repository-structure.html`; base: current `main` after the
  WebTab type-contract extraction.
- Move history, downloads, update, browser import/data, terminal and menu API
  contracts into the existing `desktop-bridge-types.ts` module.
- Keep transfer receipts, transfer API, the aggregate `DesktopBridge` interface
  and every runtime function in `desktop-bridge.ts` because the transfer types
  still share contracts with `tab-transfer-journal.ts`.
- Re-export all moved types from `desktop-bridge.ts`; direct type-only consumers
  may use the dedicated module without importing the runtime coordinator.
- RED boundary: a production type consumer requests a not-yet-exported service
  contract and TypeScript fails. GREEN boundary: TypeScript, built-in Browser,
  executable Web split and complete Web checks.
- Exclude transfer protocol changes, runtime imports, UI/CSS changes and
  unrelated active Agent configuration work.

## Implemented task brief: Desktop bridge transfer-contract extraction

- Approved source: `repository-structure.html`; base: current `main` after the
  Desktop service-contract extraction.
- Move only `DesktopTransferReceipt` and `DesktopTabTransferApi` to
  `apps/web/lib/desktop-transfer-types.ts`. These preload-facing contracts may
  depend type-only on the journal payload and placement types.
- Keep journal persistence types and functions in `tab-transfer-journal.ts`;
  keep the aggregate `DesktopBridge` interface and every transfer runtime
  function in `desktop-bridge.ts`.
- Re-export both moved types from `desktop-bridge.ts`; no runtime import or
  protocol change is allowed.
- RED boundary: `desktop-bridge.ts` imports the not-yet-created type module and
  TypeScript fails resolution. GREEN boundary: TypeScript, executable Web split
  and complete Web checks.
- Exclude transfer state-machine changes, UI/CSS changes and unrelated active
  Agent configuration work.

## Active task brief: Desktop worker recovery-state extraction

- Approved source: `repository-structure.html`; base: current `main` after the
  Desktop bridge transfer-contract extraction.
- Move only the five pure worker-recovery state helpers from
  `apps/desktop/main.js` to `apps/desktop/worker-recovery-state.js`.
- Keep HTTP probes, worker process spawning, timers, BrowserWindow ownership and
  recovery orchestration in `main.js`.
- Make the existing recovery check import the production module directly; add
  the runtime file to Electron packaging and local-App refresh closure.
- RED boundary: the direct recovery module import fails before the module
  exists. GREEN boundary: worker recovery, packaged-file, refresh fixture and
  complete Desktop checks.
- Exclude recovery behavior changes, timing changes, UI changes and unrelated
  active Agent configuration work.

## Active task brief: Apps migration C2

- Approved source: `repository-structure.html`; base: `a459c443`.
- Move FastAPI routes, WebSocket actions, owner authentication, static Web
  mounting, response shaping and their Server-only helpers to
  `apps/server/openprogram_server/_webui/` without changing their established
  `openprogram.webui.*` import names during the compatibility period.
- Keep `openprogram/webui/` as a bounded source-checkout and module-entry
  compatibility package. It may locate the installed or checkout Server
  package, but it must not duplicate mutable Server state or eagerly initialize
  the application during an Agent Core import.
- Preserve route order, WebSocket action registration, shared module globals,
  owner-auth enforcement, port `18100`, static asset lookup, CLI/TUI imports and
  worker startup. Release wheels must contain the moved Python modules and Web
  assets, and isolated imports must resolve outside the checkout.
- First sub-batch excludes only `routes/agents.py`, whose unrelated active
  worktree changes predate this migration. The route package remains a split
  compatibility path until that file is committed and moved in the final C2
  sub-batch.
- RED/GREEN boundaries: physical ownership contract, legacy import source
  resolution, route/WS application tests, owner-auth tests, built-frontend
  tests, clean wheel contents and isolated wheel import/startup probes.
- Exclude transport behavior redesign, new dependencies, UI changes and
  decomposition based only on line count.

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
76 passed — focused CLI application, parser, release and structure contracts for `91be4353`
522 passed — tracked tests that import the `openprogram.cli` compatibility API
45 passed — post-commit repository, apps-layout and formal-release contracts
pass — clean wheel contains only two `openprogram/cli` compatibility files and 40 `openprogram_cli` files
pass — isolated wheel executes console script plus `python -m openprogram`, `python -m openprogram.cli` and `python -m openprogram_cli`
510 pages; 0 broken links — documentation candidate build
changes required — first independent specification review found raw-checkout discovery, canonical TUI detection, release execution and clean-README gaps
49 passed — raw checkout, stale-package rejection, all module-entry recognition, release and clean README repair contracts
pass — independent specification re-review for B2 through `a459c443`
pass — independent quality review for B2 through `a459c443`
254 passed, 8 warnings — Server ownership, route/WS, auth, frontend, project, run-guard, WebTab, release and distribution selection for C2
pass — Ruff, compileall and git diff check for the moved Server implementation
pass — clean wheel contains canonical Server transport modules and no legacy implementation copies; isolated legacy imports resolve to `openprogram_server/_webui`
pass — independent specification review for Server C2 through `72f4cbf4`
changes required — first independent quality review found only the generated Server release staging directory missing from `.gitignore`
4 passed — executable release-staging cleanup and ignore contracts after `7e55a38e`
pass — independent quality re-review for Server C2 through `7e55a38e`
2 failed — public RED proved legacy profile migration followed the moved Server module and package-local state was still tracked
28 passed — profile migration, profile isolation, atomic writes, apps-layout and wheel package-data checks after `fb490561`
pass — Ruff and git diff check for Legacy cleanup F2
pass — independent specification review for Legacy cleanup F2 through `8843ae13`
changes required — first quality review found the deleted legacy profile path missing from `.gitignore`
1 passed; pass — executable ignore contract, Ruff and diff check after `2849fd85`
pass — independent quality re-review for Legacy cleanup F2 through `2849fd85`
pass — browser extension negative contract, Browser/Web/Desktop focused checks, TypeScript and documentation links for `bf7dd2ad` through `537b20aa`
pass — independent specification and quality review of the browser extension removal and documentation boundary
published — v0.7.0 stable GitHub Release is non-draft and non-prerelease with 17 assets; the release contains the complete built-in Browser experience and no extension manager
2587 passed, 3 skipped — complete local unit suite after the apps migration and CI repairs
8 passed — native macOS Desktop installation, downgrade, rollback, concurrency and stable-lock tests
pass — CI run 32077688937: quality, Python 3.11/3.12/3.13 unit, component, integration, e2e, coverage, Web, CLI, Desktop, Browser and macOS Desktop installation jobs
pass — independent quality review for `2f40f948`
1 failed — public RED: `check-webtab-zoom-bounds` could not import the not-yet-created `menu-geometry.js`
pass — Desktop full `npm run check` after extracting menu geometry
3 passed — packaged-file closure and local-App refresh staging contracts
pass — independent specification review for `ec8a988f`
changes required — first independent quality review found that the refresh fixture did not assert the staged module
1 passed — refresh staging regression after `ab2fef3b`
pass — independent quality re-review through `ab2fef3b`
1 failed — public RED: the production transfer journal could not resolve the not-yet-created `desktop-bridge-types` module
pass — complete Web `npm run check`, executable Web split checks and TypeScript no-emit check for `9bdc4a93`
pass — independent specification review for `9bdc4a93`
pass — independent quality review for `9bdc4a93`
1 failed — public RED: `browser-controls.tsx` requested the not-yet-exported `DesktopContextMenuItem`
pass — complete Web `npm run check`, built-in Browser, bookmarks, theme, terminal, updates, Web split and TypeScript checks for `f6ede2a7`
pass — independent specification review for `f6ede2a7`
pass — independent quality review for `f6ede2a7`
1 failed — public RED: `desktop-bridge.ts` could not resolve the not-yet-created `desktop-transfer-types` module
pass — complete Web `npm run check`, executable Web split checks and TypeScript no-emit check through `b8c97d41`
pass — independent specification review through `b8c97d41`
pass — independent quality review through `b8c97d41`
```

## Deferred boundaries

- `apps/desktop/main.js`: pure menu geometry now lives in
  `apps/desktop/menu-geometry.js`; keep window lifecycle, native WebView, tab
  transfer and menu-host orchestration in place until each boundary has direct
  executable coverage.
- `apps/web/lib/desktop-bridge.ts`: WebTab and Desktop service contracts now
  live in `apps/web/lib/desktop-bridge-types.ts`; preload-facing transfer
  contracts live in `apps/web/lib/desktop-transfer-types.ts`. Keep the aggregate
  bridge, view state, journal persistence and transfer coordination in place
  until an independently executable responsibility is identified.
- Long cohesive Python state machines remain intact until a separately tested
  responsibility is identified; line count alone is not an implementation task.
