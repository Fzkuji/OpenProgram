# Plugins

A plugin is a packaged extension: a pip / npm / git / local package with a manifest that, once installed into the host, contributes commands, skills, MCP servers, providers, hooks, agents, sidebar items, web pages, and more to OpenProgram. This page covers installing and managing plugins, and what a plugin looks like.

## Install and manage

```bash
openprogram plugins list                 # installed plugins
openprogram plugins search <query>       # search the configured marketplaces
openprogram plugins install pip <package>       # source is one of four: pip / npm / git / path
openprogram plugins install git <url> --ref v1.2
openprogram plugins install path /abs/path/to/plugin
openprogram plugins update               # reinstall-upgrade from pip / npm
openprogram plugins enable <name>
openprogram plugins disable <name>
openprogram plugins uninstall <name>
```

Everything installs under `~/.openprogram/plugins/` (npm packages under its `node_modules/`); trust levels persist in `~/.openprogram/plugin-trust.json`. The web UI exposes the same management API through the plugin routes.

## What a plugin looks like

A directory (or installed package) carrying one of three manifests, resolved in this priority order:

1. `plugin.json` (top-level file, claude-code / hermes style)
2. `[tool.openprogram.plugin]` in `pyproject.toml`
3. the `"openprogram"` field in `package.json` (opencode style)

Manifest fields include `name`, `version`, `description`, `trust` (community / verified), `entrypoints`, `sidebar`, `options`, and `requires`. `requires` declares inter-plugin dependencies: the loader enables plugins in topological order and reports `missing dependencies: ...` when one is absent.

At load time the module named by `entrypoints` is imported (a pip entry point's `module:obj`, or the `python` / `module` key in the manifest). Through that import the plugin exposes its commands / skills / mcpServers / providers / hooks / agents / sidebar / web entries to the contribution registry, and each host subsystem reads from the registry and wires them in.
