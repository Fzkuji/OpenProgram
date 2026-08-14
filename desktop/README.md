# OpenProgram Desktop

Electron shell around the OpenProgram web UI, with native browser tabs
(`window.openprogramDesktop.webTab`) backed by WebContentsView.

## Run

    cd desktop && npm install && npm run dev

Loads `http://127.0.0.1:$OPENPROGRAM_WEB_PORT/chat` (default port 18100). If the
worker is not reachable it spawns `openprogram worker start` and waits up to 30s.
Override the startup URL entirely with `OPENPROGRAM_DESKTOP_URL=http://... npm run dev`.

## Build

Release builds stage the Web export, build an OpenProgram wheel, install it into
a uv-managed portable CPython runtime, and include that runtime under Electron
resources:

    npm run dist:dir    # developer-only unpacked artifact
    npm run dist:mac    # explicitly unsigned DMG + ZIP

Packaged builds never fall back to `PATH`, system Python, conda, or the source
checkout. The tag workflow builds explicitly unsigned macOS artifacts and uses
no Apple signing or notarization credentials. Linux source development can run
Electron directly, but no Linux desktop artifact is published until a complete
packaged public-entry gate passes.
