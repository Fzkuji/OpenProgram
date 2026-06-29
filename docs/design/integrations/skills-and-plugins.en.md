# Skills & Plugins — Design Draft v2 (best-of-breed, full-featured)

Goal: incorporate **all** of the skill & plugin capabilities from claude-code / opencode / hermes, swapping in equivalents wherever the stack differs, and differentiate by optimizing on the host advantages unique to us.

Reference implementations:
- `references/claude-code-leaked/src/{skills,plugins,commands/{skills,plugin}}`
- `references/opencode/packages/opencode/src/{skill,plugin}`, `packages/plugin`, `.opencode/{skills,plugins}`
- `references/hermes-agent/{skills,plugins,optional-skills}`, `web/src/plugins`

---

## 1. Concepts and file conventions

### Skill
Unit: a directory containing `SKILL.md` + optional `references/`, `templates/`, and other resources.

`SKILL.md` frontmatter (claude-code standard + opencode/hermes additions):

```yaml
---
name: my-skill
description: One-line trigger description (LLM reads this to decide invocation)
category: devops              # hermes-style grouping
optional: false               # hermes optional-skills replaces directory-based distinction
allowed-tools: [Read, Edit]   # claude-code: restrict the usable tools
triggers:                     # explicit trigger conditions (our extension; none of the three actually use it)
  keywords: ["deploy", "ci"]
  file_patterns: ["*.yml"]
  slash: "/deploy"
version: 1.0.0
author: ...
---
```

Sources (merged for display; on conflict the latter overrides the former):
1. **Bundled** — `openprogram/skills_bundled/<name>/` (shipped with the package, matching claude-code `src/skills/bundled`)
2. **User** — `~/.openprogram/skills/<name>/`
3. **Project** — `<project>/skills/<name>/` (already exists)
4. **Plugin-provided** — contributed by enabled plugins
5. **Remote-pulled** — `~/.openprogram/cache/skills/<name>/` (matching opencode discovery, pulled from a remote index)

Resource layout (hermes convention): `SKILL.md` + `references/` + `templates/`.

### Plugin
Unit: a package containing a manifest + entrypoint. **All three manifest forms are supported**, parsed uniformly:
- `plugin.json` (claude-code / hermes style)
- `[tool.openprogram.plugin]` inside `pyproject.toml` (Python-native)
- the `"openprogram"` field inside `package.json` (Node-native, opencode style)

Manifest fields:
```jsonc
{
  "name": "...",
  "version": "...",
  "description": "...",
  "deprecated": false,            // opencode style
  "compatibility": ">=0.1.0",     // opencode-style minVersion check
  "trust": "community",           // community | verified
  "entrypoints": {
    "commands":   "...",
    "skills":     "./skills",
    "agents":     "...",
    "hooks":      "...",
    "mcpServers": "...",
    "providers":  "...",          // opencode style: LLM provider injection
    "web":        "./web/dist"    // hermes-style dashboard; can register a frontend
  },
  "sidebar": [                     // our extension: plugin registers first-class sidebar items
    { "label": "My Tool", "icon": "...", "route": "/plugin/my-plugin/tool" }
  ],
  "options": { /* JSON Schema, see PluginOptionsDialog.tsx */ }
}
```

Sources:
1. **Installed via pip** — Python package, scan the entry_points group `openprogram.plugins`
2. **Installed via npm** — Node package, `~/.openprogram/plugins/node_modules/<name>/` (opencode-style PluginLoader)
3. **Local path** — `~/.openprogram/plugins/<name>/` (hermes style, placed manually)
4. **Project-pinned** — `<project>/.openprogram/plugins.json` (declares enablement, version, source)

---

## 2. Backend

### Directory
```
openprogram/
  skills/
    loader.py           # five-source merged loading
    discovery.py        # pull on demand from a remote index (matching opencode discovery.ts)
    watcher.py          # watchdog file watching, hot reload + WS broadcast
  skills_bundled/       # built-in skills
    ...
  plugins/
    loader.py           # multi-manifest parsing, install, uninstall
    sandbox.py          # layered sandbox: subprocess / in-process
    marketplace.py      # multiple marketplaces, claude-code schema adapter
    trust.py            # trust policy + persistence
    bundled/
  webui/routes/
    skills.py
    plugins.py
```

