# Installation

OpenProgram has separate release installations for desktop users and CLI/server users. Source checkout installation is for development only.

## Supported installation matrix

| Platform | Desktop | CLI / Server | Browser client |
|---|---|---|---|
| macOS arm64 / x64 | DMG | Supported | Local or remote |
| Linux x86_64 | AppImage | Supported | Local or remote |
| Linux arm64 | No desktop artifact | Supported | Local or remote |
| Windows | Not supported | Not supported | May connect to a supported remote host |
| iOS / Android / iPadOS | No native app | Not applicable | May connect to a supported remote host; mobile layout is not a support commitment |

Only artifacts attached to a published [GitHub Release](https://github.com/Fzkuji/OpenProgram/releases) are release installations. CI artifacts and source-checkout builds are not stable releases.

## Desktop installation

Desktop artifacts contain Electron, a managed CPython runtime, OpenProgram's Python dependencies, and the prebuilt Web UI. They do not use a system Python, Node.js, or Git at runtime.

### macOS

1. Download the DMG for the machine architecture from GitHub Releases.
2. Verify its SHA-256 against the release checksum file.
3. Open the DMG and copy `OpenProgram.app` to `/Applications`.
4. Start OpenProgram from Applications. The published app must pass Gatekeeper validation.

### Linux x86_64

1. Download the x86_64 AppImage and checksum file from GitHub Releases.
2. Verify the SHA-256.
3. Make the file executable and start it:

```bash
chmod u+x OpenProgram-*-linux-x64.AppImage
./OpenProgram-*-linux-x64.AppImage
```

The AppImage does not require root. A Linux arm64 desktop artifact is not currently published.

## CLI and server installation

The release installer supports macOS and Linux. It installs a pinned uv binary, a managed CPython runtime, and one exact OpenProgram wheel under `~/.openprogram/runtime/cli/releases/<version>`. It does not clone the repository or build JavaScript.

Use the installer from the same immutable release tag as the package version:

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  https://raw.githubusercontent.com/Fzkuji/OpenProgram/v0.6.1/scripts/install-release.sh \
  | OPENPROGRAM_VERSION=0.6.1 sh
```

The command creates `~/.local/bin/openprogram`. If that directory is not already on `PATH`, invoke it by its absolute path or add the directory to the shell configuration.

Before switching `current`, the installer automatically runs the version probe and a worker cold-start/health check. A failed probe leaves the current version unchanged. After installation, run:

```bash
~/.local/bin/openprogram --version
~/.local/bin/openprogram web
~/.local/bin/openprogram doctor
```

The Web UI is served at `http://localhost:18100`. The released wheel contains the prebuilt Web UI, so Node.js is not required. `doctor` checks the complete working environment; it may return non-zero before a provider is configured, while the persistent worker is stopped, or when development tools are absent. Those results do not mean the base release installation failed.

## Programs and optional components

Agent programs are not part of the base desktop or CLI artifact. Use `openprogram programs install <name-or-git-source>` only in an installation whose Program environment support is documented by that release. The current Program installer modifies its active Python environment, so it is not enabled as a supported operation inside an immutable desktop package yet.

Browser models, GUI-agent weights, OCR data, and third-party Programs may require separate downloads. Their absence does not invalidate the base installation.

## Development checkout

Contributors use a source checkout:

```bash
git clone https://github.com/Fzkuji/OpenProgram.git
cd OpenProgram
./scripts/install.sh
```

This development installer may install toolchains, use an editable Python package, and build the Web and Ink interfaces with npm. It is not the recommended installation for ordinary users and does not define the `stable` channel.

## Data and removal

Configuration, sessions, logs, Programs, and caches live under `~/.openprogram`; replacing the desktop application or CLI runtime does not delete them.

- Desktop: remove `OpenProgram.app` or the downloaded AppImage.
- CLI runtime: remove `~/.local/bin/openprogram` and `~/.openprogram/runtime/cli`.
- User data is removed only by an explicit purge of `~/.openprogram` after backup.

See [Upgrading](upgrade.md) for version changes and [Profiles](profiles.md) for isolated state directories.
