"""Create, list, and restore snapshots of the profile state dir.

Scope is an explicit allowlist of top-level entries under the state dir
(``~/.openprogram/`` or ``~/.openprogram-<profile>/``) — the things that
represent *what the user built*: memory workspace, sessions, config,
programs/functions metadata, channel bindings, agents, skills, plugins.

Everything else is excluded by construction, because an allowlist can't
silently start capturing a new cache directory the way a denylist would.
Caches, trash, logs, locks/pids/ports, the web token, ``node_modules``,
and browser profiles are all regenerated on next start and would only
bloat the archive.

Credentials are the one deliberate opt-out: ``auth/`` and ``mcp_tokens/``
hold live secrets in file form and are skipped unless the user passes
``--include-credentials``, which prints a warning. (They are migrating to
the system keychain, at which point the flag stops mattering.)

Archives land in ``<state>/backups/`` as ``<profile>-<timestamp>.tar.gz``
with mode 0600.
"""
from __future__ import annotations

import os
import sys
import tarfile
import time
from pathlib import Path

# Top-level entries worth preserving. Anything not listed is skipped.
# Keep this list in sync with docs/server/backup.md when it changes.
INCLUDED: tuple[str, ...] = (
    "memory",            # memory workspace (core.md, topics, timeline, ...)
    "sessions",          # session transcripts on disk
    "sessions.db",       # session index
    "session_aliases.json",
    "config.json",       # main configuration
    "cli-config.json",
    "agents",            # per-agent definitions
    "agents.json",
    "programs_meta.json",
    "functions_meta.json",
    "program-sources.json",
    "channels",          # channel account state
    "bindings.json",     # channel <-> session bindings
    "skills",
    "skills.json",
    "plugins",
    "marketplaces.json",
    "mcp_servers.json",
    "models",
    "commands",
    "owner.json",
    "projects",
    "worktrees.json",
    "usage.db",          # usage/accounting history
)

# Only included when --include-credentials is passed.
CREDENTIAL_ENTRIES: tuple[str, ...] = ("auth", "mcp_tokens")

# Never archived, even if nested inside an included entry.
_SKIP_NAMES = frozenset({"node_modules", "__pycache__", ".DS_Store"})
_SKIP_SUFFIXES = (".lock", ".pid", ".port", ".log", ".sock")


def _state_dir() -> Path:
    from openprogram.paths import get_state_dir
    return get_state_dir()


def _profile_name() -> str:
    from openprogram.paths import get_active_profile
    return get_active_profile() or "default"


def backups_dir() -> Path:
    d = _state_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _excluded(path: Path) -> bool:
    """True if this member should be left out of the archive."""
    if path.name in _SKIP_NAMES:
        return True
    if path.name.endswith(_SKIP_SUFFIXES):
        return True
    # Symlinks are skipped rather than followed: a link out of the state
    # dir would silently pull unrelated (possibly huge, possibly secret)
    # trees into the archive.
    return path.is_symlink()


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def _entries_to_archive(state: Path, include_credentials: bool) -> list[str]:
    names = list(INCLUDED)
    if include_credentials:
        names += list(CREDENTIAL_ENTRIES)
    return [n for n in names if (state / n).exists()]


