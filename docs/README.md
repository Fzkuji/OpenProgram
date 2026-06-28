# Documentation

This directory is the documentation entry point for OpenProgram.

## Start here

| File | Purpose |
|---|---|
| [`GETTING_STARTED.md`](GETTING_STARTED.md) | Installation, provider setup, and runnable examples |
| [`features.md`](features.md) | Detailed tour of the key features the README summarises |
| [`install.md`](install.md) | What each pip extra adds + post-install steps |
| [`installing-harnesses.md`](installing-harnesses.md) | Harnesses: install any (first/third-party) by one command, write your own |
| [`troubleshooting.md`](troubleshooting.md) | "It doesn't work" cookbook (no provider, port in use, multi-repo install …) |
| [`API.md`](API.md) | Public API index |
| [`README_CN.md`](README_CN.md) | Chinese project overview |
| [`philosophy/agentic-programming.md`](philosophy/agentic-programming.md) | Agentic Programming rationale |

## Agentic programming guide

OpenProgram's own programming model — the concepts a generic LLM-framework
tutorial will not teach you. **Read this before writing functions.**

| File | Purpose |
|---|---|
| [`agentic-programming/README.md`](agentic-programming/README.md) | Guide index + learning path |
| [`agentic-programming/writing-functions/agentic-function.md`](agentic-programming/writing-functions/agentic-function.md) | `@agentic_function` usage patterns |
| [`agentic-programming/writing-functions/function-metadata.md`](agentic-programming/writing-functions/function-metadata.md) | Function metadata — source of truth |
| [`agentic-programming/choosing-the-next-step/fixed-order-calls.md`](agentic-programming/choosing-the-next-step/fixed-order-calls.md) | Fixed-order sub-function pipelines |
| [`agentic-programming/choosing-the-next-step/tool-calling.md`](agentic-programming/choosing-the-next-step/tool-calling.md) | Provider-native tool-use loop |
| [`agentic-programming/choosing-the-next-step/next-step-decision.md`](agentic-programming/choosing-the-next-step/next-step-decision.md) | `decision.make` / `exec(choices=)` — LLM picks the next step |
| [`agentic-programming/writing-functions/pure-python.md`](agentic-programming/writing-functions/pure-python.md) | Plain Python helpers (no LLM) |

## API reference

| File | Purpose |
|---|---|
| [`api/agentic_function.md`](api/agentic-function.md) | `@agentic_function` decorator API |
| [`api/runtime.md`](api/runtime.md) | `Runtime.exec()` and runtime behavior |
| [`api/providers.md`](api/providers.md) | Provider/runtime classes and setup |
| [`provider-token-tracking.md`](provider-token-tracking.md) | Provider usage accounting semantics |

## Integration guides

| File | Purpose |
|---|---|
| [`INTEGRATION_CLAUDE_CODE.md`](INTEGRATION_CLAUDE_CODE.md) | Claude Code subscription/runtime integration |
| [`INTEGRATION_OPENCLAW.md`](INTEGRATION_OPENCLAW.md) | OpenClaw integration patterns |

## Design notes

Use [`design/README.md`](design/README.md) as the entry point. It separates
current specs from archived audits and standalone demos.

## Maintenance rules

- Keep current API facts under `api/`; keep design rationale under `design/`;
  keep function-authoring guides under `agentic-programming/`.
- Prefer linking to a source document instead of repeating the same rules in
  multiple files.
- If a design note no longer defines current behavior, move it to
  `design/archive/`.
- Verify docs with a relative-link check after moving files.
