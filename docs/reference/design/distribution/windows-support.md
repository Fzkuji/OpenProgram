# Windows support

Scope plan for bringing OpenProgram to Windows. The 2026-08-12 scope
decision in [feature-matrix.html](../feature-matrix.html) marks Windows
`deferred / do-not-plan` across implementation, testing, packaging, and
compatibility; this document is the plan that supersedes ad-hoc effort
whenever that decision is revisited. Until then it also serves as the
triage guide for community-contributed Windows patches.

Rationale recorded for the original deferral: Windows support opens four
fronts at once (porting, a second CI test matrix, packaging + signing,
and sandbox semantics that have no cheap Windows equivalent), while the
current user base runs macOS/Linux and Windows users can already reach a
remote worker from a browser. Deferral is a sequencing decision, not a
rejection.

## Product decision: what "Windows support" means

Support arrives in tiers, each independently shippable and each a
meaningful product on its own. Later tiers are strictly optional.

| Tier | Deliverable | Explicitly out of scope at this tier |
|---|---|---|
| W0 | Community patches accepted, known gaps documented, hard failures degrade to clear errors | Any support commitment |
| W1 | CLI/server runs natively on Windows; UI via browser at `localhost:18100`; Windows CI green | Sandbox, integrated PTY terminal, MCP token file, Desktop app |
| W2 | Windows release artifact + PowerShell installer; `openprogram doctor` Windows-aware | Desktop app, sandbox |
| W3 | Electron Desktop build, signing, auto-update | Sandbox parity |
| W4 | Sandbox story (AppContainer / Job Objects / WSL2 delegation — separate design) | — |

The recommended long-term resting point is **W2**: native CLI/server +
browser UI. W3/W4 only if Windows demand proves out. This mirrors the
Linux posture (no desktop artifact, CLI + browser is the supported path).

## Ground rules for all tiers

These keep the port maintainable instead of a fork-in-place:

1. **One compatibility seam.** Platform branches live in
   `openprogram/_compat.py` (or a sibling module), never inline at call
   sites. The existing `fcntl`/`kill_process_tree` shims are the
   pattern: call sites stay POSIX-shaped, the shim owns the divergence.
   Inline `os.name == "nt"` checks are allowed only where behaviour is
   *dropped* (e.g. directory fsync) rather than *replaced*.
2. **Capability detection over platform detection** where the API
   supports it: `os.supports_dir_fd`, `hasattr(os, "O_NOFOLLOW")` — the
   way `sandbox/recoverable_delete.py` already does it. `sys.platform`
   checks are for genuinely OS-specific facilities (msvcrt, taskkill,
   ConPTY).
3. **Degrade loudly, never crash.** A feature that cannot work on
   Windows raises a clear, single-line "not supported on this platform"
   error at its entry point (the `mcp/server/auth.py` pattern), or is
   hidden from the UI. Silent `NotImplementedError` from deep inside a
   dir_fd call is a bug regardless of tier.
4. **No security downgrades by omission.** Where a POSIX guarantee has
   no Windows equivalent (0o600 modes, O_NOFOLLOW symlink races,
   directory fsync durability), the replacement is chosen consciously
   and the gap is documented in the code comment at the seam — not
   discovered later.
5. **CI is the definition of done.** A tier is not reached until its
   scope is green in the Windows CI job. Patches without a covering
   test that fails on regression do not close a gap.

## W1 work breakdown: native CLI/server

### W1.1 POSIX seam completion (small, partly done)

