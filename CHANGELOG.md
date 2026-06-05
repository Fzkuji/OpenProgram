# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added — Unified account management + key rotation
- **One way to manage accounts across CLI / web / TUI.** Every provider now has the same account surface — list / add / activate / rename / remove multiple accounts (each account is a profile), backed by `/api/providers/{id}/accounts/*`. claude-code stays Meridian-backed behind the same routes, so it's just one instance of the generic panel; one `<ProviderAccounts>` (web) and one Ink picker (TUI) drive every provider. `/login <provider>` in the TUI now completes OAuth / device-code / import-from-CLI / API-key sign-in **in the terminal** instead of sending you to the web UI.
- **Per-provider active account** (`auth/active.py`; CLI `openprogram providers use <provider> [profile]`) — run "openai on the work account, anthropic on personal" at the same time. The request path defaults to each provider's active profile; nothing changes until you activate a non-default one (fully backward compatible).
- **Automatic key rotation + cooldown** (`auth/usage.py`) — the provider call path now acquires a key from the pool per request and reports the outcome: a 429 cools that key down and the next request rotates to another (the rotation/cooldown/fallback machinery in `auth/pool.py` was previously dead — zero callers). Gated: a no-op unless a provider actually has a multi-key pool, so env-key / OAuth / claude-code setups are byte-for-byte unchanged.
- **Named, switchable keys with optional rotation** (web `provider-keys.tsx` + REST `/accounts/{name}/{keys,rotation,keys/{id}/{use,name},strategy,reorder}`) — api-key providers show their key(s) as one list: name each key, pick which is **active** with *Use* (the only one called), and a **rotation toggle** (off by default) that, when on, fails over a rate-limited key to the next (strategy + ↑/↓ priority). Same "several credentials, switch between them" model OAuth providers have for accounts. Backed by a new pool `"fixed"` strategy + `active_credential_id`. A key set the old way (env var / config) is migrated into the list; validated on add.
- Design + status: [`docs/design/unified-account-management.md`](docs/design/unified-account-management.md).

## [0.4.0] - 2026-05-28

### Added — Design system foundation
- **`docs/design/ui/surface-system.md`** — codified the dark-mode "two surfaces" rule (deep sidebars vs lifted panel) and the borderless Button variants that live on each. The `default` variant is now the brand-coloured ghost pill (`bg-background` + `text-primary` idle, `bg-primary` + `text-primary-foreground` hover); pure-white hover text dropped in favour of near-black `--primary-foreground: #1a1a19` so the warm orange fill no longer reads as a neon pill.
- **`docs/design/ui/indicator-dots.md`** — unified four parallel CSS classes (`.pulse`, `.pending-pulse`, `.status-dot`, `.attach-card-status-dot`) under one `.indicator-dot` primitive with size / colour / animation modifiers. Outer box always equals the `●` glyph advance width (~12.8 px) so header glyphs and body dots line up by layout instead of margin tweaks; visual disc painted by `::before` so the scale-breathing animation never jitters surrounding text. Migrated 8 call sites, dropped 4 legacy classes + 3 keyframes blocks.
- **Two-set sizing tokens** — `--ui-list-h: 32px` / `--ui-list-radius: 6px` for sidebar / list rows, `--ui-button-h: 30px` / `--ui-button-radius: 8px` for panel buttons. Each set is locked: no sm / md / lg ladder inside a set. Button height intentionally shorter than list so a panel pill doesn't visually outweigh sidebar rows. Sidebar (`nav-classes`, `favorites-list`, `sessions-list`, `sidebar.tsx`, popover menu rows), `Input` primitive, and several inline form selects all consume the tokens.
- **Font-smoothing locked to grayscale antialiasing** in both themes — macOS / Safari previously swapped between subpixel and grayscale across dark / light, making bold text "lighter in dark mode" even though no font-weight rule changed.

