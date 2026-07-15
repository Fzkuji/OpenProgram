# Upgrading

This page covers updating an existing OpenProgram install to the latest version, and when to re-run the install script.

## openprogram update

```bash
openprogram update           # check and apply updates
openprogram update --check   # check only, don't apply
openprogram update --force   # bypass the 6-hour throttle, check now
```

The update strategy depends on how you installed. A git-clone install (`pip install -e .`, the install script's default) updates via `git fetch` + `git pull --ff-only`:

- The pull is **refused when the working tree has uncommitted changes**, to avoid creating merge conflicts on top of your edits;
- Only fast-forwards are applied — if you have local commits of your own, nothing is force-merged.

A successful update writes a record, and the next `openprogram` start shows an "updated to X" notice. The update only changes the code on disk — a running service needs `openprogram restart` to pick up the new version.

## Automatic updates

The worker checks for and applies updates in the background at startup, at most once every 6 hours; failures are silent and never affect the service. Disable with `OPENPROGRAM_NO_AUTO_UPDATE=1`.

## When to re-run the install script

`openprogram update` only pulls code — it doesn't reinstall dependencies or rebuild the web frontend. If an update leaves you with missing dependencies or a broken page, re-run the install script:

```bash
cd OpenProgram && ./scripts/install.sh    # Windows: .\scripts\install.ps1
```

Every step of the script is idempotent, so re-running is safe at any time — completed steps are skipped or refreshed in place, and your existing configuration and session data are untouched (they live in `~/.openprogram/`, which the script never touches).

A manual upgrade is equivalent to these steps:

```bash
git pull
./scripts/install.sh
openprogram restart
```
