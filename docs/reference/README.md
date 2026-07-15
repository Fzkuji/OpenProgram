# Reference overview

Come here to look up parameters, commands, and configuration keys. This tab holds the complete reference for the Python API, the CLI commands, and the configuration entries, plus the engineering design-notes archive.

## Python API

- [API overview](API.md) — the core components on one page: `agentic_function`, `Runtime`, providers
- [agentic_function](api/agentic-function.md) — the decorator itself: parameters, metadata, behavior
- [Runtime](api/runtime.md) — every parameter of `runtime.exec()` and its semantics
- [Providers](api/providers.md) — `create_runtime` and the built-in provider runtimes

## CLI and configuration

- [CLI reference](cli.md) — what every `openprogram` subcommand does and its key flags
- [Configuration reference](config.md) — the keys in `config.json`, how to use `openprogram config`, and the environment variable roundup

## Topic notes

- [Claude Code context compaction](claude-code-compaction.md) — an analysis of Claude Code's compaction behavior

## Design-notes archive

[`design/`](design/README.md) is the archive of engineering design notes: written for the developers themselves, organized by subsystem (runtime, providers, function, memory, channels, cli, ui, etc.), append-only. It records the thinking at decision time and is not guaranteed to match the current code line by line — for accurate user-facing information, trust the other pages in this tab and the code.
