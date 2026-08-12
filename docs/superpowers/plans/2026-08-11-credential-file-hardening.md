# Credential file hardening implementation plan

Specification: `docs/reference/design/providers/auth/credential-file-hardening.html`.

## Global constraints

- Keep local plaintext files. Do not add Keychain, keyring, encrypted databases, a new secret backend, or arbitrary shell-based secret resolution.
- Follow strict TDD: record each expected RED failure before production changes and keep behavior-focused tests.
- A secret is never visible before owner-only permissions apply. POSIX files are `0600`, secret directories `0700`; Windows permits only the current user and SYSTEM and must verify the ACL.
- Reject symlinks below the resolved state root, non-regular files, foreign owners, mask/sentinel writeback, and copy fallback after `EXDEV`.
- Use stable per-record locks around complete read-modify-write operations. Preserve legacy file formats and state roots.
- API, Web, CLI, logs, doctor, backup manifests, and errors must not return raw secret values. The legacy key reveal route returns a stable deprecation error.
- Do not mark a feature-matrix row complete until every gate in the specification passes.

## Task 1: Inventory, private atomic primitive, and safe backup defaults

Implement the typed secret inventory and the smallest reusable private atomic writer. Make backup archives its first consumer so archives are owner-only from creation, fsynced, atomically published, and directory-durable on POSIX. Correct default backup redaction/exclusion for mixed `config.json`, Channel credentials, MCP env/headers/auth, profile AuthStore and `.env`; always exclude Web runtime tokens and pending pairing codes. Preserve explicit `--include-credentials` with accurate warning and manifest. Restore of redacted/missing secret fields preserves local secrets.

Required verification: umask 000/022, symlink and fixed-temp attacks, fsync/replace failures, default archive contains zero registered raw secrets, opt-in contains exactly allowed persistent secrets, runtime/pairing tokens never appear, archive mode is private from first visibility, and existing backup compatibility tests.

## Task 2: Migrate every native secret writer

Migrate `config.json[api_keys]`, account `.env`, Channel credentials/access, MCP token/config, AuthStore, and Web token to the shared primitive without changing their serialized schemas or lifecycle. Add stable cross-process locks and revision/conflict checks around read-modify-write. Preserve `committed_not_durable` after replace succeeds but directory fsync fails; never overwrite the visible new value with the old value in that case.

Required verification: first create and replacement, two processes, external edit conflict, lock timeout, symlink/non-regular/foreign-owner rejection, POSIX and Windows ACL contract tests, and affected subsystem suites.

## Task 3: Inventory-driven doctor, masked editing, and deletion truthfulness

Add `openprogram doctor credentials` and `--repair` using the inventory. Repair only current-user regular files/directories, never follow symlinks or take ownership. Make all secret edits use omit/replace/delete tri-state semantics and reject masks/sentinels. Remove raw MCP editor flow and account-key reveal UI/CLI behavior; the legacy reveal route returns a stable deprecation error without a value. Make Channel and other secret deletion report failure unless absence is verified, and clear relevant runtime caches.

Required verification: no raw secret in API/CLI/Web/doctor/log projections, mask rejection, tri-state behavior, historical 0644 repair, foreign-owner/symlink refusal, deletion failure injection, and legacy client compatibility responses.

## Task 4: Staged restore and crash recovery

Restore through a same-filesystem staging directory. Validate manifest, containment, member type, JSON shape, inventory, ownership, and permissions before publication. Reject symlink, hardlink, unknown secret path, and traversal members. Publish each target with the shared writer and a durable restore journal; reverse already-published targets after mid-restore failure. Only report success after final inventory and permission verification. Respect the explicit credential authorization for pre-restore snapshots.

Required verification: malicious tar corpus, redacted-field preservation, permission failures, injected crashes before/after each publish point, journal idempotence, complete old-or-new state, and POSIX/Windows backup-restore acceptance.

## Task 5: Whole-feature verification and documentation

Run focused, affected, full unit, CLI/Web contract, docs-link, matrix-mechanical, lint, type, and build checks. Perform a final secret-value scan over response fixtures and produced archives. Update implementation evidence in the HTML design and feature matrix only for gates proven by tests; record platform limitations and existing unrelated failures separately.
