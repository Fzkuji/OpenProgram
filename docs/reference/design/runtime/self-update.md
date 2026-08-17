# Source checkout upgrades

> Scope: this document defines the gated Git update path for development/source
> checkouts. The current design for Desktop and managed CLI/server releases is
> [Automatic updates](../distribution/automatic-updates.html). Installation and
> packaging remain defined by
> [Installation, packaging, release, and upgrade](../distribution/installation-packaging.html).

## Product boundary

`openprogram upgrade` first detects the installation type and then selects one
of two separate implementations:

| Installation | Meaning of `stable` | Upgrade path |
|---|---|---|
| Managed release | Latest non-draft, non-prerelease GitHub Release | Verify the complete platform runtime and atomically change `current`; do not restart a running worker |
| Source checkout | `origin/main` | Run the gated Git pipeline below and restart only after the candidate passes its cold-start probe |

An unknown installation is rejected. A source checkout never presents a commit
on `main` as a published product release, and a managed installation never
falls back to Git, PyPI, a wheel, or an npm package.

## Problem

A source checkout is commonly installed as an editable Python project. Changes
to that checkout therefore become the next worker's code at restart. An
unverified `git pull` followed by `openprogram restart` can replace a working
worker with code that does not import, build, or start.

Development should happen in a separate worktree. The serving checkout stays
unchanged until the candidate has passed its normal tests and review. The
upgrade command then provides a second, runtime-specific gate before restart.

## Implemented source-checkout flow

`openprogram upgrade` executes these steps in order:

1. **Preflight** refuses a dirty checkout, resolves the configured ref, and
   requires confirmation before a downgrade.
2. **Checkout** fetches and fast-forwards to the target commit.
3. **Dependencies** runs the editable Python install or `npm ci` only when the
   corresponding dependency files changed.
4. **Build** rebuilds the Web export when Web sources changed.
5. **Probe** starts the candidate under an isolated temporary profile, waits for
   `/healthz`, and runs the applicable doctor checks.
6. **Restart** restarts the real worker unless `--no-restart` was requested.
7. **Verify** polls `/healthz` and requires the new Git SHA.

Everything before restart leaves the running worker unchanged. A verify failure
returns `verify-failed`; in normal text output it also prints the exact manual
checkout/restart recovery command. Automatic rollback and chat-sentinel
reporting are not implemented.

## Commands and channels

```bash
openprogram upgrade status
openprogram upgrade --dry-run
openprogram upgrade
```

For source checkouts, the built-in `stable` channel intentionally resolves to
`origin/main`; this is a development-channel name within the source path only.
Managed releases interpret `stable` as the latest published GitHub Release.
`--channel` persists a source checkout's selected ref, while managed releases
accept only `stable`.

`openprogram update` remains a compatibility alias for `openprogram upgrade`.
The complete command, flag, failure, and manual recovery reference is
[Server upgrading](../../../server/upgrading.md).

## Failure semantics

| Failure | Outcome |
|---|---|
| Dirty checkout or unresolved ref | Stop before checkout |
| Dependency, build, doctor, or cold-start probe failure | Stop before restart; the existing worker continues |
| Restart failure | Return a structured non-zero result |
| Restarted service reports the wrong SHA | Return `verify-failed`; normal text output also prints the previous-SHA recovery command |

Progress is recorded in `~/.openprogram/upgrade-state.json`. Without an
explicit `--channel`, `--dry-run` does not change the checkout, worker, or
upgrade state. Supplying `--channel` still persists that source-channel choice.
`--json` emits one machine-readable JSON document on every exit path.

## Implementation status

The source checkout gate, status/dry-run modes, persisted channel, isolated
probe, restart, SHA verification, structured failures, and manual recovery
instruction are implemented in `apps/cli/python/openprogram_cli/_impl/commands/upgrade.py`. Automatic
rollback remains explicitly out of scope for the current implementation.
