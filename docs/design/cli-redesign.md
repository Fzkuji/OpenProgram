# OpenProgram CLI / TUI Redesign

Motivation: settings that today live only behind CLI flags (most acutely **ports**) must be editable from a visual, in-app interface. The audit of our own codebase confirms the core problem: settings are fragmented across four surfaces (argparse flags, the questionary `setup` wizard, incomplete web `/settings` pages, and TUI pickers), and the TUI `/config` slash command is a stub that redirects users back to the shell (`cli/src/commands/handler.ts:601-612`). This doc proposes a fix grounded primarily in opencode, secondarily in openclaw.

The non-negotiable design principle, taken from opencode: **opencode has no web-only settings editor.** Its TUI edits live state through `DialogSelect` / `DialogThemeList` dialogs (theme preview-on-move, model/agent/provider pickers). The web dashboard exists but is not the only way to change a setting. Our mistake was building web `/settings` pages and TUI pickers as separate, partial surfaces with no shared backing. We unify them on one schema.

## 1. Command model — keep the verb grammar, close the discoverability gaps

What we already have right, and should keep:

- The single-verb model (`openprogram <verb> <subverb>`, openclaw/`gh`/`docker` style) is correct and matches both references. opencode is noun-at-root / verb-at-subcommand (`session list`, `mcp add`, `console login`); openclaw is the same (`config get/set`, `channels accounts add`). Our `cli.py` already does this with argparse subparsers (`programs run`, `skills list`, `mcp add`, `channels accounts add`).
- Retiring `--tui` / `--web` / `--cli` mode flags in favor of verbs (`openprogram web`, bare `openprogram` = chat) is the right call — opencode does exactly this (`$0` positional launches the TUI, `web`/`serve` are verbs).
- `openprogram ports` is a good, focused command and `_ports.py` is genuinely strong: it mirrors openclaw's three-part port story (liveness probe, identity probe `backend_is_ours`/`frontend_is_ours`, owner diagnostic `describe_port_owner`/`port_owner_hint`) and the reuse-if-ours / report-if-not stance. **Build on this, don't replace it.**

Two concrete gaps to fix, each grounded in an opencode pattern:

**(a) Container verbs must always show their subcommands.** opencode uses `yargs.command(A).command(B).demandCommand()` so a bare `opencode session` prints the subcommand list instead of doing nothing. Several of our argparse container parsers (e.g. when `programs_verb`/`memory_verb` is `None`) rely on ad-hoc `print_help()` calls that aren't uniform. Add a single helper that every container verb's dispatcher calls when its `*_verb` is `None`: print that subparser's help and exit non-zero. One pass over `cli.py`.

**(b) `openprogram config` becomes a first-class concept, not a `setup` alias.** Today `config` is in the TUI-bypass word list (`cli.py:91`) but the actual command is `setup [menu|<section>]`. Rename/alias to `openprogram config`:
- `openprogram config` (no arg) → schema-driven picker menu (the openclaw `run_configure_menu` pick-loop we already have in `wizard.py:265`).
- `openprogram config get <dot.path>` / `config set <dot.path> <value>` → non-interactive, exactly openclaw's `config get/set/unset` with `parseConfigPath`. This gives scriptable edits and a stable target for docs.
- `openprogram config <section>` → jump to one section (already supported).

Naming stays noun-first; `setup` remains as an alias for the first-run linear walk (`run_full_setup`). Help text for each verb already exists; the only addition is the `get`/`set` leaves backed by the schema in §3.

## 2. The visual settings experience — a TUI settings panel, schema-driven, over the existing WebSocket

Decision: **add a real Settings panel to the Ink TUI**, opened by `/config` (and a future Ctrl+K palette entry). Not "another picker" — a grouped editor. Reasoning from the references:

- opencode's settings UX in the terminal is *dialogs that edit live state* (`DialogThemeList`, `DialogSelect` for model/agent/provider). It does not bounce users to a browser for theme/model/provider. Our TUI already has the exact substrate: the `Picker` component (`cli/src/components/Picker.tsx`) is a self-contained overlay with filter + arrow-select, and `openPicker(kind)` is already wired in `REPL.tsx`. A settings panel is the same overlay machinery with a group→field→editor structure on top.
- openclaw's `configure` is a TUI wizard with composable sections (auth, models, gateway, channels, plugins). That validates "in-app, sectioned settings editing in a terminal" as a shape — but openclaw's is a one-shot wizard, not a persistent panel. We take opencode's *persistent, live-edit dialog* model because mid-session editing (toggle a tool, change model, fix a port) is the actual user need the audit identified ("users in mid-session want to toggle tools or change model without exiting").

### What the panel edits, and how each field applies

