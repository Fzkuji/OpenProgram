# Upgrading

Upgrade behavior depends on the installation type. A stable installation always moves between published versions; it never follows `origin/main`.

## Desktop release

Desktop automatic update is not enabled until signed cross-version update acceptance passes. Upgrade manually from GitHub Releases:

- macOS: download the new notarized DMG and replace `OpenProgram.app`.
- Linux: download the new AppImage, verify its SHA-256, add execute permission, and replace the previous AppImage.

The application code and embedded Python are replaced together. State under `~/.openprogram` remains unchanged.

## CLI and server release

Run the installer from the target immutable tag and set the same package version:

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  https://raw.githubusercontent.com/Fzkuji/OpenProgram/v0.6.1/scripts/install-release.sh \
  | OPENPROGRAM_VERSION=0.6.1 sh
```

The installer creates a new version directory, installs and probes the exact wheel, then changes the `current` symlink. A failure before the change leaves the previous version selected.

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
