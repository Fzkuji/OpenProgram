# Upgrading

Upgrade behavior depends on the installation type. A stable installation always moves between published versions; it never follows `origin/main`.

Version 0.7.0 is the one-time transition from v0.6.6 to the updater-enabled release line: Desktop users install the v0.7.0 DMG manually; CLI/server users rerun the complete release installer once:

```bash
curl -fsSL https://openprogram.io/install | sh
```

After installing v0.7.0, the Desktop settings and `openprogram upgrade` commands below handle later stable releases.

## Desktop release

The Desktop checks the latest stable GitHub Release automatically. You can also use **Settings → General → Application → Check now**.

- macOS: when a release is available, choose **Download and open DMG**. OpenProgram selects the architecture-matched complete `unsigned` DMG, downloads it to the location you choose, verifies its byte count and SHA-256, and opens it. Quit OpenProgram and replace `OpenProgram.app`; macOS may require **Privacy & Security → Open Anyway** again.
- Linux: rerun the release installer from the target immutable tag. Linux currently has no published desktop package.

The application shell and complete product runtime are replaced together. State under `~/.openprogram` remains unchanged.

## CLI and server release

Check or upgrade to the latest stable release:

```bash
openprogram upgrade --check
openprogram upgrade
```

To select a specific immutable release instead:

```bash
curl -fsSL https://openprogram.io/install | OPENPROGRAM_VERSION=X.Y.Z sh
```

On Windows, the equivalent command is:

```powershell
$env:OPENPROGRAM_VERSION = "X.Y.Z"
irm https://openprogram.io/install.ps1 | iex
```

The command downloads the versioned installer from the immutable release tag. The installer downloads the platform runtime archive, verifies its checksum and complete capability manifest in a staging directory, and cold-starts the worker before publishing or activating that version. macOS and Linux serialize upgrades and atomically change the `current` symlink; Windows atomically replaces the PowerShell launcher and retains its previous launcher. A failure before activation leaves the previous version selected and removes an unpublished staging runtime. Version directories are retained, so rollback uses the same command with the previous `OPENPROGRAM_VERSION`. A running worker is not restarted automatically.

Restart a login service after upgrading:

```bash
openprogram worker restart
```

## Development checkout

In a source checkout, the same command uses the development pipeline instead of the release installer. It validates a Git target, updates dependencies and built assets when their source files changed, probes the new checkout, and restarts the worker only after the probe succeeds:

```bash
openprogram upgrade --check
openprogram upgrade --dry-run
openprogram upgrade
```

The historical `openprogram update` command is a compatibility alias for `openprogram upgrade`.

See [Server upgrading](../server/upgrading.md) for source-checkout recovery details.
The maintained architecture, trust boundaries, UI states, and implementation
evidence are in [Automatic updates](../reference/design/distribution/automatic-updates.html).
