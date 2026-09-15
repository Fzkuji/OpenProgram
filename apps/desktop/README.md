# OpenProgram Desktop

Electron shell around the OpenProgram web UI, with native browser tabs
(`window.openprogramDesktop.webTab`) backed by WebContentsView.

## Run

    npm install && npm run dev --workspace apps/desktop

Loads `http://127.0.0.1:$OPENPROGRAM_WEB_PORT/chat` (default port 18100). If the
worker is not reachable it spawns `openprogram worker start` and waits up to 30s.
Override the startup URL entirely with `OPENPROGRAM_DESKTOP_URL=http://... npm run dev`.

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
