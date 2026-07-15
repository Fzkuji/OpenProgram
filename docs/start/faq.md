# FAQ

The most common questions from installation and daily use, each with the command that solves it.

## Port 18100 or 18109 is already in use?

Check the currently configured ports, then move to free ones:

```bash
openprogram ports                              # show current ports
openprogram ports --backend 18119 --frontend 18110   # persistent change, takes effect on next start
```

To change just one run, override with the environment variables `OPENPROGRAM_BACKEND_PORT` / `OPENPROGRAM_WEB_PORT`. If the port is held by a leftover process, free it with `lsof -ti:18100 | xargs kill` and restart.

## Provider not detected / "No provider available"?

```bash
openprogram providers            # list detected credentials
openprogram providers discover   # scan external sources (Claude Code / Codex / Gemini CLI, ...)
openprogram providers doctor     # diagnose credentials: expiry, refresh, cooldown, conflicts
openprogram setup                # re-run the setup wizard
```

You can also set an environment variable directly (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY`) and restart the service.

## Where is my data stored?

Everything lives under `~/.openprogram/` by default: `config.json` (configuration), `sessions/` (sessions), `logs/` (logs), `memory/` (memory), `usage.db` (token usage). With `--profile <name>` it moves to `~/.openprogram-<name>/`.

## How do I update to the latest version?

```bash
openprogram update           # check and apply updates
openprogram update --check   # check only, don't apply
openprogram update --force   # bypass the 6-hour throttle, check now
```

The worker also auto-checks for updates in the background at startup (at most once every 6 hours). See [Upgrading](../install/upgrade.md).

## The page opened by `openprogram web` won't load?

The page to open is **http://localhost:18100** (the frontend), not :18109 (the backend API, which serves no HTML). If nothing is on 18100 at all, the web UI most likely wasn't built — re-run `./scripts/install.sh`.

## The service doesn't seem to be up / behaves oddly — how do I debug?

In this order:

```bash
openprogram status     # is the service running
openprogram restart    # restart
openprogram doctor     # health check
openprogram rescue     # diagnose problems and print the fix commands
```

## How do I read the logs?

```bash
openprogram logs list            # all log files (size, age)
openprogram logs tail            # last 50 lines of the worker log
openprogram logs tail -f         # follow live
openprogram logs tail runtime    # pick a log: worker / runtime / ink
```

## The GUI agent download is slow or failed?

`openprogram programs install gui` downloads PyTorch (~300 MB for the CPU build, ~3 GB on CUDA machines) and model weights, so a long download is normal. If it fails, re-run the same command to resume. If the GPA detector weight won't download, fetch it manually:

```bash
hf download Salesforce/GPA-GUI-Detector model.pt --local-dir ~/GPA-GUI-Detector
```

## I installed an agent program but it doesn't show up in the UI?

Programs register at startup, so run `openprogram restart` after installing (or hit Refresh on the Functions page). Confirm it's installed with `openprogram programs available`.

## Multiple accounts or keys for the same provider — how do I switch?

```bash
openprogram providers login openai --profile work   # add a second account
openprogram providers use openai work               # switch to the "work" account
openprogram providers list                          # list accounts, the active one is marked
```

## Can one machine run two OpenPrograms at once?

Yes — use profiles to separate the state directories and ports. See [Multiple instances & profiles](../install/profiles.md).

## How do I get an earlier conversation back?

```bash
openprogram sessions list          # list all sessions
openprogram --resume <session_id>  # resume it in the terminal
```

You can also open past sessions directly from the web sidebar.
