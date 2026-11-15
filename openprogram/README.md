# `openprogram/`

This directory is the reusable Python Agent Core and its compatibility APIs. It
owns the agent runtime, persistence, providers, Programs, integrations, worker
lifecycle, the FastAPI Server application and the Python CLI application. The
Web, Ink TUI, and Electron clients live under `apps/` and depend on this core.

## Package map

The top level holds only these packages plus `__init__.py` and `__main__.py`.
Programs sit at the top level. Everything else is grouped by what it is
for; each group is a plain package with no code of its own.

| Group | Packages |
| --- | --- |
| Programs (top level) | `programs/` |
| `runtime/` — the agent runtime | `agent/` (one model call: `Agent`, `agent()`, the tool loop), `conversation/` (chats and turns), `jobs/` (background runs), `agent_registry/` (saved Agents), `permissions/`, `context/`, `execution/` (pause, cancel, recovery), `events/`, `commands/`, `proactive/`, `scheduler/` |
| `interfaces/` — how people and programs talk to it | `acp/`, `channels/`, `speech/`, `server/`, `cli/` |
| `host/` — the machine it runs on | `backend/`, `environments/`, `sandbox/`, `processes/`, `resources/`, `system_access/`, `lsp/`, `security/` (including `identity_header.py`), `_platform/` (filesystem, locking, processes, terminal) |
| `models/` — model access | `providers/`, `auth/` (including `auth/credentials/`), `usage/` |
| `state/` — what it keeps | `store/`, `memory/`, `worktree/`, `attachments.py`, `paths.py` (state directory layout), `settings.py` (the `SETTINGS` registry for config.json), `program_directory.py` (user Program folders) |
| `extensions/` — what extends it | `plugins/`, `skills/`, `mcp/` (client and `mcp/server/`) |
| `operations/` — running an installation | `worker/`, `self_update/`, `setup.py` |

`extensions/skills/` implements the AgentSkills-compatible loader for remote-cache,
plugin, user, and project sources. Product workflows ship as Programs rather
than default skills, so the package contains no bundled skill directory.

The Server application (FastAPI assembly, routes, WebSocket actions) is the
package `openprogram.interfaces.server` and the Python CLI application is
`openprogram.interfaces.cli`, in `interfaces/server/` and `interfaces/cli/`
(see [`interfaces/README.md`](interfaces/README.md)).

`python -m openprogram` runs the CLI through `openprogram/__main__.py`, which
calls `openprogram.interfaces.cli.application.main`. Parser construction lives in
`interfaces/cli/parser.py`; command handlers, the Rich REPL, the Ink launcher, and
setup sections live beside it in `interfaces/cli/`.

Most packages, at the top level and inside a group, have their own `README.md`. Generated package
READMEs use the corresponding `__init__.py` docstring as their source and are
checked by the repository contracts.

See [Repository Structure](../docs/reference/design/repository-structure.html)
for ownership rules and [the test guide](../tests/README.md) for test placement.