The panel is organized into groups. Each field declares whether it applies **live** (takes effect this session) or **next-start** (worker/web must restart). This live-vs-next-start flag is the key UX honesty: `set_ui_ports` in `setup.py:150` already documents "takes effect on the next start — nothing live is rebound here." We surface that per-field instead of burying it.

```
Group        Field                 Widget        Apply        Backing
Ports        backend port          number-input  next-start   ui.port      (set_ui_ports)
             frontend port         number-input  next-start   ui.web_port  (set_ui_ports)
             open browser          toggle        next-start   ui.open_browser
Model        default model         picker        live         default_provider/default_model
             thinking effort       picker        live         agent.thinking_effort (per default agent)
Providers    <provider> key set?   status+action live*        api_keys.* (POST /api/config)
Theme        color theme           picker+preview live         (TUI-local: setTheme)
Tools        enabled/disabled      checkbox      live          tools.disabled
Channels     <channel> enabled     status+action mixed         channels.* (status only in panel; login stays a flow)
Search       default backend       picker        live         search.default_provider
Memory       backend               picker        next-start*   memory.backend
```

Notes grounded in our code and the references:
- **Ports** is the headline. The number-input widget is new (Picker is enum-only today); it's a single small Ink input reusing `LineInput`. Editing writes through `set_ui_ports` and the panel shows "saved — takes effect next `openprogram web`/`worker` start", reusing the exact wording the CLI already prints. When the user types a port, validate with `_ports.port_in_use` + `describe_port_owner` and warn if it's held by something not ours — the openclaw report-if-not stance our `_ports.py` already implements.
- **Theme** uses opencode's `DialogThemeList` **preview-on-move / rollback-on-cancel** pattern: set the theme on cursor move, restore the original on ESC, no separate Apply button (`setTheme` is already a live TUI callback in `SlashContext`).
- **Providers / Channels** show *status and an action*, not raw secret entry inline. opencode masks API keys in its provider editor; we already have `/api/config` returning masked keys and `/api/config/verify`. Channel login (QR / token) stays a guided flow — openclaw keeps credential collection in a wizard adapter, not a single field. The panel's job is to show "Anthropic: key set ✓ / not set ✗" and launch the existing flow, not to reimplement OAuth in a text box.
- **Keybinds are deliberately out of scope** for the panel. opencode itself ships **no** visual keybind editor — keybinds are file-only (`tui.json`). Our Ink TUI has fixed keybinds and no keybind config file. Inventing one is unsupported by the findings; defer (see §4 P2-optional).

### Discoverability: a command palette over the slash registry (P2)

opencode's `command-palette.tsx` (Ctrl/Cmd+K) lists commands filtered by namespace/visibility with keybind hints. We have 40+ slash commands in `registry.ts` but they're only discoverable via `/help` text. A Ctrl+K palette that renders `SLASH_COMMANDS` (name + description) through the existing `Picker` overlay raises discoverability with no grammar change, and is where `/config`, `/model`, `/theme` all surface uniformly. This is additive and low-risk; it lands after the panel.

## 3. Config view/edit end-to-end — one schema, three renderers, one writer

The root cause of the fragmentation is that every surface poke the config dict directly: `setup.py` has `read_ui_prefs`/`set_ui_ports`/`read_search_default_provider`; `webui/server.py` has its own `_load_config`/`_save_config`; `routes/config.py` writes `api_keys` directly; each questionary section in `_setup_sections/sections.py` reads/writes its own keys. There is no shared description of "what settings exist."

**Introduce `openprogram/config_schema.py`** — a single ordered registry, the way openclaw centralizes config behind `parseConfigPath`/`setConfigValueAtPath` (with prototype-pollution guards via `isBlockedObjectKey`) and opencode centralizes behind typed `Config.Service` + Zod-like schemas.

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

