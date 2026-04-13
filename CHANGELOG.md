# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **Real-time web visualization** (`python -m agentic.visualize`) — interactive Context tree viewer with WebSocket streaming
- **Built-in agentic functions**: `general_action`, `agent_loop`, `wait`, `deep_work`
- **`deep_work`** — autonomous plan-execute-evaluate loop with quality levels (high_school → professor)
- **Session continuity** for CLI providers (Claude Code, Codex, Gemini CLI)
- **Interactive mode** for Claude Code CLI with full tool access
- **Nested JSON export** for Context trees (`.json` format)

### Changed
- README redesigned: Quick Start with 3 usage paths (Python/Skills/MCP), annotated code hero image, Deep Work feature showcase
- Split meta-function skill into four focused skills
- `create_skill` updated with "one skill, one entry function" pattern
- Context compaction via `/compact` instead of process restart
- README and `docs/README_CN.md` now include `improve()` in the self-evolving workflow and meta-function API overview

### Fixed
- Stderr pipe buffer deadlock in CLI providers
- Per-call readline thread replaced with persistent queue-based stdout reader
- Context branch indentation

## [0.3.0] - 2025-04-04

### Added
- **Built-in providers**: `AnthropicRuntime`, `OpenAIRuntime`, `GeminiRuntime` in `agentic/providers/`
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
