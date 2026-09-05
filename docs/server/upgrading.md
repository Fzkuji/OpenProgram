# Upgrading

`openprogram upgrade` selects behavior from the installation type. A managed
release installs the latest stable GitHub Release and leaves the running worker
unchanged until you restart it explicitly. A source checkout uses the gated Git
pipeline documented on this page: it updates the code and restarts the service
only after proving the new code boots.

For Desktop and managed CLI/server releases, including the one-time v0.6.6 to
v0.7.0 transition, see [Upgrading a release installation](../install/upgrade.md).

## Source checkout behavior

For a source checkout, `upgrade` is the safe replacement for "`git pull` then
`openprogram restart`".

The problem it solves: OpenProgram is installed as an editable checkout, so
the repo it serves from is the repo it develops. A bad commit is invisible
until the next restart, and by then the tool you would use to fix it is the
tool that is broken. `upgrade` moves that failure earlier, to a throwaway
process that nobody depends on.

## Quick reference

```bash
openprogram upgrade status          # what would change? read-only
openprogram upgrade --dry-run       # print the plan; do not move code or worker
openprogram upgrade                 # do it
```

## What happens

Seven steps, in order. The first failure stops the chain and prints a
reason.

| Step | What it does |
|---|---|
| preflight | Refuses a dirty working tree, resolves the target commit, asks before a downgrade |
| checkout | Fast-forwards the checkout to the target commit |
| deps | Refresh the source checkout's Python environment if `pyproject.toml` changed; install the frontend workspaces if `package-lock.json` changed |
| build | Rebuild `apps/web` and/or `apps/cli` when that workspace or `package-lock.json` changed |
| probe | Boots the new code cold on a scratch port under an isolated profile, waits for `/healthz`, runs the doctor checks, kills it |
| restart | Restarts the real service |
| verify | Polls `/healthz` until it reports the new commit sha |

Everything before **restart** leaves the running instance untouched. A
syntax error, a broken config schema, or a failed web build is caught by the
probe, and your service keeps serving the old code.

## Checking before committing to it

`status` tells you whether there is anything to pick up:

```console
$ openprogram upgrade status
  channel        stable (origin/main)
  head           f5671fd25e4c6ae89e6d77f3fcffc4d4a2c0570a
  target         a2d7f95633527e182ac850e40aca727aa0f6a3e6
  update         available
```

Add `--json` for machine output (`head_sha`, `target_sha`,
`update_available`).

`--dry-run` resolves the target and prints the steps it would run without
moving the checkout, worker, or upgrade state. If it is combined with an
explicit `--channel`, that source-channel choice is still persisted:

```console
$ openprogram upgrade --dry-run
  [OK  ] preflight  stable → origin/main: 80d77d1ed44c → 1a4101433b13
  [OK  ] checkout   planned (dry run)
  [OK  ] deps       planned (dry run)
  [OK  ] build      planned (dry run)
  [OK  ] probe      planned (dry run)
  [OK  ] restart    planned (dry run)
  [OK  ] verify     planned (dry run)
```

## Flags

| Flag | Effect |
|---|---|
| `--dry-run` | Print the planned steps without changing checkout, worker, or upgrade state; an explicit `--channel` is still persisted |
| `--no-restart` | Stop after the probe. The checkout moves and the code is verified, but the running service keeps the old code until you restart it yourself |
| `--yes`, `-y` | Skip the confirmation a downgrade requires |
| `--channel NAME` | Follow a different release line, and remember it |
| `--json` | Emit a machine-readable result including every step |

## Source checkout channels

A source-checkout channel is a name for the ref to track. `stable` follows
`origin/main` and is the only one built in. It is a development ref here, not
the stable GitHub Release consumed by managed installations. `--channel`
persists the choice as the `update.channel` setting, so later runs need no flag.

## When a step fails

The failure prints a reason code (`dirty-worktree`, `probe-failed`,
`build-failed`, `verify-failed`, …) and exits non-zero.

- **`dirty-worktree`** — commit or stash your changes. `upgrade` will not
  move a checkout that has work in it.
- **`downgrade-needs-confirmation`** — the target is older than what you are
  running. Old code may not understand config written by new code; pass
  `--yes` if you mean it.
- **`probe-failed`** — the new code does not boot. Nothing was restarted and
  your service is still fine; the fix belongs upstream.
- **`verify-failed`** — the restart happened, but the service is not
  reporting the new sha. Automatic rollback is not implemented yet, so the
  command prints the manual escape hatch:

  ```bash
  git -C <repo> checkout <previous-sha> && openprogram restart
  ```

Progress is written to `~/.openprogram/upgrade-state.json` after every step,
which is where to look if an upgrade dies partway.

## Related

- [Troubleshooting](troubleshooting.md) for problems unrelated to updating.
- `openprogram update` is a compatibility alias for `openprogram upgrade`.
  Managed releases use the stable GitHub Release path; source checkouts use
  the gated Git pipeline described above.
