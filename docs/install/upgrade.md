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

The command downloads the versioned installer from the immutable release tag. The installer downloads the platform runtime archive used by Desktop, verifies its checksum and complete capability manifest in a new version directory, cold-starts the worker, then changes the `current` symlink. A failure before the change leaves the previous version selected. A running worker is not restarted automatically.

Restart a login service after upgrading:

```bash
openprogram worker restart
```

## Recovering a conversational self-update

This source-checkout capability is separate from stable-release upgrades. On macOS,
if a conversational self-update leaves the default App in maintenance, inspect it
from your local terminal without starting an Agent:

```bash
openprogram self-update status --json
openprogram self-update status UPDATE_ID --json
openprogram self-update repair UPDATE_ID
```

Replace `UPDATE_ID` with the ID reported by `status`. Repair requires an interactive
terminal and the exact confirmation displayed with the action, revision and plan
digest. There is no `--yes` or force-clear option. An existing approved attempt can
resume only within its original ten-minute window; a failed or expired attempt
requires fresh confirmation.

Repair uses the controller saved before the update. It restores the previous App
when rollback remains possible, or completes an already-started irreversible
commit only with the original accepted verification evidence. An aborted,
unactivated transaction retains the old App. Missing or changed evidence leaves
maintenance enabled. Repair restarts the default worker, checks the App identity
and live service, then clears maintenance. It creates no new verifier Job and does
not change the original update verdict. The separate repair result records which
version was recovered; service recovery is not proof that a failed feature meets
its original goal.

If the App or normal CLI cannot start, use the entry saved for that update:

```bash
"$HOME/.openprogram/self-updates/UPDATE_ID/recover.sh" status
"$HOME/.openprogram/self-updates/UPDATE_ID/recover.sh" repair
```

The script uses the original saved runtime outside the App. `status` is also the
default when no argument is given. `repair` still requires interactive owner
confirmation; it does not bypass failed evidence or expired authorization.
`recover.sh resume` invokes the original supervisor within its existing authority
and deadlines, without approving a new update or recreating a verifier Job.

Before activation, OpenProgram also publishes the update's user-owned
`ai.openprogram.self-update.recovery.UPDATE_ID.plist` under `~/Library/LaunchAgents/`.
It runs once per subsequent user login, independently of the App. There is no
resident process or periodic retry, and writing the file does not start another
controller immediately. Recovery does not run before login or disk unlock. If
both the App and controller stop in the current login session, use the saved script
explicitly. Completed updates remove only their unchanged login file; the saved
runtime, script and evidence remain. Missing or damaged trusted recovery files
require manual intervention rather than reconstruction from an unverified App.

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
