"""OpenProgram plugins subsystem.

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
"""
from __future__ import annotations

from .manifest import PluginManifest, parse_manifest_dir
from .loader import (
    Plugin,
    list_plugins,
    get_plugin,
    load_plugin,
    unload_plugin,
    reload_plugin,
    rescan,
)

__all__ = [
    "PluginManifest",
    "parse_manifest_dir",
    "Plugin",
    "list_plugins",
    "get_plugin",
    "load_plugin",
    "unload_plugin",
    "reload_plugin",
    "rescan",
]
