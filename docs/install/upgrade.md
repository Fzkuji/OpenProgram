# Upgrading

Upgrade behavior depends on the installation type. A stable installation always moves between published versions; it never follows `origin/main`.

## Desktop release

Desktop automatic update is not enabled for the current unsigned macOS distribution. Upgrade manually from GitHub Releases:

- macOS: download the new architecture-matched `unsigned` DMG, verify its SHA-256, and replace `OpenProgram.app`; macOS may require **Privacy & Security → Open Anyway** again.
- Linux: download the new AppImage, verify its SHA-256, add execute permission, and replace the previous AppImage.

The application shell and complete product runtime are replaced together. State under `~/.openprogram` remains unchanged.

## CLI and server release

Run the installer from the target immutable tag and set the same package version:

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  https://raw.githubusercontent.com/Fzkuji/OpenProgram/v0.6.1/scripts/install-release.sh \
  | OPENPROGRAM_VERSION=0.6.1 sh
```

The installer downloads the platform runtime archive used by Desktop, verifies its checksum and complete capability manifest in a new version directory, cold-starts the worker, then changes the `current` symlink. A failure before the change leaves the previous version selected.

Restart a login service after upgrading:

```bash
openprogram worker restart
```

## Development checkout

`openprogram upgrade` applies only to source checkouts. It validates a Git target, updates dependencies and built assets when their source files changed, probes the new checkout, and restarts the worker only after the probe succeeds:

```bash
openprogram upgrade status
openprogram upgrade --dry-run
openprogram upgrade
```

The historical `openprogram update` command remains a compatibility path for existing installations. It does not define stable desktop or managed CLI release behavior.

See [Server upgrading](../server/upgrading.md) for source-checkout recovery details.
