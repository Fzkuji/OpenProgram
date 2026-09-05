# Installation

OpenProgram has separate release installations for desktop users and CLI/server users. All supported release installations contain the same product runtime; only the launch shell differs. Optional backend dependencies are described below. Source checkout installation is for development only.

## Supported installation matrix

| Platform | Desktop | CLI / Server | Browser client |
|---|---|---|---|
| macOS arm64 / x64 | DMG | Supported | Local or remote |
| Linux x86_64 | No published desktop artifact | Supported | Local or remote |
| Linux arm64 | No published desktop artifact | Supported | Local or remote |
| Windows x86_64 / arm64 | Signed EXE when attached to the release | Supported | Local or remote |
| iOS / Android / iPadOS | No native app | Not applicable | May connect to a supported remote host; mobile layout is not a support commitment |

Only artifacts attached to a published [GitHub Release](https://github.com/Fzkuji/OpenProgram/releases) are release installations. CI artifacts and source-checkout builds are not stable releases.

## Desktop installation

Supported macOS and Windows desktop artifacts contain Electron and the platform product runtime. The runtime includes managed CPython, OpenProgram, the prebuilt Web UI, providers, channels, search, Playwright Chromium, the GPA detector weight, and the GUI, Research, and Wiki first-party Programs. The GUI Program is registered, but the product runtime deliberately excludes PyTorch, OpenCV, and EasyOCR; GUI perception paths that require those dependencies need a separately configured backend or development overlay. The runtime does not use a system Python or Node.js. Git is required for session and Memory history and is checked by `openprogram doctor`. Linux currently uses the same CLI/server runtime because the AppImage failed its packaging gate; no reduced Linux desktop artifact is published.

### macOS

1. Download the DMG whose name contains `unsigned` for the machine architecture from GitHub Releases.
2. Verify its SHA-256 against the release checksum file.
3. Open the DMG and copy `OpenProgram.app` to `/Applications`.
4. Start OpenProgram from Applications. Because the current release is not signed with Apple Developer ID, macOS may block the first launch. Open **System Settings → Privacy & Security**, find the OpenProgram notice, and select **Open Anyway**. The checksum verifies the downloaded bytes; the app is not Apple-verified.

### Windows

1. In GitHub Releases, confirm that the release includes `OpenProgram-<version>-win-x64.exe` or `OpenProgram-<version>-win-arm64.exe` for the machine. If it does not, use the supported CLI/server installation below; unsigned CI or source builds are not release installers.
2. Download the EXE and verify its SHA-256 against `SHA256SUMS-win-x86_64` or `SHA256SUMS-win-arm64`.
3. Open the file properties and confirm that **Digital Signatures** reports a valid signature before running it.
4. Run the assisted per-user installer. It can choose an installation directory and does not require an administrator account.

The Windows app uses the embedded runtime, Windows PowerShell/ConPTY for its Terminal panes, and the same browser, Files, chat, and multi-window surfaces as macOS. Windows Desktop updates verify release metadata, file length, SHA-256, and Authenticode before opening the next installer.

## CLI and server installation

The release installer supports macOS, Linux, and Windows x86_64/arm64. It downloads the complete platform runtime archive, verifies its SHA-256 and capability manifest, and installs it under `~/.openprogram/runtime/cli/releases/<version>`. On macOS, the same archive is also the Desktop build input. It does not resolve product dependencies, clone the repository, or build JavaScript on the user's machine.

Install the latest stable release:

```bash
curl -fsSL https://openprogram.io/install | sh
```

The short bootstrap resolves the latest stable GitHub Release and then runs the installer from that immutable tag. For a reproducible install of a specific release, pass the version to the shell process:

```bash
curl -fsSL https://openprogram.io/install | OPENPROGRAM_VERSION=0.8.1 sh
```

The command creates `~/.local/bin/openprogram`. If that directory is not already on `PATH`, invoke it by its absolute path or add the directory to the shell configuration.

Published Linux runtimes are native CLI/server packages for glibc systems, not
Desktop packages. Both x86_64 and arm64 are consumed on a clean Ubuntu 22.04
(glibc 2.35) image before publication; newer glibc distributions are expected
to work, while Alpine and other musl systems are not release targets. The host
needs `curl`, `tar`, a SHA-256 utility, Git, and the standard shared libraries
used by Chromium. The installer reports a failed browser/runtime probe before
changing the active version. A system Python, Node.js, or npm is not required.

On Windows, run the PowerShell bootstrap:

```powershell
irm https://openprogram.io/install.ps1 | iex
```

To select a specific immutable release:

```powershell
$env:OPENPROGRAM_VERSION = "X.Y.Z"
irm https://openprogram.io/install.ps1 | iex
```

The Windows installer downloads the signed-by-checksum release ZIP, validates
every archive entry before extraction, verifies the complete runtime, and
cold-starts the worker before activating it. It installs versioned runtimes
under `%USERPROFILE%\.openprogram\runtime\cli\releases` and creates
`%LOCALAPPDATA%\OpenProgram\bin\openprogram.cmd`. The launcher directory is
added to the user `PATH`; open a new terminal if the current one does not see
it yet.

Before switching `current`, the installer verifies the architecture-matched manifest and bundled Ink TUI, then runs a worker cold-start/health check on an operating-system-assigned loopback port. Installation is serialized by a per-user lock, files are staged outside the immutable release directory, and `current` is changed atomically only after every check passes. A failed probe leaves the previous version selected. After installation, run:

```bash
~/.local/bin/openprogram --version
~/.local/bin/openprogram web
~/.local/bin/openprogram doctor
```

The Web UI is served at `http://localhost:18100`. The runtime contains the prebuilt Web UI, a private Node.js executable, and the compiled Ink application, so system Node.js is not required. Before activation, the installer verifies Web, providers, MCP, memory, channels, search, Ink, Chromium, the GPA detector weight, and registration and import of all three first-party Programs. `doctor` may still report missing user configuration such as provider credentials.

## Included product and additional extensions

GUI Agent, Research Agent, and Wiki Agent are part of every supported release installation. Their Program packages, the GPA detector weight, and Playwright Chromium are included. The GUI Program is installed without dependency resolution: PyTorch, OpenCV, and EasyOCR are not in the product runtime, so GUI perception paths that use them require a separately configured backend or development overlay.

Third-party Programs are additional user-selected functionality and are stored separately from the read-only product runtime. Editable first-party Program sources, diagnostics, local frontend builds, and replacement OCR/browser backends are developer additions. Replacement backends or dependencies are required only for GUI perception paths that use libraries omitted from the product runtime; they do not change the base runtime manifest.

## Development checkout

Contributors use a source checkout:

```bash
git clone https://github.com/Fzkuji/OpenProgram.git
cd OpenProgram
./scripts/install.sh
```

On Windows, use PowerShell instead:

```powershell
git clone https://github.com/Fzkuji/OpenProgram.git
Set-Location OpenProgram
.\scripts\install.ps1 -Yes
```

The Windows development installer creates an isolated `.venv`, installs the
frontend workspaces from the npm lockfile, builds the browser UI and full Ink
terminal UI, and installs the optional browser and Channel dependencies. It also creates
`%LOCALAPPDATA%\OpenProgram\bin\openprogram.cmd` and adds that directory to the
user `PATH`, so a new PowerShell can invoke `openprogram` without activating
the checkout environment. Node.js 22 LTS is the validated version. `-Minimal`
installs only the Python CLI/server and does not install or build frontend
dependencies.

This development installer installs editable CLI/server sources and the browser
UI. Windows Desktop development is packaged separately with the Desktop
workspace's `dist:win` command; it is not installed by `scripts/install.ps1`.
The release and development installs include the full Ink terminal UI, with
the Python Rich interface as a capability fallback. Windows sandboxing is optional: `auto`
keeps native commands usable when WSL2 with bubblewrap is absent, while an
explicit `workspace-write` policy requires that backend. A source build is not
recommended for ordinary users and does not define the `stable` channel.

## Data and removal

Configuration, sessions, logs, Programs, and caches live under `~/.openprogram`; replacing the desktop application or CLI runtime does not delete them.

- macOS Desktop: remove `OpenProgram.app`.
- macOS/Linux CLI runtime: remove `~/.local/bin/openprogram` and `~/.openprogram/runtime/cli`.
- Windows CLI runtime: remove `%LOCALAPPDATA%\OpenProgram\bin` and `%USERPROFILE%\.openprogram\runtime\cli`.
- Windows Desktop: uninstall OpenProgram from **Installed apps**. User state is retained unless it is explicitly purged.
- User data is removed only by an explicit purge of `~/.openprogram` after backup.

See [Upgrading](upgrade.md) for version changes and [Profiles](profiles.md) for isolated state directories.
