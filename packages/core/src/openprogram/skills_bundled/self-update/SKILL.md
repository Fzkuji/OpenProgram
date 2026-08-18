---
name: self-update
description: "Safely update OpenProgram itself (the running service) to newer code without breaking the instance the user is chatting through. Covers checking for updates, previewing, running the gated upgrade, rollback on failure, and live-testing risky changes on a second instance. Triggers: 'update openprogram', 'upgrade yourself', 'self update', 'pull the latest code', 'update to latest', 'restart with new code', 'apply the code changes'."
---

# Self-update — upgrade the running OpenProgram safely

You are running inside the process being updated. A careless restart on
broken code takes down the very session the user is talking through, so
all code updates go through the gated `openprogram upgrade` command —
never a bare `openprogram restart` after editing code.

## The rules

1. **Never bare-restart to pick up code changes.** `openprogram upgrade`
   is the only sanctioned path. It gates the restart behind: preflight →
   checkout → deps → build → cold-start probe → restart → sha verify.
   Any failure before the restart step leaves the running instance
   untouched.
2. **One failure = stop and report.** Never retry an upgrade or restart
   in a loop. Repeated restarts have crashed the user's whole session
   before.
3. **Never experiment on the serving instance.** To live-test risky
   changes, start a second instance from a git worktree with
   `--profile dev` on another port.

## Workflow

```
1. Check    openprogram upgrade status        # current sha, target sha, update available?
2. Preview  openprogram upgrade --dry-run     # planned steps, nothing executed
3. Run      openprogram upgrade               # the gated chain
4. Verify   the command's own verify step polls /healthz until the sha
            matches; if it reports success you are done
5. Failure  the command prints the exact rollback:
              git -C <repo> checkout <sha> && openprogram restart
            run that verbatim, confirm /healthz answers, then STOP and
            tell the user what failed — do not retry
```

Useful flags: `--yes` (skip downgrade confirmation), `--no-restart`
(prepare everything, let the user restart), `--channel <name>` (persist a
different update channel; `stable` → `origin/main` is the default).

## Preconditions the preflight enforces

- The serving checkout must be **clean** — commit or stash first. If your
  own edits are the update, commit and push them, then upgrade.
- The target ref must exist; downgrades need `--yes` or confirmation.

## Full design

`docs/reference/design/runtime/self-update.md` — step chain rationale,
failure modes, extension points. User docs: `docs/server/upgrading.md`.