### Skills API (`routes/skills.py`)
| Method | Path | Description |
|---|---|---|
| GET | `/api/skills` | Five-source merged list, including source / enabled / category / optional |
| GET | `/api/skills/{name}` | Full SKILL.md text + frontmatter + resource file tree |
| POST | `/api/skills` | Create in project / user |
| DELETE | `/api/skills/{name}` | Delete from project / user / remote-cache only |
| POST | `/api/skills/{name}/toggle` | enable/disable |
| POST | `/api/skills/{name}/invoke-trace` | Return the injection record from the LLM's last invocation of this skill (unique) |
| GET | `/api/skills/discovery/sources` | List registered remote indexes |
| POST | `/api/skills/discovery/sources` | Add a remote index |
| POST | `/api/skills/discovery/pull` | Pull a single skill from an index into the cache |
| WS | `skills:changed` | Triggered by the watcher |

### Plugins API (`routes/plugins.py`)
| Method | Path | Description |
|---|---|---|
| GET | `/api/plugins` | Installed list + status + errors |
| GET | `/api/plugins/{name}` | Details + manifest + entrypoint load status |
| POST | `/api/plugins/install` | `{source: pip|npm|git|path, spec, ref}` |
| POST | `/api/plugins/{name}/uninstall` | |
| POST | `/api/plugins/{name}/toggle` | |
| POST | `/api/plugins/{name}/reload` | Matching claude-code `/reload-plugins` |
| POST | `/api/plugins/{name}/validate` | Matching `ValidatePlugin.tsx`, dry-run check |
| GET / POST | `/api/plugins/{name}/options` | Read/write the options JSON Schema |
| POST | `/api/plugins/{name}/trust` | Set the trust level, affecting the sandbox policy |
| GET | `/api/plugins/marketplaces` | List marketplaces |
| POST | `/api/plugins/marketplaces` | Add (compatible with the claude-code marketplace schema) |
| GET | `/api/plugins/marketplace/{id}/index` | Browse, with search / category / pagination |
| WS | `plugins:changed` | |
| WS | `plugins:error` | Push load failures in real time |

### Junction points for plugin contribution entrypoints
| Entrypoint type | Injected into |
|---|---|
| skills | The skill registry (merged with filesystem skills) |
| commands | `availableFunctions`, tagged with `source=plugin:<name>` |
| mcpServers | The existing `openprogram/mcp/` registry; no changes to the `/mcp` page |
| providers | The provider registry (corresponding to the openprogram provider system, opencode style) |
| agents | The subagent registry |
| hooks | The event bus (PreToolUse / PostToolUse / SessionStart / Stop, etc.) |
| web | Static assets mounted at `/plugin/<name>/static/`, rendered by the Next.js dynamic route `/plugin/[name]/[...slug]` |
| sidebar | Pushed to the plugin section of the sidebar store (unique) |

### Sandbox (layered)
| trust | Loading method | Failure behavior |
|---|---|---|
| `verified` | in-process import | A load failure disables only that plugin |
| `community` | subprocess + RPC (stdin/stdout JSON-RPC), with CPU / memory / FS limits | A process crash does not affect the main process |
| `untrusted` | Refuse to load; the UI pops a trust confirmation | — |

Hook execution always goes through a subprocess (even when verified), consistent with claude-code.

---

## 3. Frontend

### Sidebar additions (`web/components/sidebar/sidebar.tsx`)
```
New chat
Functions
Skills        <- new
Plugins       <- new
MCP Servers
Memory
Chats
─────────── (plugin-registered sidebar items are inserted dynamically below this divider)
<Plugin A nav>
<Plugin B nav>
```

### Routes
- `/skills` — list (grouped by category, optional collapsed), details, create, remote index management
- `/plugins` — three tabs: Installed / Marketplace / Errors
  - Installed: `UnifiedInstalledCell`-style rows
  - Marketplace: marketplace selector + card browsing + install (porting `BrowseMarketplace / AddMarketplace / DiscoverPlugins`)
  - Errors: porting `PluginErrors`
- `/plugin/[name]/[...slug]` — dynamically render the plugin's own frontend (from `web/dist`)
- `/skills/[name]/trace` — Skill invocation record visualization (unique)

