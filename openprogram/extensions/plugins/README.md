# `openprogram/extensions/plugins/`

> OpenProgram plugins subsystem.

## Overview

A plugin is a declarative package in the Claude Code, Codex, OpenCode or
Hermes format: skills, slash commands, agents, hooks, MCP servers and
provider directories described by its manifest, mounted through a
``ProgramContext`` owned by ``plugin:<name>``. A manifest that names code
is refused; code that adds tools or a UI is an application. Sources are
npm, a local path (usually a link) and project pins. Design record:
``docs/reference/design/integrations/plugin-runtime.html``.

The host exposes the HTTP API in ``routes/catalog/plugins.py``; this
package parses manifests, loads plugins, and keeps their contributions and
persisted state.

## Files in this directory

- **`autoupdate.py`** — Plugin auto-update
- **`hooks.py`** — A plugin's hooks: shell commands subscribed to bus events
- **`inspect.py`** — What a plugin can contain, and how entries are exposed, read from the live registries
- **`installer.py`** — Plugin installer
- **`loader.py`** — Plugin discovery and loading
- **`manifest.py`** — Plugin manifests, read compatibly from the external formats
- **`marketplace.py`** — Marketplace 管理。
- **`paths.py`** — 统一路径：~/.openprogram/ 下的插件相关目录。
- **`registry.py`** — What each loaded plugin contributed, for the HTTP API and the commands registry
- **`trust.py`** — Trust 等级持久化。

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
