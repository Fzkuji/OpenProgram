# Profiles

This page explains how to run several independent OpenProgram instances side by side on one machine — the common case being a stable instance for daily use and a development instance for hacking on the code.

## Profiles: isolated state directories

By default all state (config / sessions / logs / memory) lives in `~/.openprogram/`. With a profile it moves to `~/.openprogram-<name>/`, and the two instances share no data:

```bash
openprogram --profile dev            # CLI global flag
OPENPROGRAM_PROFILE=dev openprogram  # or the env var — equivalent (the flag wins if both are set)
```

`--profile` is a global flag: put it before the subcommand and it applies to every subcommand — `openprogram --profile dev sessions list`, `openprogram --profile dev restart`, and so on.

## Ports: one per instance

Each instance listens on a single port (API, WebSocket, and the web UI together). The default is 18100. Three ways to change it:

```bash
# 1. Persist into that profile's config (recommended — no flags needed on later starts)
openprogram --profile dev ports --port 18200

# 2. Environment variable, override one run
OPENPROGRAM_WEB_PORT=18200 openprogram web

# 3. Command-line flag, this `openprogram web` invocation only
openprogram web --port 18200
```


`ports` writes into the current profile's config, so each profile remembers its own ports.

## Example: stable + development pair

The stable instance uses the default profile and default port; the development instance uses the `dev` profile on 18200:

```bash
# Stable instance (daily use)
openprogram web                        # http://localhost:18100

# Development instance: write the port into the dev profile (once)
openprogram --profile dev ports --port 18200

# From then on, start it like this
openprogram --profile dev web          # http://localhost:18200
```

Each instance has its own sessions, config, logs, and background worker; `openprogram status` reports on the default instance, `openprogram --profile dev status` on the development one.

On Linux, persistent workers are isolated too. Install each profile from its
own command context and OpenProgram creates a separate systemd user service:

```bash
openprogram worker install
openprogram --profile dev worker install
```

After that, `worker start`, `stop`, `restart`, `status`, and `uninstall` only
operate on the active profile's service. Stopping or upgrading the `dev`
worker does not replace or stop the default worker.
