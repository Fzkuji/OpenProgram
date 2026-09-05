# Web UI Port — Configuration and Conflict Handling

How OpenProgram chooses, configures, and defends the port its web UI runs
on: the configuration surface (`openprogram ports`) and what happens when
the port is occupied. The runtime that makes one port sufficient is
described in [single-port.md](single-port.md).

## The port at a glance

| Default | Serves | Configured by |
|---------|--------|---------------|
| `18100` | FastAPI `/api/*`, `/ws`, `/healthz`, and the static web UI export | `ports --port`, `OPENPROGRAM_WEB_PORT`, `ui.web_port` |

The browser, the TUI and the CLI all talk to this one port. There is no
proxy hop and no second process.

### Why 18100

A fixed, uncommon, 5-digit value chosen so it almost never collides with
something already running:

- In the **registered-port** range (`< 49152`), so it never clashes with
  the OS *ephemeral* range the kernel hands out to outbound sockets.
- The `18xxx` block is rarely used by mainstream dev tooling — unlike
  3000 / 5173 / 8000 / 8080, which any other project may already hold.

A fixed port also means a stable, bookmarkable URL and a browser session
(localStorage, service worker scope) that survives restarts.

## Configuration surface

```
explicit flag / arg  >  environment variable  >  stored pref  >  built-in default
```

### `openprogram ports`

```
openprogram ports                 # show the current port
openprogram ports --port 9100     # set + persist
```

Writes to `~/.openprogram/config.json` under `ui.web_port`. **Nothing
live is rebound** — the change takes effect on the next `openprogram web`
/ `openprogram worker` start.

### `openprogram setup ui`

The interactive wizard asks for the port (and the auto-open-browser
pref) and validates range `1–65535`.

### Environment override (single run, not persisted)

- `OPENPROGRAM_WEB_PORT` — the port for this process.

### Per-launch flag

`openprogram web --web-port <p>` overrides for that run without
persisting.

### Where each entry point reads from

| Entry point | Port |
|-------------|------|
| `openprogram web` (`cli/commands/web.py:_cmd_web`) | `--web-port` → `resolve_worker_port()` |
| `openprogram worker` (`worker/runner.py`) | `resolve_worker_port()` |

`resolve_worker_port()` in `openprogram/worker/lifecycle.py` is the one
resolution path: `OPENPROGRAM_WEB_PORT` → pref `ui.web_port` → 18100.
`read_ui_prefs()` / `set_ui_ports()` in `openprogram/setup.py` are the one
read/write path for the persisted pref.

## Conflict handling

The port is pinned on purpose — a stable UI URL is worth more than
"start no matter what". So the policy is **reuse if it's ours, report
and refuse if it's not** — never kill the holder, never silently drift to
a random port. This mirrors openclaw. All probing lives in one module,
`openprogram/_ports.py`:

- **liveness** — `port_in_use(port)`: a bare TCP connect.
- **identity** — `backend_is_ours(port)` first checks the managed worker PID and
  port files plus the active profile's owner-only `web/access.json` snapshot,
  then sends a fresh random nonce to `/api/auth/challenge` and verifies the
  returned token-HMAC proof locally. The probe never sends the owner token or a
  Bearer header to the listener; a foreign process on the port cannot obtain
  the credential. An optional `expected_revision` binds the proof to the
  revision served by an upgrade target.
- **ownership** — `describe_port_owner(port)` / `port_owner_hint(port)`:
  `lsof` / `netstat` + `/proc` / `ps` / Windows CIM to name the holding PID
  and command line, classified ours-vs-foreign. This is what lets a
  "port in use" error say *who* holds it.

### Behavior by case

| The fixed port is… | `openprogram web` | `openprogram worker` |
|--------------------|-------------------|----------------------|
| free | binds, starts | binds, starts |
| held by **our** instance | reuse it, point the browser at the UI | the worker lock already prevents a second worker |
| held by a **foreign** program | refuse; print *who* holds it (PID + cmdline) + how to free it or change the port; do **not** open a browser at it | name the holder, then fall back to a free port and report it (the UI URL tracks it) — the worker also hosts channels, so it must still come up |
| recently-exited (TIME_WAIT) | uvicorn's `SO_REUSEADDR` rebinds it | `_port_available` uses `SO_REUSEADDR`, so a quick self-restart does **not** drift |

The one deliberate asymmetry: `openprogram web` is a foreground UI command,
so a foreign squatter is a hard stop. The worker is a long-running host for
channels *and* the webui, so it stays up, falling back to another port with
a diagnostic message, rather than refusing entirely.

## Relationship to openclaw

openclaw pins its gateway to `18789` and handles conflicts in three
layers; OpenProgram's equivalents:

| openclaw layer | openclaw source | OpenProgram equivalent |
|----------------|-----------------|------------------------|
| single-instance lock (pid + start-time + argv) | `src/infra/gateway-lock.ts` | `worker.lock` (fcntl) + `worker.pid` (with start-time) + `_process_alive` |
| EADDRINUSE retry to ride out TIME_WAIT | `src/gateway/server/http-listen.ts` | `SO_REUSEADDR` on the bind (no retry loop needed) |
| name the holder via `lsof` | `src/infra/ports.ts` | `_ports.describe_port_owner` / `port_owner_hint`, wired into every "port in use" message |

Notably, openclaw's `lsof` diagnostic is **not** on its main gateway-start
path (only on the SSH-tunnel path), so its gateway-start "port in use"
error can't name the holder. OpenProgram wires the owner diagnostic into
the actual start path.
