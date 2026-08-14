# OpenProgram __TAG__

OpenProgram __TAG__ is a stable complete-product release. Every supported normal-user installation contains the same product capabilities for its platform; the desktop package adds the Electron window to that same runtime.

## Install

### macOS desktop

- Apple Silicon: download [`OpenProgram-__VERSION__-mac-arm64-unsigned.dmg`](https://github.com/Fzkuji/OpenProgram/releases/download/__TAG__/OpenProgram-__VERSION__-mac-arm64-unsigned.dmg).
- Intel: download [`OpenProgram-__VERSION__-mac-x64-unsigned.dmg`](https://github.com/Fzkuji/OpenProgram/releases/download/__TAG__/OpenProgram-__VERSION__-mac-x64-unsigned.dmg).

Follow the [macOS installation guide](https://openprogram.io/docs/install/install.html) for the first launch.

### macOS or Linux CLI/server

```sh
curl -fsSL https://openprogram.io/install | sh
```

The installer selects the matching macOS/Linux arm64/x86_64 runtime, verifies its SHA-256 checksum and complete capability manifest, performs a worker health check, and only then switches the active version.

## Platform guide

| User | Platform | Installation |
|---|---|---|
| Desktop user | macOS Apple Silicon | arm64 DMG above |
| Desktop user | macOS Intel | x64 DMG above |
| CLI/server user | macOS arm64/x86_64 | public install command above |
| CLI/server user | Linux arm64/x86_64 | public install command above |
| Developer | macOS/Linux source checkout | [developer installation guide](https://openprogram.io/docs/install/install.html) |

Windows artifacts are not included in this release. The runtime and desktop packaging remain structured so Windows support can be added in a later release decision. Linux desktop artifacts are not included; Linux users receive the complete product through the CLI/server runtime with Web and TUI access.

The wheel and source distribution attached to this release are developer artifacts, not the normal-user product installer.

## Included product capabilities

- Managed CPython and OpenProgram runtime
- Web UI, CLI/TUI, providers, MCP, memory, channels, and search
- Playwright Chromium, default OCR data, and the GUI detector model
- GUI, Research, and Wiki first-party Programs

Default product capabilities are installed before first use; the supported normal-user paths do not clone the repository, build frontend assets, or resolve product dependencies from PyPI.

## Upgrade and verify

CLI/server users upgrade by running the same public install command again. After installation:

```sh
openprogram --version
openprogram doctor
```

Desktop users replace the previous app with the matching DMG from this release. User state remains under `~/.openprogram`.

## Release integrity

Use the platform `SHA256SUMS-*` files or the archive `.sha256` files to verify downloads. [`release-manifest.json`](https://github.com/Fzkuji/OpenProgram/releases/download/__TAG__/release-manifest.json) records every published artifact size and SHA-256 digest.

See the [installation documentation](https://openprogram.io/docs/install/install.html) and the [commits included in __TAG__](https://github.com/Fzkuji/OpenProgram/commits/__TAG__) for further details.
