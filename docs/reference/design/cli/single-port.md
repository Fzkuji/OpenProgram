# Single-Port Architecture

Status: design approved, not yet implemented.
Companion: [ports.md](ports.md) (current dual-port semantics — superseded by this doc once implemented).

## 1. Problem

The runtime is three processes with a fragile dependency chain:

```
Electron shell → Next.js server (Node, web port) → Python worker (backend port)
```

- Next `rewrites()` bakes the `/ws` proxy target **at build time**. A build run
  without the right profile env silently points `/ws` at a dead port and every
  client shows "disconnected". Two workarounds exist solely for this:
  `_patch_manifest_ports` regex-rewriting `.next/routes-manifest.json`
  (`openprogram/worker/web.py`), and the `/api/[...path]` route handler
  re-reading `worker.port` per request.
- The worker spawns and babysits the Next server: ~330 lines in
  `openprogram/worker/web.py` for port reclaim, orphan `next-server` killing,
  BUILD_ID watching, manifest patching, parent-PID watch
  (`web/scripts/with-parent-watch.mjs`).
- Users need Node at runtime just to render a UI that is already 100%
  client-side.

## 2. Why the merge is cheap

Verified against the current tree:

- The app shell is loaded with `next/dynamic` + `ssr: false`
  (`web/app/(shell)/layout.tsx`); every real page is `"use client"`.
- No `middleware.ts`, no server actions, no `next/image`, no `output`/
  `basePath`/`headers` config to unwind.
- The only two route handlers (`app/api/[...path]`, `app/files/[...path]`)
  are proxies to the worker — the origin server under single-port.
- The worker's FastAPI app already serves static content (`/docs` docs-site
  mount, `/files/raw`) and has `docs_url=None`, so no route collisions.

## 3. Design

One process, one port. The Python worker serves everything:

```
Electron shell → Python worker (FastAPI, single port)
                   ├─ /ws            native WebSocket (already index-0 route)
                   ├─ /api/*         native routers (already exist)
                   ├─ /files/raw     already exists
                   ├─ /docs/*        already exists
                   └─ /*             Next static export (out/) + SPA fallback
```

### 3.1 Frontend becomes a static export

`web/next.config.mjs`:

- `output: "export"` → `next build` emits plain HTML/JS/CSS into `web/out/`.
- Delete `rewrites()` and `resolveBackend()` entirely — nothing to bake.
- Frontend code talks to its own origin (`/ws`, `/api/...` relative URLs —
  already the case).

Dynamic page segments (`(shell)/s/[sessionId]`, `(shell)/skills/[...name]`,
`(shell)/settings/providers/[providerId]`, `plugin/[name]/[...slug]`) are
route markers that render null or resolve params client-side from
`pathname`. Static export rejects them without `generateStaticParams`, so
**delete the page files** and let the SPA fallback (3.2) serve the shell for
those paths. Client-side routing already handles the rest. Any segment that
turns out to do real work keeps a `generateStaticParams` returning one
placeholder instead.

Delete: `app/api/[...path]/route.ts`, `app/files/[...path]/route.ts`.

### 3.2 Worker serves the export

New module `openprogram/webui/frontend.py`, mounted last in `create_app()`:

- Static files from `web/out/` (immutable cache headers for `/_next/static`,
  no-cache for HTML).
- SPA fallback: any GET not matching a file or an API route returns the
  shell HTML (`out/chat.html` — the app redirects `/` → `/chat` and resolves
  everything else from `pathname`).
- Build gate: reuse the `_ensure_built` idea — if `web/out/` is missing or
  older than `web/` sources, run `npm run build` once at startup. Node is
  then a **build-time** dependency only; a packaged release ships `out/`
  pre-built and never invokes Node.

### 3.3 Delete the process babysitting

- `openprogram/worker/web.py` — entire file (spawn, port reclaim, manifest
  patch, BUILD_ID watcher).
- `web/scripts/with-parent-watch.mjs`.
- `start_web_frontend` call in `openprogram/worker/runner.py`.

### 3.4 Port semantics

The backend port becomes *the* port. Web-port knobs are retired:

| | before | after |
|---|---|---|
| stable | web 18100 / backend 18109 | **18100** |
| dev | web 18200 / backend 18209 | **18200** |

- `OPENPROGRAM_WEB_PORT` and `web_port` UI pref: accepted as aliases for the
  backend port during a deprecation window (log a warning), then removed.
- `worker.port` file: unchanged (still the single source of truth for
  discovery).
- Electron `desktop/main.js`: `WEB_PORT` constant stays (18200 dev / 18100
  release) — it now simply *is* the worker port; the three usage sites
  (start URL, origin check, navigation guard) need no structural change.
- `scripts/promote_stable.sh`: `npm run build` now emits `out/`; no other
  change.

## 4. Invariants

- **Backend is the sole origin.** No proxy layer, no second server, no
  build-time port baking anywhere. `/ws` target is trivially correct because
  it is the same origin the page loaded from.
- **API routes always win over static.** The frontend mount registers last;
  the SPA fallback runs only for paths no router claimed.
- **Node is build-time only.** Runtime dependencies: Python + the packaged
  worker. (Step 2 of the roadmap — Electron supervising the worker — and
  step 3 — PyInstaller bundling — build on this and get their own docs.)

## 5. Risks

- Dev iteration loses `next dev` HMR against the merged origin. Mitigation:
  keep `npm run dev` working by pointing it at a running worker via a
  dev-only env (`NEXT_PUBLIC_BACKEND_ORIGIN`) used by the ws/api client
  helpers when set; production code path stays origin-relative.
- A deleted dynamic segment that actually rendered content would 404 its
  deep link. Covered by the SPA fallback; verify each of the four segments
  during implementation.
- Stale `out/` after pulling frontend changes. The startup build gate
  (mtime check) covers it; `openprogram restart` after `git pull` already
  is the documented workflow.

## 6. Acceptance

1. `openprogram` (dev profile) starts exactly one listening port; `lsof`
   shows no `next-server`.
2. Fresh page load on `/chat`, `/s/<id>`, `/settings/providers/<id>`,
   `/skills/<name>` all render; `/ws` connects; `/api/pick-folder` works.
3. Kill the worker: the page (already loaded) shows disconnected; restart
   reconnects. No orphan processes on any port.
4. A build run with **no** profile env produces a working instance — the
   baked-port failure class is gone by construction.
5. Full test suite passes; desktop app repackaged and verified.
