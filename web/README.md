# OpenProgram Web workspace

This workspace contains the Next.js interface used by both the browser and the
Electron App. It is a client of the Python worker and does not own a separate
server-side product runtime.

## Runtime boundary

`next build` produces the static export in `web/out/`. The Python worker serves
that export from the same origin as its REST and WebSocket APIs, normally on
port `18100`. Formal releases copy the export into
`openprogram/webui/_frontend/`; source checkouts can rebuild it from this
workspace. The Desktop App loads the same interface through its worker.

## Source map

- `app/` contains the route tree.
- `components/` contains product-domain React components.
- `lib/` contains client state, API clients, and the Desktop bridge.
- `public/` contains static assets and the remaining legacy chat shell source.
- `tests/` owns pure Node tests; `scripts/check-*.mjs` owns structural and
  component contracts that are exposed through `npm run check`.

## Verification

```bash
npm install
npm run check
npm test
npx tsc --noEmit
npm run build
```

Visible acceptance is performed through `/Applications/OpenProgram.app` and
the default worker/profile, not through a second frontend or worker instance.
