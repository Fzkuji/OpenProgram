# Single-Port Architecture

> One process, one port, the Python worker as sole origin. The port's
> configuration surface and conflict handling live in
> [ports.md](ports.md).

## 1. The problem with three processes

The dual-port runtime is three processes with a fragile dependency chain:

```
Electron shell → Next.js server (Node, web port) → Python worker (backend port)
```

- Next `rewrites()` fixes the `/ws` proxy target **at build time**. A build run
  without the right profile env points `/ws` at a dead port, and every client
  shows "disconnected". Two workarounds exist solely for this:
  `_patch_manifest_ports` regex-rewriting `.next/routes-manifest.json`
  (`openprogram/worker/web.py`), and the `/api/[...path]` route handler
  re-reading `worker.port` per request.
- The worker spawns and supervises the Next server: ~330 lines in
  `openprogram/worker/web.py` for port reclaim, orphan `next-server` killing,
  BUILD_ID watching, manifest patching, parent-PID watch
  (`apps/web/scripts/with-parent-watch.mjs`).
- Users need Node at runtime just to render a UI that is already 100%
  client-side.

## 2. Why merging the two is cheap

The frontend has nothing that requires a Node server:

- The app shell is loaded with `next/dynamic` + `ssr: false`
  (`apps/web/app/(shell)/layout.tsx`); every real page is `"use client"`.
- No `middleware.ts`, no server actions, no `next/image`, no `output`/
  `basePath`/`headers` config to unwind.
- The only two route handlers (`app/api/[...path]`, `app/files/[...path]`)
  are proxies to the worker — the origin server under single-port.
- The worker's FastAPI app already serves static content (`/docs` docs-site
  mount, `/files/raw`) and has `docs_url=None`, so there are no route
  collisions.

## 3. Design

One process, one port. The Python worker serves everything:

```
Electron shell → Python worker (FastAPI, single port)
                   ├─ /ws            native WebSocket (index-0 route)
                   ├─ /api/*         native routers
                   ├─ /files/raw
                   ├─ /docs/*
                   └─ /*             Next static export (out/) + SPA fallback
```

### 3.1 The frontend is a static export

`apps/web/next.config.mjs`:

- `output: "export"` → `next build` emits plain HTML/JS/CSS into `apps/web/out/`.
- No `rewrites()` and no `resolveBackend()` — there is no proxy target to
  resolve.
- Frontend code talks to its own origin (`/ws`, `/api/...` relative URLs).

Dynamic page segments (`(shell)/s/[sessionId]`, `(shell)/skills/[...name]`,
`(shell)/settings/providers/[providerId]`, `plugin/[name]/[...slug]`) are
route markers that render null or resolve params client-side from
`pathname`. Static export rejects them without `generateStaticParams`, so
those page files do not exist; the SPA fallback (3.2) serves the shell for
those paths and client-side routing handles the rest. A segment that does
real work keeps a `generateStaticParams` returning one placeholder instead.

`app/api/[...path]/route.ts` and `app/files/[...path]/route.ts` do not exist.

### 3.2 The worker serves the export

`apps/server/openprogram_server/_webui/frontend.py`, mounted last in `create_app()`:

- Static files from `apps/web/out/` (immutable cache headers for `/_next/static`,
  no-cache for HTML).
- SPA fallback: any GET not matching a file or an API route returns the
  shell HTML (`out/chat.html` — the app redirects `/` → `/chat` and resolves
  everything else from `pathname`).
- Build gate: if `apps/web/out/` is missing or older than `apps/web/` sources, run
  `npm run build` once at startup. Node is then a **build-time** dependency
  only; a packaged release ships `out/` pre-built and never invokes Node.

### 3.3 No process supervision

Nothing spawns or watches a Node process. `openprogram/worker/web.py`
(spawn, port reclaim, manifest patch, BUILD_ID watcher),
`apps/web/scripts/with-parent-watch.mjs`, and the `start_web_frontend` call in
`openprogram/worker/runner.py` have no counterpart here.

### 3.4 Port semantics

The backend port is *the* port; web-port knobs are retired:

| | dual-port | single-port |
|---|---|---|
| stable | web 18100 / backend 18109 | **18100** |
| dev | web 18200 / backend 18209 | **18200** |

- `OPENPROGRAM_WEB_PORT` and the `web_port` UI pref are accepted as aliases
  for the backend port during a deprecation window (logging a warning), then
  removed.
- `worker.port` file: unchanged, still the single source of truth for
  discovery.
- Electron `apps/desktop/main.js`: the `WEB_PORT` constant (18200 dev / 18100
  release) simply *is* the worker port; the three usage sites (start URL,
  origin check, navigation guard) need no structural change.
- `scripts/promote_stable.sh`: `npm run build` emits `out/`.

## 4. Invariants

- **The backend is the sole origin.** No proxy layer, no second server, no
  port fixed at build time anywhere. The `/ws` target is correct by
  construction because it is the same origin the page loaded from.
- **API routes always win over static.** The frontend mount registers last;
  the SPA fallback runs only for paths no router claimed.
- **Node is build-time only.** Runtime dependencies are Python plus the
  worker.

## 5. Trade-offs

- Dev iteration loses `next dev` HMR against the merged origin. `npm run dev`
  keeps working by pointing at a running worker through a dev-only env
  (`NEXT_PUBLIC_BACKEND_ORIGIN`) read by the ws/api client helpers; the
  production code path stays origin-relative.
- A dynamic segment that actually rendered content would 404 its deep link
  once its page file is gone. The SPA fallback covers this, and each of the
  four segments is verified individually.
- `out/` can go stale after pulling frontend changes. The startup build gate
  (mtime check) covers it, and `openprogram restart` after `git pull` is the
  documented workflow.

## 6. Acceptance criteria

1. `openprogram` (dev profile) starts exactly one listening port; `lsof`
   shows no `next-server`.
2. Fresh page loads on `/chat`, `/s/<id>`, `/settings/providers/<id>`, and
   `/skills/<name>` all render; `/ws` connects; `/api/pick-folder` works.
3. Killing the worker leaves an already-loaded page showing disconnected;
   restarting reconnects. No orphan processes on any port.
4. A build run with **no** profile env produces a working instance — the
   fixed-at-build-time port failure class is gone by construction.
5. Full test suite passes; desktop app repackaged and verified.

## Distribution boundary

Single port makes Node a build-time dependency and lets every release ship a
prebuilt frontend. The authoritative installation and packaging design is
[Installation, packaging, release, and upgrade](../distribution/installation-packaging.html).
The macOS desktop app embeds CPython and the core dependencies inside the
signed app bundle; it does not download the base Python runtime on first
launch. CLI/server installs use a uv-managed Python environment, while source
checkouts retain the development build flow.

## Appendix: Implementation Status

The single-port design is implemented. Desktop supervision and distribution
status are tracked by the installation and packaging design linked above.
