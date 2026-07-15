# Server overview

OpenProgram's Web UI, TUI, and CLI all sit on top of one resident local service, called the worker in code and logs. This page covers how it starts, how to check its status, and where the ports and logs are.

## Starting

No manual start is needed. Running `openprogram` (the terminal UI) automatically launches a background worker and connects to it if none is running. To disable the auto-launch, set `OPENPROGRAM_NO_AUTO_WORKER=1`; the TUI then only connects to an existing worker.

For manual control, use the `openprogram worker` subcommands:

```bash
openprogram worker start     # start a worker in the background and return
openprogram worker run       # run in the foreground (blocking), for debugging; Ctrl-C to stop
openprogram worker status    # running or not, PID, port, uptime
openprogram worker stop      # stop (SIGTERM, escalating to SIGKILL if needed)
openprogram worker restart   # stop and start a fresh one
```

`openprogram web` starts the service in the current terminal and opens the browser UI (`http://localhost:18100`).

## status / stop / restart

Three shortcut commands also exist at the top level:

```bash
openprogram status     # is the background service running (PID, port, uptime, log path)
openprogram stop       # stop the background service
openprogram restart    # restart (after code or config changes)
```

Example output of `openprogram status`:

```
openprogram: running (PID 82472, port 18109, up 48m)
  logs: ~/.openprogram/worker.log
```

## Ports

| Port | Purpose | Default |
|------|------|--------|
| backend | FastAPI backend (API + WebSocket); both the TUI and the Web UI connect to it | 18109 |
| frontend | Next.js frontend (the address you open in the browser) | 18100 |

Persistent change:

```bash
openprogram ports --backend 8102 --frontend 8101
```

One-off override: the environment variables `OPENPROGRAM_BACKEND_PORT` / `OPENPROGRAM_WEB_PORT`, or `openprogram web --port <backend> --web-port <frontend>`. Precedence: explicit flags → environment variables → persisted preference → defaults.

## Logs

```bash
openprogram logs list           # all log files (size, last updated)
openprogram logs tail [name]    # last N lines; -n line count, -f follow
openprogram logs path [name]    # print the log file's absolute path
```

There are three log names: `worker` (the default, `~/.openprogram/worker.log`), `runtime` (`~/.openprogram/logs/runtime.log`), and `ink` (TUI startup log, `~/.openprogram/logs/ink-startup.log`).

## Running as a login service

```bash
openprogram worker install      # install as a system service
openprogram worker uninstall    # remove it
```

macOS uses launchd (`~/Library/LaunchAgents/ai.openprogram.worker.plist`); Linux uses systemd --user. Once installed, the worker starts automatically at login and restarts after a crash. `openprogram status` shows whether the service is installed.

## Related pages

- [Configuration and data directory](configuration.md) — what lives in `~/.openprogram/` and how to use `openprogram config`
- [Troubleshooting](troubleshooting.md) — the recurring "it doesn't work" cases
