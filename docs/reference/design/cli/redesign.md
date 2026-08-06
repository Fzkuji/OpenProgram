# CLI / TUI Configuration Surface — Design

> This document is the authoritative design of how settings are described,
> read, and written across every surface: the command grammar, the schema that
> defines what a setting is, the TUI settings panel, and the transport that
> connects them. For the naming grammar of individual commands, see
> [`naming.md`](naming.md).

## 1. Overview and Motivation

A setting must be editable from wherever the user already is — the shell, the
first-run wizard, a running TUI session, or the web dashboard. Ports are the
sharpest case: they are start-time bindings that users need to change without
knowing which flag or config file holds them.

The failure mode this design excludes is four independent settings surfaces.
When argparse flags, the `setup` wizard, the web `/settings` pages, and the TUI
pickers each poke the config dict directly, every new setting must be written
four times, and each surface covers a different subset. There is no shared
description of what settings exist, so no surface can be complete.

**One schema describes every setting; every surface is a renderer of it.** A
new setting is one `SettingSpec` and it appears in the CLI, the wizard, the TUI
panel, and the web page with no per-surface code.

The corollary is that there is **no web-only settings editor**. The web
dashboard exists, but it is never the only way to change a setting. A user
mid-session who wants to toggle a tool, change a model, or fix a port does it
in the terminal.

## 2. Command Model

Commands are noun-first, verb-last (`openprogram <noun> <verb>`), the grammar
specified in [`naming.md`](naming.md). Modes are verbs, not flags:
`openprogram web` launches the web UI, bare `openprogram` launches chat. There
are no `--tui` / `--web` / `--cli` mode flags.

### Container verbs always show their subcommands

A verb that is a pure namespace (`programs`, `skills`, `plugins`, `sessions`,
`channels`, `memory`, `worker`, `mcp`, `browser`, `subagent`) does nothing on
its own. Invoked bare, it prints its subcommand list and exits non-zero, via
the single helper `cli._need_subcommand(parser)`. Routing every container verb
through one helper is what makes the behaviour uniform — ad-hoc `print_help()`
calls per dispatcher drift, and half of them exit zero.

Verbs with a real default action keep it: `providers` shows pools, `ports`
shows the port table, `config` lists settings. The distinction is whether the
verb names an action or only a namespace.

Every `add_argument` / `add_parser` in the tree carries `help=`, and the
top-level `--help` carries a common-commands epilog, so the tree is
self-documenting under tab completion.

### `openprogram config` is its own command group

`config` is the settings entry point, not an alias for `setup`:

- `openprogram config` — the schema-driven picker menu.
- `openprogram config get <dot.path>` / `config set <dot.path> <value>` —
  non-interactive, scriptable, and the stable target for documentation.
- `openprogram config <section>` — jump straight to one section.

`setup` remains the first-run linear walk. The `get` / `set` leaves are backed
by the schema in §3, so a scripted edit and a panel edit take the same
validated path.

## 3. The Settings Schema

`openprogram/config_schema.py` holds a single ordered registry. Every setting
is one frozen spec:

```python
@dataclass(frozen=True)
class SettingSpec:
    key: str                 # stable id, e.g. "ui.port"
    path: tuple[str, ...]    # dot-path into config.json, e.g. ("ui","port")
    group: str               # "Ports" | "Model" | "Theme" | ...
    label: str
    widget: str              # "number" | "toggle" | "enum" | "checkbox" | "secret-status"
    apply: str               # "live" | "next-start"
    choices: Callable[[], list[str]] | None = None   # for enum/checkbox, computed at read time
    validate: Callable[[Any], str | None] | None = None  # returns error or None
    secret: bool = False
```

`SETTINGS: list[SettingSpec]` is the source of truth, and two functions are the
only access path:

- `get_settings() -> list[ResolvedSetting]` reads `config.json` once, resolves
  each spec's current value (computing `choices()` lazily), and masks secrets.
- `set_setting(key, value) -> {applied, error?}` validates against the spec and
  writes through the typed helper for that key when one exists (`set_ui_ports`
  for `ui.*`, `write_search_default_provider` for search, the `/api/config`
  writer for `api_keys`), falling back to a generic dot-path write otherwise.