### Fixed — fn-form, mini-DAG, CI
- **fn-form non-agentic tool calls now return a structured 400 instead of failing silently.** Picking a non-agentic tool (`bash`, `edit`, …) in the function form used to land in a daemon-thread `raise` after the HTTP response had already returned 200, leaving a phantom `[function call] foo()` user row that never produced output. The endpoint now validates the tool synchronously before creating a session or spawning the subprocess. The composer surfaces the failure via an `alert()` so users see the reason without opening DevTools.
- **Mini-DAG pixel-aligned with `Function call` glyph** — `.pending-pulse` outer box widened to 12.8 px (`●` glyph advance width) so the "Running…" disc sits on the same column as the header dot. `.inline-tree-body` `padding-left` bumped 8→10 px to match `.inline-tree-header` so the entire body column lines up with the header label.
- **CI green on Python 3.11 / 3.12 / 3.13** — fixed a PEP 701 nested-quote f-string in `cli.py`; mocked codex CLI presence in `test_codex_source_imports_from_file`; lower-cased the path check in `test_bootstrap_real_user_data_dir_is_platform_specific`; skipped `test_dispatcher_dag_attach` on bare CI runners (it requires a configured provider in `$HOME` to exercise the DAG-attach path, which Linux runners don't have).

### Removed
- **8 zero-importer half-typed orphan files** (`tree-panel.tsx`, `shiki-code.tsx`, `ui/dropdown.tsx`, `branches-panel.tsx`, `memory-page.tsx`, `providers-section.tsx`, `search-providers-section.tsx`, `use-legacy-globals.ts`) — each referenced modules that don't exist, was carried in by an earlier integration commit, and was caught here. `tsc --noEmit` error count dropped from 23 to 0, and the `next.config.mjs` `eslint.ignoreDuringBuilds` / `typescript.ignoreBuildErrors` escape-hatches were removed; `npm run build` now goes through full lint + typecheck on every CI run.
- **`web/components/programs/`** orphan directory + `web/lib/programs-*.ts` — the upstream `programs → functions` rename finished in `b516787a`, but a parallel local copy of the pre-rename tree had survived; cleaned up here.

### Fixed
- **Windows: `import fcntl` no longer breaks the worker / agent registry / channel bindings / sleep runner / browser bootstrap** — six modules did a top-level `import fcntl`, which is POSIX-only and crashed every Windows import with `ModuleNotFoundError`. A new `openprogram._compat` shim re-exports `fcntl` on POSIX and emulates the same `flock` / `LOCK_*` surface on Windows via `msvcrt.locking`, translating `EACCES` to `BlockingIOError` so call sites keep the POSIX exception pattern. `openprogram worker <verb>` now runs cleanly on Windows.
- **`/api/providers/list` no longer 500s when an LLM SDK isn't installed** — `openprogram.providers.anthropic` and `openprogram.providers.openai_completions` did a top-level `import anthropic` / `import openai`, even though both SDKs are declared as optional extras. Importing the package transitively (e.g. from `webui/_model_catalog.py` for the side-effect registry) crashed the catalog endpoint and showed an empty LLM Providers page. Imports are now wrapped in try/except; a runtime guard at the entry of each `stream_simple` / `_build_client` raises a clear `ImportError` naming the pip extra to install if the provider is actually used.
- **`_init_providers` no longer picks a not-actually-usable provider as the global default** — the boot-time priority scan only checked that `Runtime.__init__` returned cleanly. For HTTP-backed providers like `claude-code` that's "always yes" because the constructor just stamps a base_url and never touches the network, so a setup where Meridian wasn't running still got selected as the default and persisted into every new conversation's `provider_name` — then exploded at send time with a connection error. The probe now defers to the same `_is_configured` predicate the Settings page uses (API key present / CLI binary on PATH / local proxy answering on its port).
- **Codex: pre-2026 `auth.json` files (no `auth_mode` discriminator) now import cleanly** — `import_from_codex_file` discriminated strictly on `auth_mode` to refuse apikey-shape files, but the field is a recent addition; older ChatGPT-OAuth files were silently rejected. The shape is now inferred when the discriminator is missing: tokens populated + empty/null `OPENAI_API_KEY` slot ⇒ treat as `chatgpt` mode. The apikey case still falls through to the existing return-None branch.
- **Windows: non-ASCII chat content no longer crashes `_log`** — `sys.stdout` / `sys.stderr` default to `cp1252` (or `gbk` on a CN locale) on Windows, and the chat-execute path's plain `print` raised `UnicodeEncodeError` on any CJK or em-dash, bubbling out as a 500. `cli.main()` now reconfigures both streams to UTF-8 with `errors='replace'` so diagnostic logs are lossy-but-non-fatal. POSIX builds are unaffected.
- **`@agentic_function` docstring restored to the rendered context** — the tree-Context → DAG refactor dropped the function docstring from the prompt. It is now stored on the function's DAG `Call` node (`metadata.doc`) and rendered into the context of the LLM calls made inside the function, so the model sees what the function does.
- **`@agentic_function(system=...)` now reaches the model** — the decorator's system prompt was stored on the function object but never applied. It is now stamped onto the injected runtime for the duration of the call (saved/restored so a caller's own `system` survives).
- **`_retry_choice` buildin module restored** — `parse_args`'s retry path imported a module deleted in the DAG refactor, so any failed parse crashed with `ModuleNotFoundError` instead of retrying.
- **Agent runtime bugs** — `wiki_agent` passed a bare string to `runtime.exec` and imported a non-existent `legacy_providers`; `research_agent`'s `_stage_step` called `parse_args` with the pre-rewrite API (dict in, tuple out).

