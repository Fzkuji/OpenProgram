# Formal release updates task brief

## Objective

Add stable-release update discovery to OpenProgram without treating `main` commits as user updates or claiming an install action that the current package cannot guarantee.

## Product contract

- Stable users consume only the latest non-draft, non-prerelease GitHub Release.
- Every update is a complete platform artifact with the same product capability manifest.
- macOS Desktop automatically checks, shows release information, downloads the matching unsigned DMG, verifies bytes and SHA-256, and opens the verified DMG.
- macOS/Linux managed CLI installs reuse the existing versioned installer and atomic `current` switch.
- Source checkouts retain the existing explicit Git update pipeline.
- Worker startup no longer checks and applies updates. Immutable runtimes never enter the old Git/PyPI updater, and `openprogram update` becomes a compatibility entry for `upgrade`.
- Install detection distinguishes managed release, source checkout, and unknown; the immutable-runtime marker takes precedence over path heuristics.
- Windows implementation and OS credential-store integration are outside this batch.

## Bounded implementation

1. Remove worker-start automatic apply and retire the old Git/PyPI product-update behavior before adding the replacement paths.
2. Add a dependency-free Desktop update service with strict release/asset validation, atomic cached state, 24-hour successful-check scheduling, 6-hour failure retry, one active request/download, and verified DMG download.
3. Expose only fixed updater actions through preload IPC.
4. Replace the Settings hard-coded version with the real app/host version and add automatic-check, manual-check, release, progress, and error states. A read-only owner-authenticated host-version endpoint performs no update action.
5. Extend `openprogram upgrade` to distinguish managed release installs from source checkouts and reuse `scripts/install-release.sh` for managed releases. Switching `current` does not restart a running worker; the user performs an explicit restart.
6. Update install/upgrade documentation and release gates.

## Acceptance

- Tests are written before each production slice and fail for the missing behavior.
- No new updater dependency is introduced.
- No renderer-provided remote URL or command reaches the main process.
- GitHub metadata, raw installer, and release assets use fixed HTTPS entry points, a bounded redirect count, and per-hop host validation.
- Managed CLI upgrade fixes the repository/version and removes installer test overrides from its child environment.
- Nested manifest paths map to flat GitHub assets only through unique basenames; duplicate basenames reject the release.
- A missing or invalid complete artifact never falls back to a wheel, source archive, another architecture, or an older release.
- Failed Desktop downloads are not opened; failed CLI updates do not change `current`.
- Worker startup never applies a product update, and managed CLI switching never silently restarts the running worker.
- Desktop and Web checks, focused Python tests, documentation links/build, shell syntax, diff check, independent specification review, and independent quality review pass.

## Authoritative design

`docs/reference/design/distribution/automatic-updates.html`