The generic dot-path write carries a blocked-key guard against
prototype-pollution keys. This is what makes `config set ui.port 19000` safe
regardless of which surface issued it.

### Where values live

`~/.openprogram/config.json`, read through `get_config_path()`, which is
profile-aware. Per-agent settings (model, effort, skills) stay in the agent
record; `set_setting` for those keys delegates to `agents.manager`. The schema
routes to the correct writer per spec rather than flattening agent state into
global config — the split is real, and describing it in the schema is what lets
one panel edit both.

### Live vs next-start

Each spec declares whether its change takes effect this session or at the next
start, and `set_setting` returns which one applied. Fields that are re-read per
use (theme, effort, model, search default, tool toggles) are `live`. Fields
that are read once at bind time (`ui.port`, `ui.web_port`, `memory.backend`)
are `next-start`, and the surface says so at the moment of the edit rather than
leaving the user to discover that nothing happened.

## 4. The TUI Settings Panel

`/config` opens a grouped editor overlay in the TUI, reachable also from the
Ctrl+K command palette. It is a persistent panel that edits live state, not a
one-shot wizard — mid-session editing is the need it serves.

The panel is built on the existing `Picker` overlay machinery (filter plus
arrow-select), with a group → field → editor structure on top. Enum and
checkbox fields reuse `Picker` directly; number and text fields use
`LineInput`.

| Group | Field | Widget | Apply | Backing |
|---|---|---|---|---|
| Ports | backend port | number | next-start | `ui.port` |
| | frontend port | number | next-start | `ui.web_port` |
| | open browser | toggle | next-start | `ui.open_browser` |
| Model | default model | picker | live | `default_provider` / `default_model` |
| | thinking effort | picker | live | `agent.thinking_effort` |
| Providers | key status | status+action | live | `api_keys.*` |
| Theme | color theme | picker+preview | live | TUI-local `setTheme` |
| Tools | enabled/disabled | checkbox | live | `tools.disabled` |
| Channels | channel enabled | status+action | mixed | `channels.*` |
| Search | default backend | picker | live | `search.default_provider` |
| Memory | backend | picker | next-start | `memory.backend` |

Field behaviour worth stating:

- **Ports** validate on entry with `port_in_use` and `describe_port_owner`. A
  port held by something that is not ours produces a warning naming the owner;
  the panel reports rather than seizes, matching the stance `_ports.py` already
  takes.
- **Theme** previews on cursor move and rolls back on ESC. There is no separate
  Apply step, because `setTheme` is already a live callback.
- **Providers and Channels** show status and an action, never inline secret
  entry. The panel's job is to display "key set / not set" and launch the
  existing login flow. Credential collection stays in a guided flow, so OAuth is
  never reimplemented in a text box.
- **Keybinds are out of scope.** The TUI has fixed keybinds and no keybind
  config file. A keybinds group would need `cfg['tui']['keybinds']` and a
  per-context schema behind it; the design leaves that unbuilt until there is
  demand for it.

### Command palette

Ctrl+K renders the slash-command registry (name plus description) through the
same `Picker` overlay, with keybind hints. Slash commands are otherwise
discoverable only through `/help` text, and the palette is where `/config`,
`/model`, and `/theme` surface uniformly. It changes no grammar.

## 5. Transport

The TUI panel rides the worker WebSocket already in use. `webui/ws_actions/settings.py`
exports an `ACTIONS` dict with `get_settings` and `set_setting`, registered into
the server dispatch table the same way every other action module is. The panel
sends `{action:'get_settings'}` and `{action:'set_setting', key, value}` over
the same `BackendClient` it uses for `list_models` and `set_default_agent`.

No new transport and no new process: the panel is a client of a connection that
already exists. Web pages reach the same schema over REST at `/api/settings`.

## Appendix: Implementation Status

The schema, the four renderers, and the transport are implemented. Covered
config groups are Ports, Memory, Search, and Tools (per-tool toggles). Model,
effort, theme, and providers are reached from the panel's action rows, which
launch the existing `/model`, `/effort`, `/theme`, and `/login` flows rather
than duplicating them. Keybind editing is designed away rather than deferred
work in progress (§4).

Authoritative code: `openprogram/config_schema.py`,
`openprogram/webui/ws_actions/settings.py`,
`cli/src/components/SettingsPanel.tsx`.
