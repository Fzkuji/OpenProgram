# Configure models

OpenProgram needs at least one LLM provider to work. This page covers first-time setup, the provider management commands, and how "enabled models" work.

## First-time setup

```bash
openprogram setup
```

The first-run wizard walks through every configuration section — models, tools, agents, and more. `openprogram setup model` jumps straight to the model section; `openprogram setup menu` opens the interactive picker. The Web UI shows the same wizard on first launch (and Settings → Providers is always available afterwards).

To configure provider credentials only, use the narrower entry point:

```bash
openprogram providers setup      # interactive: scan existing credentials → log in → verify
```

## The providers subcommands

`openprogram providers -h` lists every verb. The common ones:

| Command | What it does |
|---|---|
| `login <provider>` | Log in to a provider. Picks the right method automatically (OAuth or API key); use `--api-key-stdin` in scripts |
| `logout <provider>` | Delete the provider's credentials |
| `status <provider>` | Check whether the current credentials work |
| `list` | List the configured credential pools by profile |
| `available [QUERY]` | List every configurable provider catalog entry (including community ones), optionally filtered by keyword |
| `discover` / `adopt` | Scan credentials already on this machine (Codex CLI, environment variables, etc.) and import them — see [Authentication and credentials](auth.md) |
| `use <provider> [profile]` | With multiple accounts, choose which one the provider currently runs on |
| `doctor` | Diagnose credentials: expiry, refresh, cooldown, conflicts |
| `aliases` / `profiles` / `migrate` | Short-name aliases, account profile management, credential format migration |

## Enabled models

Configuring credentials does not by itself make models selectable. Each provider has an "enabled models" list, and only enabled models appear in the chat UI's model picker.

- Mechanism: the registry (`openprogram/providers/enabled_models.py`) reads each provider's model entries from the config file at startup and builds the runtime model list; config changes take effect on reload.
- Config file: `~/.openprogram/config.json` (`~/.openprogram-<name>/config.json` when using `--profile <name>`). Each provider lives under `providers.<id>`: an `enabled` flag, the enabled model entries (with specs such as context window and pricing), and manually added `custom_models`.
- How to enable models: tick them in the setup wizard; browse the provider's model list under Settings → Providers in the Web UI and tick them there (the Fetch button re-pulls the official model catalog); subscription providers auto-enable a default model set on login. Editing config.json by hand is normally unnecessary.
- Default model: the top-level `default_provider` and `default_model` config keys decide which model a new session uses; switching models in the UI updates them.

## Other pages in this section

- [Providers](providers.md) — built-in provider catalog, access methods, library usage
- [Authentication and credentials](auth.md) — credential sources, storage location, importing from other CLIs
- [Fast tier](fast-tier.md) — routing requests to a faster service tier
- [Thinking effort](thinking-effort.md) — reasoning depth levels
- [Token tracking](token-tracking.md) — how each provider reports usage