### Changed — agent functions
- **Docstring / `content` split applied to wiki / research / gui agents** — per-call instructions and output schemas moved out of docstrings into `runtime.exec(content=...)`, so they reach the model as the operative prompt rather than as background description.
- **PDF tooling** — added `extract_pdf_figures` / `extract_pdf_tables` agentic functions (LLM-guided figure/table extraction from any PDF).

### Changed — Rebrand to **OpenProgram**
- **Package renamed**: `agentic-programming` → `openprogram` (PyPI), `agentic/` → `openprogram/` (import path).
- **Repository renamed**: `Agentic-Programming` → `OpenProgram`.
- **CLI command renamed**: `agentic` → `openprogram`.
- **Internal reorganization**:
  - `openprogram/agentic_programming/` — core engine (Context / Runtime / @agentic_function), the philosophy's home.
  - `openprogram/providers/` — LLM provider runtimes (unchanged content).
  - `openprogram/functions/tools/` — `@function` leaf tools (was `openprogram/tools/`).
  - `openprogram/functions/agentics/` — `@agentic_function` modules, each its own directory with code in `__init__.py` (replaces the old `programs/functions/{buildin,third_party}/` + `programs/applications/` split). Harness apps live here as symlinks.
  - `openprogram/functions/_registry.py` — single unified registry (merges the deleted `_app_registry` / `_agentic_registry`).
  - `openprogram/webui/` — web UI (was standalone `agentic_web/`, now a sub-package).
- **`@agentic_function` decorator name preserved** — the paradigm's hallmark symbol stays, OpenProgram is its product realization.
- **MCP sub-package removed** — not part of the product scope.
- **Philosophy doc** added at `docs/philosophy/agentic-programming.md`.

### Added
- **CLI runner text transforms** for provider plugins — `text_transforms.input` rewrites prompts/system prompts before launch, and `text_transforms.output` rewrites streamed assistant text deltas without altering tool payloads
- **Real-time web UI** (`python -m openprogram.webui`, also accepts `openprogram.webui.visualize` alias) — interactive Context tree viewer with WebSocket streaming
- **Built-in agentic functions**: `deep_work`, `ask_user`, PDF helpers (`extract_pdf_figures`, `extract_pdf_tables`), `research`, `word_count`, etc.
- **`deep_work`** — autonomous plan-execute-evaluate loop with quality levels (high_school → professor)
- **Session continuity** for CLI providers (Claude Code, Codex, Gemini CLI)
- **Interactive mode** for Claude Code CLI with full tool access
- **Nested JSON export** for Context trees (`.json` format)
- **`input` parameter for `@agentic_function`** — UI metadata for Visualizer structured input forms
  - Supports `description`, `placeholder`, `multiline`, `options`, `hidden` per parameter
  - Bool params auto-render as Yes/No toggle, `options` as clickable chips
  - All meta functions and built-in functions annotated with `input` metadata
  - Design principle: free text → selection → structured input (minimize cognitive load)
  - Full spec documented in `docs/api/agentic_function.md`