A single `SETTINGS: list[SettingSpec]` is the source of truth. Two functions replace all ad-hoc access:
- `get_settings() -> list[ResolvedSetting]` — reads `config.json` once, resolves each spec's current value (computing `choices()` lazily), masks secrets.
- `set_setting(key, value) -> {applied: 'live'|'next-start', error?}` — validates against the spec, writes via the *existing* typed helper when one exists (`set_ui_ports` for `ui.*`, `write_search_default_provider` for search, `/api/config` writer for `api_keys`) and falls back to a generic dot-path write (with openclaw's blocked-key guard) otherwise.

Dot-path mutation with the prototype-pollution blocklist is taken straight from openclaw's `parseConfigPath` + `setConfigValueAtPath`/`unsetConfigValueAtPath`. This is what makes `config set ui.port 19000` safe and makes the TUI panel and web pages writers of the *same* validated path.

**Single source of truth = `~/.openprogram/config.json`**, read through `get_config_path()` (already profile-aware via the `_ConfigPathProxy` in `setup.py:35`). Per-agent settings (model, effort, skills) keep living in the agent record; the schema's `set_setting` for those keys delegates to `agents.manager` exactly as `read_agent_prefs`/`read_disabled_skills` already do. The schema does not flatten agent state into global config — it routes to the right writer per spec. This respects the existing split the audit flagged, instead of papering over it.

**Three renderers, zero duplicated field logic:**
1. questionary sections (`_setup_sections/sections.py`) iterate the schema groups instead of hand-coding prompts.
2. the TUI Settings panel iterates the same groups.
3. the web `/settings` pages render the same groups (finishing the incomplete pages by data-driving them).

**Live vs next-start, made explicit by the schema.** `set_setting` returns `applied`. For `live` fields (theme, effort, model, search-default, tool toggles — all already re-read per use today), the change takes effect immediately; the TUI panel reflects it without a restart. For `next-start` fields (`ui.port`, `ui.web_port`, `memory.backend`, backend-exec), the panel shows the "takes effect next start" line and, where relevant, the `_ports.port_owner_hint` if the new port is occupied. This matches both references: opencode reads config lazily so most changes are live; ports/server binding are inherently start-time.

**Transport for the TUI panel = the existing worker WebSocket.** Add `openprogram/webui/ws_actions/settings.py` exporting an `ACTIONS` dict with `get_settings` and `set_setting`, wired into the dispatch table at `webui/server.py:1067-1108` (one `table.update(_ws_settings.ACTIONS)` line, exactly how every other action module is registered). The handlers call `config_schema.get_settings()` / `set_setting()`. The Ink panel sends `{action:'get_settings'}` and `{action:'set_setting', key, value}` over the same `BackendClient` it already uses for `list_models`/`set_default_agent` — no new transport, no new process. The web pages call REST (`/api/config` extended to the generic schema, or a thin `/api/settings` mirroring the WS actions).

## 4. Phased plan (P0 / P1 / P2) for our architecture (argparse + questionary + Ink-over-WS)

**P0 — schema + ports-editable-in-TUI (the user's actual ask). ~2–3 days.**
- `openprogram/config_schema.py`: `SettingSpec`, `SETTINGS` (start with Ports, Model, Theme, Tools, Search groups), `get_settings`, `set_setting` with openclaw-style dot-path write + blocked-key guard. Delegate `ui.*` to existing `set_ui_ports`. *(small–medium)*
- `webui/ws_actions/settings.py` + one line in `server.py` dispatch table. *(small)*
- Ink `SettingsPanel.tsx` overlay (reuse `Picker` for enum/checkbox; add a small number/text field via `LineInput` for ports). Wire `/config` in `handler.ts` to open it instead of the stub at lines 601-612; add `PickerKind`/panel state in `REPL.tsx`. *(medium)*
- Ports field: validate with `_ports.port_in_use` + `describe_port_owner`, show next-start notice. *(small)*

Exit criterion: in a running TUI session, `/config` → Ports → change backend port → see "saved, takes effect next start" with a conflict warning if occupied. This alone satisfies the motivating requirement.

**P1 — unify the other surfaces on the schema; live-preview theme. ~2–3 days.**
- Rewrite `_setup_sections/sections.py` runners to iterate schema groups (questionary widgets chosen by `spec.widget`). New setting = one `SettingSpec`, appears in wizard + TUI automatically. *(medium)*
- Add `openprogram config get/set` leaves in `cli.py` backed by `config_schema`. *(small)*
- Theme group in the TUI panel uses opencode's preview-on-move / rollback-on-ESC. *(medium)*
- Finish the web `/settings` pages by data-driving them from the schema via `/api/settings`. *(medium)* — closes the "incomplete web pages" gap from the audit.

**P2 — discoverability + polish. ~2 days.**
- Ctrl+K command palette over `registry.ts` (opencode `command-palette.tsx`), with keybind hints. *(medium)*
- Container-verb help uniformity in `cli.py` (opencode `.demandCommand()` behavior). *(small)*
- Providers/Channels status-and-action rows in the panel (status from `/api/config` masked keys + channels list; actions launch existing flows). *(medium)*

**Explicitly deferred (unsupported by findings):** a visual keybind editor. opencode ships none (file-only `tui.json`); our TUI has no keybind config file at all. If demand appears, add a `keybinds` group backed by a new `cfg['tui']['keybinds']` map, using opencode's separate-schema-per-context approach — but only then.

### Why this fits our stack specifically
- It adds **no new runtime**: the TUI panel rides the worker WebSocket already in use; the schema is plain Python in the existing config module; questionary and the Ink Picker are reused as-is.
- It removes the four-way fragmentation by making `config_schema.py` the one writer, the way opencode centralizes on `Config.Service` and openclaw on `parseConfigPath`/`mutateConfigFile`.
- It keeps the parts we already did well — the verb grammar, `openprogram ports`, and `_ports.py` ownership diagnostics — and makes them reachable from inside the app instead of only from the shell.