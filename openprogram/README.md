# `openprogram/`

This directory is the Python product package. It owns the agent runtime,
worker and HTTP API, persistence, providers, Programs, integrations, and the
Python command entry points. The Web, Ink TUI, and Electron sources remain in
their top-level workspaces and communicate with this package through stable
HTTP, WebSocket, or CLI boundaries.

## Package map

| Area | Packages |
| --- | --- |
| Agent execution | `agent/`, `agentic_programming/`, `context/`, `events/` |
| Programs and commands | `programs/`, `commands/`, `cli/` |
| Models and credentials | `providers/`, `auth/`, `credential_files/`, `backend/` |
| Persistent services | `worker/`, `webui/`, `channels/`, `scheduler/`, `proactive/` |
| State | `store/`, `memory/`, `usage/`, `contextgit/` |
| Integrations | `skills/`, `plugins/`, `mcp/`, `mcp_server/`, `acp/`, `lsp/` |
| Platform boundaries | `sandbox/`, `security/`, `worktree/`, `updater/` |

`skills_bundled/` contains the default skills shipped as package data.
The repository-level `skills/` directory is a project-level source-checkout
skill location and does not replace the bundled defaults.

The public command starts at `openprogram/__main__.py` and the
`openprogram/cli/` package. Parser construction lives in `cli/parser.py`;
command handlers, the Rich REPL, the Ink launcher, and setup sections remain
under that same package.

Most first-level packages have their own `README.md`. Generated package
READMEs use the corresponding `__init__.py` docstring as their source and are
checked by the repository contracts.

See [Repository Structure](../docs/reference/design/repository-structure.html)
for ownership rules and [the test guide](../tests/README.md) for test placement.