Already fixed (issue #33 and follow-ups): fsync-on-readonly-handle,
bare `import fcntl` call sites, directory-fsync guards. Remaining:

- `openprogram/store/snapshot/checkpoint/store.py` — the rewind/restore
  apply path (`_open_verified_parent`, `_apply_state`,
  `_restore_changed_guard`) is built on `dir_fd` + `O_NOFOLLOW` +
  `os.link`. Two acceptable resolutions, in preference order:
  a stat-revalidated plain-path fallback behind the existing
  `supports_dir_fd`-style capability constant; or a loud
  "checkpoint rewind is not supported on this platform" error at
  `plan_history_operation`/`plan_rewind_operation` entry. Choose one;
  do not leave the deep NotImplementedError.
- `openprogram/mcp/server/auth.py` — `create_token` requires
  `dir_fd` + `os.link` and refuses on Windows today. W1 keeps the
  refusal but surfaces it in `openprogram doctor` so MCP-server users
  learn the limitation up front. A Windows implementation (CreateFile
  with `FILE_FLAG_OPEN_REPARSE_POINT`, or accepting the weaker
  guarantee) is W2 material.
- Audit for `os.replace`/unlink over open handles. Windows locks open
  files; the git-backed session store and every atomic-write path can
  hit `PermissionError` under concurrent readers. Add one shared
  retry-with-backoff helper to the compat seam and route atomic
  replaces through it.
- Path semantics sweep: `pathlib` everywhere (already dominant), no
  hardcoded `/`; enable long-path awareness in the installer
  (`LongPathsEnabled` note in docs) rather than trying to stay under
  MAX_PATH.

### W1.2 Subprocess & process-tree correctness

`start_new_session`, `os.killpg`, `signal.SIGKILL` usages are already
branched (`agent/exec.py`, `worker/lifecycle.py`, `_compat.py`). W1
verifies each branch with a Windows test, and moves any remaining
inline branch into the compat seam. Worker start/stop/restart cycling
(`openprogram worker …`) is the acceptance test: 100 restart cycles
without orphaned processes.

### W1.3 Windows CI (the core investment)

- GitHub Actions `windows-latest` job running `tests/unit` first.
  Known baseline from issue #33: ~29 failures. Burn the list down;
  every fix lands with the test that covers it.
- Marker policy: `@pytest.mark.posix_only` for tests of genuinely
  POSIX-only behaviour (sandbox, PTY, dir_fd hardening) — skipped on
  Windows with a reason, not xfailed. Everything unmarked must pass.
- Component tests (`tests/component`) join the job once unit is green.
- The job is required for merge from the moment it is green, so it
  can never rot back to red.

### W1.4 Explicit W1 exclusions, wired as such

- Sandbox: `sandbox/__init__.py` already reports "needs
  sandbox-exec/bubblewrap"; add the Windows message ("sandboxing is
  not available on Windows; permission prompts still apply") and make
  sure permission UI copy stays truthful there.
- Integrated PTY terminal: feature-flag off on Windows (the UI hides
  the terminal); ConPTY is W3+ work, tracked separately.
- `install.ps1` (exists for source checkouts) becomes the supported
  dev-env path and gets a smoke test in CI.

## W2 work breakdown: release artifact

- Runtime archive for Windows x64: managed CPython embeddable build,
  wheels, prebuilt Web UI, Playwright Chromium (`playwright install`
  supports Windows natively), OCR/model data. Mirror the existing
  macOS/Linux archive layout under
  `~/.openprogram/runtime/cli/releases/<version>` (`%LOCALAPPDATA%`
  equivalent decided here: keep `~/.openprogram` = `%USERPROFILE%\.openprogram`
  for one config story).
- PowerShell installer with SHA-256 + capability-manifest verification,
  matching the bash installer's gates.
- `openprogram doctor`: git presence, long-path support, Defender
  exclusion hint for the state dir (checkpoint/session write patterns
  trip real-time scanning), ConPTY availability report.
- Update `docs/install/install.md` support matrix from "Deferred" to
  the tier actually shipped.

## W3/W4 sketches (do not start without a separate design pass)

- **W3 Desktop**: electron-builder NSIS target, code-signing cert
  (EV or standard + reputation ramp; unsigned Windows installers are
  effectively unshippable past SmartScreen), auto-update channel,
  window-state/lifecycle code review for Windows conventions.
- **W4 Sandbox**: no lightweight seatbelt/bwrap equivalent exists.
  Candidate directions, each with real costs: AppContainer profiles
  (deep Win32 work), Job Objects + restricted tokens (weaker
  guarantees), or delegating "sandboxed" runs to WSL2 (strong but
  requires WSL). This is a design problem first; any implementation
  before that design is wasted.

## Sequencing and parallelism

W1.1 / W1.2 / W1.3 can run in parallel by different people; W1.3's CI
job should land *first* (red is fine) so every seam fix is measured
against it. W2 starts only after W1 is fully green. Community patches
are triaged against this document: anything advancing W1 is welcome;
anything reaching for W3/W4 early is parked with a pointer here.

## Implementation status

- Done: fcntl compat seam and its call sites; fsync handle semantics;
  directory-fsync guards; process-tree kill branches (issue #33 era).
- In progress: checkpoint store dir_fd resolution (fallback vs loud
  error decision pending review).
- Not started: Windows CI job; open-handle replace/unlink retry seam;
  path sweep; doctor Windows checks; everything in W2+.
