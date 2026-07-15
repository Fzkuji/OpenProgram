# Web UI Ports — Architecture, Configuration, and Conflict Handling

How OpenProgram chooses, configures, and defends the ports its web UI runs
on. Covers the two-port present, the single-port target, the configuration
surface (`openprogram ports`), and what happens when a port is occupied.

## Ports at a glance

| Role | Default | Serves | Configured by |
|------|---------|--------|---------------|
| Backend | `18109` | FastAPI: `/api/*`, `/ws`, `/healthz` | `ports --backend`, `OPENPROGRAM_BACKEND_PORT`, `ui.port` |
| Frontend | `18100` | Next.js web UI (proxies `/api`, `/ws` to the backend) | `ports --frontend`, `OPENPROGRAM_WEB_PORT`, `ui.web_port` |

The browser talks to the **frontend** port; the frontend proxies API +
WebSocket traffic to the **backend** port (`/api/*` via the Node route
handler `web/app/api/[...path]/route.ts`, which reads the live
`worker.port`; `/ws` + `/healthz` via `next.config.mjs` rewrites against
`OPENPROGRAM_BACKEND_URL`).

### Why 18109 / 18100

Both are fixed, uncommon, 5-digit values chosen so they almost never
collide with something already running:

- In the **registered-port** range (`< 49152`), so they never clash with
  the OS *ephemeral* range the kernel hands out to outbound sockets.
- The `18xxx` block is rarely used by mainstream dev tooling — unlike
  `3000` / `8080` / `5000` / `8888`, which collide constantly. (The old
  defaults were `:3000` frontend and `:8109` backend; `:3000` in
  particular was a frequent squatter.)
- openclaw makes the same choice for the same reason — its gateway is
  pinned to `18789`.

The two values are adjacent only for memorability; nothing requires it.
They **must differ** while the two-port architecture stands.

## Configuration

Precedence, applied independently to each port:

```
explicit flag / arg  >  environment variable  >  stored pref  >  built-in default
```

### `openprogram ports`

```
openprogram ports                                    # show current ports
openprogram ports --backend 18109 --frontend 18100   # set + persist both
openprogram ports --frontend 9100                    # set just one
```

Writes to `~/.openprogram/config.json` under `ui.port` / `ui.web_port`.
**Nothing live is rebound** — the change takes effect on the next
`openprogram web` / `openprogram worker` start. Setting backend == frontend
is rejected with a warning.

### `openprogram setup ui`

The interactive wizard asks for both ports (and the auto-open-browser
pref), validates range `1–65535`, and rejects equal ports.

### Environment overrides (single run, not persisted)

- `OPENPROGRAM_BACKEND_PORT` — backend for this process.
- `OPENPROGRAM_WEB_PORT` — frontend for this process.
- `OPENPROGRAM_WEB_NO_FRONTEND=1` — start the backend only.

### Per-launch flags

`openprogram web --port <backend> --web-port <frontend>` override for that
run without persisting.

### Where each entry point reads from

| Entry point | Backend port | Frontend port |
|-------------|--------------|---------------|
| `openprogram web` (`_cli_cmds/web.py:_cmd_web`) | `--port` → pref → 18109 | `--web-port` → `OPENPROGRAM_WEB_PORT` → pref → 18100 |
| `openprogram worker` (`worker/runner.py`) | `OPENPROGRAM_BACKEND_PORT` → pref → 18109 | `worker/web.py`: arg → `OPENPROGRAM_WEB_PORT` → pref → 18100 |

`read_ui_prefs()` / `set_ui_ports()` in `openprogram/setup.py` are the one
read/write path for the persisted `ui.port` / `ui.web_port`.

## Conflict handling

The port is pinned on purpose — a stable UI URL is worth more than
"start no matter what". So the policy is **reuse if it's ours, report
and refuse if it's not** — never kill the holder, never silently drift to
a random port. This mirrors openclaw. All probing lives in one module,
`openprogram/_ports.py`:

- **liveness** — `port_in_use(port)`: a bare TCP connect.
- **identity** — `backend_is_ours(port)` probes `/healthz` for
  openprogram's signature JSON (`status` + `uptime_seconds`);
  `frontend_is_ours(port)` probes `/` for a Next.js signature
  (`/_next/`, `__next`, `x-powered-by: Next.js`). Distinguishes *our*
  instance from a stranger on the same port.
- **ownership** — `describe_port_owner(port)` / `port_owner_hint(port)`:
  `lsof` / `netstat` + `/proc` / `ps` / `wmic` to name the holding PID
  and command line, classified ours-vs-foreign. This is what lets a
  "port in use" error say *who* holds it.

### Behavior by case

| The fixed port is… | `openprogram web` | `openprogram worker` |
|--------------------|-------------------|----------------------|
| free | binds, starts | binds, starts |
| held by **our** instance | reuse it, point the browser at the UI | the worker lock already prevents a second worker |
| held by **our** leftover Next (frontend) | n/a | `_reclaim_web_port` kills only the orphaned `next-server`, then binds |
| held by a **foreign** program | refuse; print *who* holds it (PID + cmdline) + how to free it or change the port; do **not** open a browser at it | name the holder, then fall back to a free port **loudly** (the UI URL tracks it) — the worker also hosts channels, so it must still come up |
| recently-exited (TIME_WAIT) | uvicorn's `SO_REUSEADDR` rebinds it | `_port_available` uses `SO_REUSEADDR`, so a quick self-restart does **not** drift |

The one deliberate asymmetry: `openprogram web` is a foreground UI command,
so a foreign squatter is a hard stop. The worker is a long-running host for
channels *and* the webui, so it stays up (loud, diagnosed fallback) rather
than refusing entirely.

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

## Single-port future

The two-port split is transitional. The planned migration (see
[`attachment-handling.md`](../ui/attachment-handling.md) sibling work and the
project's single-port notes) static-exports the Next.js SPA and serves it
from the FastAPI backend, collapsing to **one** port (`18109`) that serves
both UI and API. At that point the frontend port, its separate launcher,
the proxy, and most of `worker/web.py` go away — and "frontend port in
use" stops being a possible state. The `openprogram ports` surface stays;
`--frontend` simply becomes a no-op alias once there's a single port.
