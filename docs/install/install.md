# Installation

OpenProgram has separate release installations for desktop users and CLI/server users. All supported release installations contain the same complete product capabilities; only the launch shell differs. Source checkout installation is for development only.

## Supported installation matrix

| Platform | Desktop | CLI / Server | Browser client |
|---|---|---|---|
| macOS arm64 / x64 | DMG | Supported | Local or remote |
| Linux x86_64 | No published desktop artifact | Supported | Local or remote |
| Linux arm64 | No published desktop artifact | Supported | Local or remote |
| Windows | Deferred for a later release decision | Deferred for a later release decision | May connect to a supported remote host |
| iOS / Android / iPadOS | No native app | Not applicable | May connect to a supported remote host; mobile layout is not a support commitment |

Only artifacts attached to a published [GitHub Release](https://github.com/Fzkuji/OpenProgram/releases) are release installations. CI artifacts and source-checkout builds are not stable releases.

## Desktop installation

The supported macOS desktop artifact contains Electron and the complete platform product runtime. The runtime includes managed CPython, OpenProgram, the prebuilt Web UI, providers, channels, search, Playwright Chromium, default OCR/model data, and the GUI, Research, and Wiki first-party Programs. It does not use a system Python or Node.js at runtime. Git is required for session and Memory history and is checked by `openprogram doctor`. Linux currently uses the complete CLI/server release because the complete AppImage failed its packaging gate; no reduced Linux desktop artifact is published.

### macOS

1. Download the DMG whose name contains `unsigned` for the machine architecture from GitHub Releases.
2. Verify its SHA-256 against the release checksum file.
3. Open the DMG and copy `OpenProgram.app` to `/Applications`.
4. Start OpenProgram from Applications. Because the current release is not signed with Apple Developer ID, macOS may block the first launch. Open **System Settings → Privacy & Security**, find the OpenProgram notice, and select **Open Anyway**. The checksum verifies the downloaded bytes; the app is not Apple-verified.

## CLI and server installation

The release installer supports macOS and Linux. It downloads the complete platform runtime archive, verifies its SHA-256 and capability manifest, and installs it under `~/.openprogram/runtime/cli/releases/<version>`. On macOS, the same archive is also the Desktop build input. It does not resolve product dependencies, clone the repository, or build JavaScript on the user's machine.

Install the latest stable release:

```bash
curl -fsSL https://openprogram.io/install | sh
```

The short bootstrap resolves the latest stable GitHub Release and then runs the installer from that immutable tag. For a reproducible install of a specific release, pass the version to the shell process:

```bash
curl -fsSL https://openprogram.io/install | OPENPROGRAM_VERSION=0.7.0 sh
```

The command creates `~/.local/bin/openprogram`. If that directory is not already on `PATH`, invoke it by its absolute path or add the directory to the shell configuration.

Before switching `current`, the installer automatically runs the version probe and a worker cold-start/health check. A failed probe leaves the current version unchanged. After installation, run:

```bash
~/.local/bin/openprogram --version
~/.local/bin/openprogram web
~/.local/bin/openprogram doctor
```

The Web UI is served at `http://localhost:18100`. The runtime contains the prebuilt Web UI, so Node.js is not required. Before activation, the installer verifies Web, providers, MCP, memory, channels, search, Chromium, OCR/model data, and all three first-party Programs. `doctor` may still report missing user configuration such as provider credentials.

## Included product and additional extensions

GUI Agent, Research Agent, and Wiki Agent are part of every supported release installation. Their Python dependencies, default OCR data, GPA detector model, and Playwright Chromium are included and require no first-use installation.

Third-party Programs are additional user-selected functionality and are stored separately from the read-only product runtime. Editable first-party Program sources, diagnostics, local frontend builds, and replacement OCR/browser backends are developer additions; they are not required to make a normal installation complete.

## Development checkout

Contributors use a source checkout:

```bash
git clone https://github.com/Fzkuji/OpenProgram.git
cd OpenProgram
./scripts/install.sh
```

This development installer installs the same product capabilities, then adds toolchains, editable sources, tests, diagnostics, local Web/Ink builds, and backend replacement options. It is not the recommended installation for ordinary users and does not define the `stable` channel.

## Data and removal

Configuration, sessions, logs, Programs, and caches live under `~/.openprogram`; replacing the desktop application or CLI runtime does not delete them.

- macOS Desktop: remove `OpenProgram.app`.
- CLI runtime: remove `~/.local/bin/openprogram` and `~/.openprogram/runtime/cli`.
- User data is removed only by an explicit purge of `~/.openprogram` after backup.

See [Upgrading](upgrade.md) for version changes and [Profiles](profiles.md) for isolated state directories.
