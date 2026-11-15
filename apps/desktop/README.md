# OpenProgram Desktop

Electron shell around the OpenProgram web UI, with native browser tabs
(`window.openprogramDesktop.webTab`) backed by WebContentsView.

## Run

    npm install && npm run dev --workspace apps/desktop

Loads `http://127.0.0.1:$OPENPROGRAM_WEB_PORT/chat` (default port 18100). If the
worker is not reachable it spawns `openprogram worker start`, shows a waiting page
and probes `/healthz` every second until the worker answers. While an installer
(`install-app.sh`, `scripts/refresh-local-app.sh`) holds the lock next to the
bundle, `/Applications/.openprogram-app-install.lock`, the backend is down because
it is being replaced: the page says the App is updating and no worker is started
from the half-installed runtime.
Override the startup URL entirely with `OPENPROGRAM_DESKTOP_URL=http://... npm run dev`.

## Layout

- `main.js` is the Electron entry; every module it composes, `main/preload.js`
  and the terminal shell assets live in `main/`. `package.json` `build.files`
  packs `main.js` and `main/**`, so a new module there ships without a list edit.
- `shell/` is the thin shell, and `package.json` `main` points at `shell/loader.js`.
  It picks developer, embedded or managed mode at start. Embedded mode runs
  `main.js` exactly as before; managed mode loads `main.js` from a sealed version
  through `start(context)`. `shell/` must keep working with every version whose
  `shell_compat` falls within `shell/compat.json`.
- `scripts/` holds build steps (packaging, install, the `afterPack` hook).
- `tests/` holds the `npm run check` suite; run one with `npm run check:<name>`.
- `build/icon.icns` is maintained by hand from `build/AppIcon.icon`: nothing
  generates it, so export it again whenever the `.icon` source changes
  (`npm run icon:check` checks the source, not that the two match).

## Build

Release builds stage the Web export, build an OpenProgram wheel, install it into
a uv-managed portable CPython runtime, and include that runtime under Electron
resources:

    npm run dist        # build, validate, and replace /Applications/OpenProgram.app
    npm run dist:mac    # local unsigned packaging; production uses the release workflow
    npm run dist:win          # Windows x64 NSIS package with the complete runtime
    npm run dist:win:arm64    # Windows arm64 NSIS package with the complete runtime

`npm run dist` builds the unpacked app in a random temporary directory. It never
opens that temporary bundle. After validation it replaces the single canonical
`/Applications/OpenProgram.app`, removes the previous bundle and build staging,
and reopens only the canonical path if the app was already running. Repeating the
command does not retain versioned or task-named `.app` copies. Versioned DMG/ZIP
files remain release artifacts because immutable releases and automatic updates
need them.

`npm run dist:thin:mac` and `dist:thin:win` package the shell with the seed in
`build/seed` instead of a runtime; `scripts/release/build-release-seed.sh` makes
that seed. `scripts/release/smoke-thin-shell.sh` checks a first install end to end
in a temporary HOME.

Packaged builds never fall back to `PATH`, system Python, conda, or the source
checkout. The tag workflow requires Developer ID signing and Apple notarization for macOS.
It publishes DMG and ZIP files only after signature and stapled-ticket verification.
Configure MAC_CSC_LINK (base64 PKCS#12), MAC_CSC_KEY_PASSWORD, APPLE_ID,
APPLE_APP_SPECIFIC_PASSWORD and APPLE_TEAM_ID repository secrets, and the
MAC_SIGNING_IDENTITY repository variable. Local release signing uses
`scripts/release/sign-macos-release.py --help` with a Keychain notarization profile. The Windows tag job requires the
Windows signing certificate secrets, signs both the application executable and
NSIS installer, verifies Authenticode, and smoke-tests the embedded runtime.
Missing signing credentials fail publication; local unsigned Windows builds are
development artifacts only. Linux source development can run Electron directly,
but no Linux desktop artifact is published until a complete packaged
public-entry gate passes.

Windows Terminal panes use Windows PowerShell through ConPTY, with `COMSPEC` as
the fallback. The Desktop updater downloads the exact `win-x64.exe` or `win-arm64.exe`, checks its
size and SHA-256, verifies Authenticode, and then opens the visible installer.
No Windows Desktop path changes file ACLs.
