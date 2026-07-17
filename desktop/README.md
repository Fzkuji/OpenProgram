# OpenProgram Desktop

Electron shell around the OpenProgram web UI, with native browser tabs
(`window.openprogramDesktop.webTab`) backed by WebContentsView.

## Run

    cd desktop && npm install && npm run dev

Loads `http://127.0.0.1:$OPENPROGRAM_WEB_PORT/chat` (default port 18100). If the
worker is not reachable it spawns `openprogram worker start` and waits up to 30s.
Override the startup URL entirely with `OPENPROGRAM_DESKTOP_URL=http://... npm run dev`.

## Build

    npm run dist   # electron-builder --dir -> desktop/dist/
