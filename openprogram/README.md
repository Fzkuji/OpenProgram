# `openprogram/`

This directory is the reusable Python Agent Core and its compatibility APIs. It
owns the agent runtime, persistence, providers, Programs, integrations, worker
lifecycle, and Python command entry points. The FastAPI application assembly,
Web, Ink TUI, and Electron sources live under `apps/` and depend on this core.

## Package map

| Area | Packages |
| --- | --- |
| Agent execution | `agent/`, `agentic_programming/`, `context/`, `events/` |
| Programs and commands | `programs/`, `commands/`, `cli/` |
| Models and credentials | `providers/`, `auth/`, `credential_files/`, `backend/` |
| Persistent services | `worker/`, `channels/`, `scheduler/`, `proactive/` |
| State | `store/`, `memory/`, `usage/`, `contextgit/` |
| Integrations | `skills/`, `plugins/`, `mcp/`, `mcp_server/`, `acp/`, `lsp/` |
| Platform boundaries | `sandbox/`, `security/`, `worktree/`, `updater/` |

`skills_bundled/` contains the canonical default skills shipped as package data.
User and project skills remain external inputs discovered from
`~/.openprogram/skills/` and `<cwd>/skills/`; the repository does not keep a
second project-level copy of bundled skills.

`webui/` temporarily preserves established imports for Server routes and
extensions. New FastAPI application assembly belongs in
`apps/server/openprogram_server/`; the compatibility package is removed after
the remaining route migration.

The public command starts at `openprogram/__main__.py` and the
`openprogram/cli/` package. Parser construction lives in `cli/parser.py`;
command handlers, the Rich REPL, the Ink launcher, and setup sections remain
under that same package.

Most first-level packages have their own `README.md`. Generated package
READMEs use the corresponding `__init__.py` docstring as their source and are
checked by the repository contracts.

See [Repository Structure](../docs/reference/design/repository-structure.html)
for ownership rules and [the test guide](../tests/README.md) for test placement.