### Components
```
web/components/skills/
  skills-list.tsx, skill-detail.tsx, new-skill-dialog.tsx,
  discovery-sources.tsx, invoke-trace.tsx
web/components/plugins/
  installed-list.tsx, marketplace-browser.tsx, add-marketplace-dialog.tsx,
  plugin-options-dialog.tsx, plugin-trust-warning.tsx, plugin-errors.tsx,
  validate-plugin.tsx, plugin-detail.tsx, plugin-host.tsx (iframe / dynamic mount)
```

### Store
`lib/skills-store.ts`, `lib/plugins-store.ts`, subscribing over WS to `skills:changed` / `plugins:changed` / `plugins:error`.

---

## 4. Unique optimizations (none of the three have them / all are weak)

1. **First-class plugin sidebar items**: manifest `sidebar: [...]` → automatically injected into the sidebar, on par with built-in navigation
2. **Skill invocation trace**: each SkillTool call records `{ skill, injected_md_hash, accessed_refs, ts }`, visualized in the right panel / `/skills/[name]/trace`
3. **Skill hot reload**: watchdog watches any of the five sources for changes → re-parse → WS broadcast, no `/reload` needed
4. **Unified across the three manifests**: any of `plugin.json` / `pyproject.toml` / `package.json` is recognized, zero migration cost
5. **Cross-ecosystem marketplace interop**: a claude-code marketplace schema adapter layer, able to directly add its official / third-party marketplaces
6. **Dual-track package management**: pip + npm dual track; both opencode's Node plugins and hermes's Python plugins can be installed
7. **Explicit triggers**: `triggers.{keywords, file_patterns, slash}`; a hit on any of conversation/file/command surfaces an activation prompt
8. **Provider as a first-class contribution type**: introduced from opencode, letting LLM providers also be distributed in plugin form
9. **Layered sandbox**: the trust level maps to a loading policy, not one-size-fits-all
10. **Validate dry-run**: before install, `POST /validate` checks manifest / entrypoints / dependencies / compatibility, matching `ValidatePlugin.tsx`

---

## 5. Coverage confirmation of capabilities from the three sources

**claude-code**: bundled skills ✓, SKILL.md frontmatter ✓, SkillTool tool invocation ✓, Marketplace / AddMarketplace / BrowseMarketplace / ManageMarketplaces ✓, ManagePlugins / DiscoverPlugins / ValidatePlugin / PluginErrors / PluginOptionsDialog / PluginTrustWarning / UnifiedInstalledCell ✓, ReloadPlugins ✓, the five entrypoints commands/skills/agents/hooks/mcpServers ✓

**opencode**: remote skill discovery (IndexSkill schema) ✓, npm-package-form plugins ✓, Provider plugin ✓, PluginPackage / resolvePluginTarget / compatibility / deprecated fields ✓, Effect-style concurrency and retry (swapped for httpx+tenacity) ✓

**hermes**: domain-grouped skills ✓ (category), optional distinction ✓ (optional field), the `SKILL.md` + `references/` + `templates/` resource layout ✓, `plugin_api.py` entrypoint ✓, `dashboard/dist/` frontend contribution ✓ (upgraded to web entrypoint + sidebar registration)

---

## 6. Phased delivery

- **M1 Skills, full** (covering the skill capabilities of all three)
  - Five-source loader + watchdog + WS
  - SKILL.md parsing (including triggers / category / optional)
  - `/skills` page + bundled default set
  - SkillTool built-in tool + invoke trace
  - Remote discovery (opencode equivalent)

- **M2 Plugins, local**
  - Unified parsing of the three manifests
  - Installation from the four sources pip / npm / git / path
  - Layered sandbox + trust
  - Injection of the commands / skills / mcpServers / providers / hooks / agents entrypoints
  - Installed / Errors pages + Validate / Options / Reload

- **M3 Plugins, frontend and sidebar**
  - `web` entrypoint asset mounting
  - `/plugin/[name]/[...slug]` dynamic rendering
  - Sidebar registration items

- **M4 Marketplace, full set**
  - Multiple marketplaces, claude-code schema adapter
  - Porting BrowseMarketplace / AddMarketplace / DiscoverPlugins

---

## 7. To confirm (not blocking, but pick one)

1. `~/.openprogram/` or reuse some existing global directory per current project conventions
2. How the Provider plugin entrypoint aligns with OpenProgram's existing provider abstraction (need to look at `openprogram/providers/`)
3. Whether the Hook event names align with claude-code (PreToolUse, etc.)
