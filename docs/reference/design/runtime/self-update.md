# Self-update for source checkouts

> Scope: this document defines updates for development/source checkouts. The
> release-installed desktop and CLI mechanisms are defined by
> [Installation, packaging, release, and upgrade](../distribution/installation-packaging.html).
> In release installations, `stable` means a published version; it never means
> `origin/main`.

## 1. Problem

Once OpenProgram is the user's only agent tool, it is also the tool that
develops OpenProgram. Every change then carries the same risk: the process
serving the chat is the process whose code is being edited. A bad edit must
never leave the user without a working instance, because the working
instance is the only way to fix the bad edit.

Concretely today the risk is worse than it looks: the CLI is a **pip
editable install pointing at the working repo** (`pip show openprogram` →
`Location: …/OpenProgram`). An agent editing files in the repo is editing
the live code path of the running server. Python only reads files at import
time, so the running process is safe until restart — but the *next* restart
picks up whatever half-finished state is on disk.

## 2. What already protects us

| Property | Effect |
|---|---|
| All state on disk (`~/.openprogram/`: sessions, DAG, checkpoints, shadow git) | A restart loses only the WebSocket connection; the frontend reconnects and history is intact. |
| Frontend is a static export (`web/out/`) served by the worker | UI changes need only `next build`; zero downtime, no restart. |
| `openprogram restart` | Python-side changes apply in seconds, between turns. |
| `--profile <name>` | Reroutes config/sessions/logs to `~/.openprogram-<name>/` — a second instance can run with fully isolated state. |
| `openprogram worker install` | Login service with crash-restart: if a bad build dies on boot, the supervisor keeps retrying (which also means a *broken* build crash-loops — see §5). |
| `openprogram doctor` | Existing health checks, reusable as the pre-restart gate. |

Missing pieces: nothing gates a restart on the code actually working, and
nothing verifies after a restart that the new code is what is serving.

## 3. Prior art

**OpenClaw** (`openclaw update`, the closest analogue — a resident gateway
that updates itself):

- Fixed step chain for source checkouts: git fetch/checkout → deps install
  → build → `doctor` → restart gateway → **verify the restarted service
  reports the expected new version**. Any failing step aborts with a
  structured reason (`doctor-failed`, …) and does *not* restart.
- The gateway never runs the update inside its own process: it spawns a
  detached helper, exits, and the helper runs the normal CLI update path
  from outside the process tree.
- npm installs are **staged**: install into a temp prefix, verify the
  package tree there, only then swap into the real prefix.
- Downgrades require confirmation (old code may not read new config).

**Claude Code / VS Code**: download and activation are separated — the new
version lands in its own directory while the current session keeps running
the old one; the switch happens on next launch. A failed download has zero
impact.

**Home Assistant / Jupyter**: no hot swap at all — state fully persisted,
restarts cheap, clients auto-reconnect. This is OpenProgram's current
model.

**nginx / Caddy**: true zero-downtime binary swap by passing listener fds.
Overkill for a single-user tool; rejected.

## 4. Design

Two rules, then a command that enforces them.

### 4.1 Rule 1 — the serving checkout is not the editing checkout

Development happens in a `git worktree`; the running instance serves a
stable checkout. The agent edits, tests (`pytest`, `npm run check`), and
builds in the worktree. To try Python changes live, a second instance
starts from the worktree on another port with `--profile dev` so its state
is isolated. Only after verification does the change merge into the
serving checkout.

This rule alone already guarantees an always-working instance: the worst
case is a broken *candidate*, never a broken *server*.

### 4.2 Rule 2 — restart only through the gate

`openprogram upgrade` replaces bare `restart` for code updates. Step chain,
mirroring OpenClaw:

1. **Preflight** — serving checkout clean? Target ref exists? Downgrade →
   ask.
2. **Fetch + checkout** the target ref (default: `origin/main`).
3. **Deps** — `pip install -e .` (only if dependency files changed) and
   `cd web && npm ci` (only if lockfile changed).
4. **Build** — `next build`.
5. **Doctor gate** — `openprogram doctor` plus a cold-start probe: launch
   the new code with `--profile upgrade-probe` on a scratch port, wait for
   `/healthz`, kill it. Import errors, config-schema breaks, and port
   binding failures are all caught here, before the real instance is
   touched.
6. **Restart** the real instance.
7. **Verify** — poll `/healthz` until it reports the expected git SHA (the
   endpoint gains a `version`/`sha` field). Mismatch or timeout →
   **rollback**: `git checkout <previous sha>` + restart + re-verify.

Every step logs a structured result; any failure before step 6 leaves the
running instance untouched.

### 4.3 Detached execution

Like OpenClaw, the upgrade must not run to completion inside the process
being replaced. `openprogram upgrade` runs as a plain CLI process (it
already is one — the CLI is separate from the worker), and when invoked
*from a chat turn* the tool call spawns it detached (`start_new_session`)
so the worker's own restart doesn't kill the upgrade mid-flight. The helper
writes progress to a sentinel file the new worker reads on boot, so the
first chat turn after an upgrade can report "upgraded to <sha>" or "rolled
back: <reason>".

### 4.4 Extension points (design for, don't build yet)

Kept open deliberately so later needs slot in without reshaping the
command:

- **Channels.** The current source-checkout implementation resolves its
  historical `stable` name to `origin/main`. This name is incompatible with
  release installation and must become `dev` during the distribution
  migration. Release-installed `stable` resolves only to a published version.
  CLI shape retains `upgrade --channel <name>` and `upgrade status`.
- **Distribution methods.** The step chain is expressed as
  *resolve target → materialize → verify → activate*. Today "materialize"
  is `git checkout`; a future pip/npm package install implements the same
  four verbs (with OpenClaw-style staged install as its "materialize").
  Steps 5–7 (probe, restart, verify) are distribution-agnostic already.
- **Update source.** Target resolution takes a remote name, defaulting to
  `origin` — a fork or private mirror is a config value.

### 4.5 What stays out of scope

- Hot code reload / fd handover — restart is seconds and sessions survive.
- Automatic background updates — the user (or their agent) initiates.

## 5. Failure modes

| Failure | Caught by | Outcome |
|---|---|---|
| Syntax/import error in new code | Step 5 cold-start probe | No restart; instance untouched |
| Schema/config incompatibility | Step 5 doctor | No restart |
| New code boots but serves wrong version (stale build, wrong checkout) | Step 7 verify | Auto rollback |
| Rollback itself fails | Step 7 re-verify | Sentinel records it; worker supervisor keeps last process alive if possible; manual `git checkout` + `openprogram restart` is the documented escape hatch |
| Crash-loop under `worker install` supervisor | Supervisor restart counter | Supervisor backs off and pins the previous sha |
| Upgrade requested while a turn is running | Step 1 preflight | Wait for idle or require `--force` |

## Implementation Status

The design lands in three layers:

- **Rule 1, discipline only** — worktree development, a second instance under
  `--profile` for live testing, merge then `restart`. Possible with existing
  flags, and current practice.
- **`openprogram upgrade`** — the §4.2 step chain without automatic rollback:
  preflight, checkout, deps, build, probe, restart, verify, plus the `sha`
  field on `/healthz`. Implemented (`openprogram/_cli_cmds/upgrade.py`, user
  docs at `docs/server/upgrading.md`), and structured around the §4.4
  extension points even though one channel and one distribution method exist
  today.
- **Automatic recovery** — rollback on verify failure, sentinel reporting into
  the first post-upgrade chat turn, and supervisor back-off pinning. Not yet
  implemented; a failed verify currently prints the manual rollback command
  instead of rolling back.
