# Backup and restore

`openprogram backup` snapshots the profile state directory — your memory
workspace, sessions, configuration, and channel bindings — into a single
`tar.gz`, and restores it later.

The problem it solves: everything OpenProgram remembers about you lives in one
hidden directory. A bad migration, an experiment on the memory workspace, or a
mistyped `rm` takes all of it at once, and none of it is in git.

## Quick reference

```bash
openprogram backup create           # snapshot now
openprogram backup list             # what do I have?
openprogram backup restore <name> --dry-run   # what would this overwrite?
openprogram backup restore <name>             # do it
openprogram backup prune --keep 5   # delete all but the newest 5
```

Archives land in `~/.openprogram/backups/` (or
`~/.openprogram-<profile>/backups/` under a named profile), named
`<profile>-<timestamp>.tar.gz`. POSIX hosts apply mode `0600`. Windows source
checkouts preserve the state directory's inherited NTFS ACL instead of
rewriting ACL inheritance. The archive is written to a unique temporary file,
flushed, and atomically published; POSIX also flushes the containing directory.

## What gets backed up

The scope is an allowlist, not "everything except". A new cache directory
added by a future release cannot silently start bloating your archives.

| Included | What it holds |
|---|---|
| `memory/` | The memory workspace: `core.md`, topics, timeline, sources |
| `sessions/`, `sessions.db`, `session_aliases.json` | Chat history and its index |
| `config.json`, `cli-config.json` | Your configuration |
| `agents/`, `agents.json` | Agent definitions |
| `programs_meta.json`, `functions_meta.json`, `program-sources.json` | Program and function metadata |
| `channels/`, `bindings.json` | Channel accounts and session bindings |
| `skills/`, `skills.json` | The skill registry |
| `plugins/`, `marketplaces.json` | Installed plugins |
| `mcp_servers.json`, `models/`, `commands/` | MCP servers, model overrides, custom commands |
| `owner.json`, `projects/`, `profiles/`, `worktrees.json`, `usage.db` | Ownership, project and account metadata, worktrees, usage history |

Left out on purpose, because it is regenerated on next start and would only
make the archive bigger:

- `cache/`, `tool_results/`, `browser-states/`, `chrome-profile/`
- `trash/` and `shadow-git/`
- `logs/` and any `*.log`
- Locks, PID files, and port files (`*.lock`, `*.pid`, `*.port`)
- The web token, which is regenerated every launch
- Credential-writer temporary files such as `.env.tmp` and `*.json.tmp`
- Credential-account `profiles/*/home/` trees. Only `metadata.json` and the
  registered `.env` and AuthStore inventory paths are eligible.
- `node_modules/` anywhere in the tree
- Symlinks, which are skipped rather than followed — a link out of the state
  directory would pull unrelated trees into the archive

## Credentials

By default, the archive omits `auth/`, `mcp_tokens/`, profile AuthStore files and `.env` files,
and Channel `credentials.json` files. It removes `config.json[api_keys]` and MCP
server env, header, bearer-token, and OAuth client-secret fields while keeping
the rest of those mixed configuration files.

The Web runtime token and pending Channel pairing codes are never archived,
including when credential opt-in is enabled.

```bash
openprogram backup create --include-credentials
```

This opts in to every registered persistent credential category and prints an
accurate plaintext warning. Store the resulting file with the same access
restrictions as the original credentials. `backup-manifest.json` records only
credential categories that were actually included, redacted, or excluded in
that archive. Global never-backup rules are reported separately under
`credential_policy`, without recording secret values. If you only need to move
to a new machine, logging in again is usually safer than copying the archive.

## Restoring

```bash
openprogram backup restore default-20260811-012458.tar.gz
```

Three things happen before anything is overwritten:

1. **Running-process check.** If a worker or web server is up, the restore is
   refused — restoring session files underneath a live process corrupts both.
   Stop it first with `openprogram stop`.
2. **Confirmation.** The command lists what it is about to overwrite and waits
   for `y`. Pass `-y` to skip this in a script.
3. **Automatic safety snapshot.** Your current state is backed up as
   `<profile>-pre-restore-<timestamp>.tar.gz` first, so a mistaken restore is
   itself undoable. Restoring an archive that was created with
   `--include-credentials` makes that snapshot include credentials too, under
   the same authorization — otherwise the undo would drop the very secrets the
   restore replaced. The command says so when it happens.

Use `--dry-run` to see the overwrite list without any of this happening:

```bash
openprogram backup restore default-20260811-012458.tar.gz --dry-run
```

Restore only replaces the entries present in the archive. State outside that
scope — caches, logs — is left alone. When a mixed file omits or redacts a
registered secret field, restore keeps the current machine's value for that
field instead of replacing it with a mask or deleting it.

The whole archive is validated before any of it becomes visible: containment,
member type, and the JSON shape of every registered secret file. Symlink,
hardlink, and path-traversal members are refused outright, and a rejected
archive leaves your state exactly as it was. Each file is then published
through the same owner-only atomic writer the rest of OpenProgram uses, and
every publish is journalled — so a restore interrupted by a crash or a full
disk is reversed rather than left half-applied. Recovery runs automatically at
the start of the next restore. POSIX uses descriptor-relative traversal;
Windows, where Python does not expose those `dir_fd` operations, validates
containment and rejects symlinks, junctions, and other reparse points before
each fallback path operation.

## Pruning

Backups are never deleted automatically. When they pile up:

```bash
openprogram backup prune --keep 5
```

This keeps the five newest and deletes the rest, printing how much space it
freed. `--keep` defaults to 5, and values below 1 are rejected.

## Profiles

Every subcommand operates on the active profile only. Under
`--profile alpha`, backups are read from and written to
`~/.openprogram-alpha/backups/`, and a restore can never cross into another
profile's state.