def create_backup(
    include_credentials: bool = False,
    label: str | None = None,
) -> Path:
    """Write a tar.gz of the in-scope state and return its path.

    Raises OSError if the archive can't be written or read back.
    """
    state = _state_dir()
    out_dir = backups_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = f"-{label}" if label else ""
    target = out_dir / f"{_profile_name()}{suffix}-{stamp}.tar.gz"

    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        base = Path(info.name).name
        if base in _SKIP_NAMES or base.endswith(_SKIP_SUFFIXES):
            return None
        if info.issym() or info.islnk():
            return None
        return info

    # Write to a temp name then rename, so an interrupted create never
    # leaves a half-archive that `list` would happily show.
    # ``.partial`` deliberately does not match the ``*.tar.gz`` glob that
    # ``list``/``prune`` use, so an interrupted create is invisible to them.
    tmp = target.with_name(target.name + ".partial")
    try:
        with tarfile.open(tmp, "w:gz") as tar:
            for name in _entries_to_archive(state, include_credentials):
                tar.add(state / name, arcname=name, filter=_filter)
        os.chmod(tmp, 0o600)
        tmp.replace(target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


def _archive_summary(path: Path) -> tuple[int, list[str]]:
    """Return (member count, sorted top-level names). Raises on unreadable."""
    with tarfile.open(path, "r:gz") as tar:
        names = tar.getnames()
    tops = sorted({n.split("/", 1)[0] for n in names})
    return len(names), tops


def _running_processes() -> list[str]:
    """Names of live OpenProgram processes that would fight a restore."""
    live: list[str] = []
    try:
        from openprogram.worker.lifecycle import current_worker_pid, find_running_webui
        pid = current_worker_pid()
        if pid is not None:
            live.append(f"worker (PID {pid})")
        else:
            port, _wpid, source = find_running_webui()
            if source == "unmanaged" and port:
                live.append(f"web server (port {port})")
    except Exception:  # pragma: no cover - worker module optional at runtime
        pass
    return live


def _cmd_backup_create(include_credentials: bool = False) -> int:
    try:
        path = create_backup(include_credentials=include_credentials)
    except OSError as exc:
        print(f"[error] backup failed: {exc}", file=sys.stderr)
        return 1

    # Verify the archive is actually readable before claiming success —
    # a backup you can't open is worse than no backup, because the user
    # stops worrying.
    try:
        count, tops = _archive_summary(path)
    except (OSError, tarfile.TarError) as exc:
        print(f"[error] archive written but unreadable, removing: {exc}",
              file=sys.stderr)
        path.unlink(missing_ok=True)
        return 1

    size = path.stat().st_size
    print(f"Backup: {path}")
    print(f"  size:    {_human(size)}")
    print(f"  entries: {count} files across {len(tops)} top-level items")
    print(f"  content: {', '.join(tops)}")
    if include_credentials:
        print("  WARNING: this archive contains credentials (auth, mcp_tokens) "
              "in plaintext. Store it somewhere you would store a password.")
    else:
        print("  credentials excluded (use --include-credentials to include them)")
    return 0


def _list_backups() -> list[Path]:
    try:
        files = sorted(backups_dir().glob("*.tar.gz"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    return files


def _cmd_backup_list() -> int:
    files = _list_backups()
    if not files:
        print(f"No backups in {backups_dir()}")
        return 0
    print(f"Backups in {backups_dir()}:")
    for path in files:
        stat = path.stat()
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))
        try:
            _count, tops = _archive_summary(path)
            summary = ", ".join(tops[:6]) + (" ..." if len(tops) > 6 else "")
        except (OSError, tarfile.TarError):
            summary = "(unreadable)"
        print(f"  {path.name}")
        print(f"    {when}  {_human(stat.st_size)}  [{summary}]")
    return 0


def _resolve(name: str) -> Path | None:
    """Accept a bare filename, or an absolute/relative path."""
    candidate = backups_dir() / name
    if candidate.is_file():
        return candidate
    direct = Path(name).expanduser()
    return direct if direct.is_file() else None


def _cmd_backup_restore(
    name: str,
    dry_run: bool = False,
    yes: bool = False,
) -> int:
    path = _resolve(name)
    if path is None:
        print(f"[error] no such backup: {name}", file=sys.stderr)
        print("Run `openprogram backup list` to see available archives.",
              file=sys.stderr)
        return 1

    try:
        _count, tops = _archive_summary(path)
    except (OSError, tarfile.TarError) as exc:
        print(f"[error] cannot read archive {path}: {exc}", file=sys.stderr)
        return 1

    state = _state_dir()
    if dry_run:
        print(f"Dry run — restoring {path.name} would overwrite, under {state}:")
        for top in tops:
            marker = "overwrite" if (state / top).exists() else "create"
            print(f"  {marker:>9}  {top}")
        return 0

    running = _running_processes()
    if running:
        print("[error] refusing to restore while OpenProgram is running: "
              + ", ".join(running), file=sys.stderr)
        print("Stop it first with `openprogram stop`, then retry.",
              file=sys.stderr)
        return 1

    if not yes:
        print(f"About to restore {path.name} into {state}.")
        print("This overwrites: " + ", ".join(tops))
        try:
            answer = input("Continue? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 1

    # Safety net: snapshot current state before overwriting it, so a
    # mistaken restore is itself undoable.
    try:
        safety = create_backup(label="pre-restore")
        print(f"Saved current state to {safety.name} before restoring.")
    except OSError as exc:
        print(f"[error] could not back up current state, aborting: {exc}",
              file=sys.stderr)
        return 1

    try:
        with tarfile.open(path, "r:gz") as tar:
            _extract(tar, state)
    except (OSError, tarfile.TarError) as exc:
        print(f"[error] restore failed: {exc}", file=sys.stderr)
        print(f"Your previous state is preserved in {safety}", file=sys.stderr)
        return 1

    print(f"Restored {path.name} into {state}")
    print(f"Restored items: {', '.join(tops)}")
    return 0


def _extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract with path containment, refusing anything outside ``dest``."""
    dest = dest.resolve()
    for member in tar.getmembers():
        if member.issym() or member.islnk():
            continue
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(dest) + os.sep):
            raise tarfile.TarError(f"unsafe path in archive: {member.name}")
    # ``filter="data"`` (3.12+) also strips ownership/permission surprises;
    # fall back for older interpreters, where the loop above is the guard.
    try:
        tar.extractall(dest, filter="data")
    except TypeError:  # pragma: no cover - Python < 3.12
        tar.extractall(dest)


def _cmd_backup_prune(keep: int) -> int:
    if keep < 1:
        print("[error] --keep must be at least 1", file=sys.stderr)
        return 1
    files = _list_backups()
    doomed = files[keep:]
    if not doomed:
        print(f"Nothing to prune — {len(files)} backup(s), keeping {keep}.")
        return 0
    freed = 0
    for path in doomed:
        try:
            freed += path.stat().st_size
            path.unlink()
            print(f"Removed {path.name}")
        except OSError as exc:
            print(f"[warn] could not remove {path.name}: {exc}", file=sys.stderr)
    print(f"Pruned {len(doomed)} backup(s), freed {_human(freed)}. "
          f"{min(keep, len(files))} kept.")
    return 0


__all__ = [
    "INCLUDED",
    "CREDENTIAL_ENTRIES",
    "backups_dir",
    "create_backup",
    "_cmd_backup_create",
    "_cmd_backup_list",
    "_cmd_backup_restore",
    "_cmd_backup_prune",
]