- **Structured function form in Visualizer** — replaces text command input for function execution
  - Shows function name, description, typed parameter fields with hints
  - Integrated into the chat input area (replaces textarea when active)
  - Keyboard support: Esc to cancel, Enter/Ctrl+Enter to submit
- **Thinking effort selector** in Visualizer — per-provider thinking/reasoning level control
- **Markdown + LaTeX rendering** in Visualizer chat output
- **Runtime Block UI** — card-style display for function executions with inline context trees
- **Retry with branching** — attempt navigation (Modify) and error retry (Retry) in Visualizer

### Changed
- README redesigned: Quick Start with 3 usage paths (Python/Skills/MCP), annotated code hero image, Deep Work feature showcase
- Split meta-function skill into four focused skills
- `create_skill` updated with "one skill, one entry function" pattern
- Context compaction via `/compact` instead of process restart
- `docs/API.md` now reflects the current public exports for `fix`, `improve`, and `create_runtime`
- Visualizer welcome page redesigned: examples above input, centered welcome screen
- Provider/model badges lock after conversation starts (session immutability)
- Chat history and execution trees persist across page refreshes

### Fixed
- Context JSON roundtrips now preserve `source_file`, so visualizer restores can still locate original function source after reload
- Stderr pipe buffer deadlock in CLI providers
- Per-call readline thread replaced with persistent queue-based stdout reader
- Context branch indentation
- `pytest tests/` now works from a fresh checkout without manually exporting `PYTHONPATH`
- `_loop` not captured when server started via `uvicorn.run()` (broadcast silently failed)
- Detail panel resize/collapse conflict
- Codex default model showing as null

## [0.3.0] - 2025-04-04

### Added
- **Built-in providers**: `AnthropicRuntime`, `OpenAIRuntime`, `GeminiRuntime` in `openprogram/providers/`
  - Each provider is an optional dependency (SDK not required by core)
  - Anthropic: text + image, prompt caching (`cache_control`)
  - OpenAI: text + image (base64/URL), `response_format` (JSON mode / structured output)
  - Gemini: text + image, system instructions
- **`fix()` meta function**: Analyze errors and rewrite broken generated functions
- **Retry mechanism**: `Runtime(max_retries=N)` for automatic retry on transient API errors
  - TypeError/NotImplementedError are never retried (programming errors)
  - All other exceptions retried up to `max_retries` times
  - Exhausted retries raise `RuntimeError` with full error report
- **New examples**:
  - `examples/code_review.py` — code review pipeline (read → analyze → report)
  - `examples/data_analysis.py` — data analysis with render levels and compress
  - `examples/meta_chain.py` — dynamic function chain using `create()`
- **Documentation**:
  - `docs/api/providers.md` — provider configuration guide
  - `docs/api/meta_function.md` — added `fix()` documentation
  - `docs/api/runtime.md` — added retry mechanism documentation
  - README — added Built-in Providers section
- **Tests**: render level tests (summary/detail/result/silent) and summarize parameter combinations (34 new tests, 53 → 87 total)

### Changed
- `meta.py` renamed to `meta_function.py` for clarity

## [0.2.0] - 2025-04-03

### Added
- **`create()` meta function**: Generate `@agentic_function` from natural language descriptions
- **Safety sandbox**: Generated code runs with restricted builtins (no imports, no file I/O)
- **`meta_demo.py`**: Example showing `create()` usage

## [0.1.0] - 2025-04-03

### Added
- **`@agentic_function` decorator**: Auto-records execution into Context tree
  - Parameters: `render`, `summarize`, `compress`
  - Supports sync and async functions
- **`Runtime` class**: LLM call interface with Context integration
  - `exec()` and `async_exec()` with automatic context injection
  - Content blocks: text, image, audio, file
  - One exec() per function guard
- **`Context` dataclass**: Execution record tree
  - `summarize()` with depth, siblings, level, include, exclude, branch, max_tokens
  - `tree()` for human-readable view
  - `traceback()` for error chains
  - `save()` to .md or .jsonl
- **Render levels**: trace, detail, summary (default), result, silent
- **Auto-save**: Completed trees auto-saved to `agentic/logs/`
- **Examples**: `main.py` (Gemini), `claude_demo.py` (Claude Code CLI)
