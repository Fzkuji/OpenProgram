"""``.worktreeinclude`` — copy untracked files a fresh worktree needs.

``git worktree add`` only ever checks out tracked content; local-only
files (``.env``, per-machine certs, ``*.local.json``) never show up in
the new worktree even though the agent's tools immediately need them.

If ``source_repo/.worktreeinclude`` exists, each line is a
gitignore-style pattern (glob, directory, ``#`` comment, blank line).
After ``git worktree add`` succeeds we resolve the matching
**untracked** paths in ``source_repo`` and copy any that don't already
exist in the new worktree.

No ``.worktreeinclude`` file → :func:`sync_include_files` returns
immediately, zero git calls.

Pattern matching: hand-rolled, not git's own exclude engine. ``git
check-ignore`` / ``ls-files --exclude-from`` only ever *exclude*
matches from a listing — there's no plumbing form that treats a
pattern file as an *include* filter, and pointing git at an arbitrary
pattern file otherwise requires ``core.excludesFile`` config
injection, which this codebase's sandbox refuses to run. So we get the
full untracked-file list from ``git ls-files --others`` (deliberately
without ``--exclude-standard``, so already-gitignored files like
``.env`` are still candidates) and match each path against the
manifest ourselves.

Supported pattern syntax (the common subset of gitignore(5) most
``.worktreeinclude`` entries need):

* ``#`` at the start of a line (after stripping whitespace) is a comment.
* Blank lines are ignored.
* ``*`` and ``?`` and ``[...]`` — standard ``fnmatch`` glob wildcards.
* A pattern containing no ``/`` matches the basename anywhere in the
  tree (e.g. ``*.local.json`` matches both ``a.local.json`` and
  ``sub/a.local.json``).
* A pattern containing a ``/`` is anchored to the repo root and
  matched against the full relative path.
* A trailing ``/`` marks a directory pattern — matches the directory
  and everything under it.
* Leading ``/`` is stripped (patterns are always repo-root relative
  here; there's no per-directory ``.gitignore`` nesting to disambiguate).

Not supported (falls back to literal/glob matching, i.e. these
characters are not treated specially): ``!`` negation, ``**``
double-star, character-class edge cases beyond what ``fnmatch``
provides. ``.worktreeinclude` entries needing those should be split
into multiple plain patterns instead.
"""
from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

_GIT_TIMEOUT_SECS = 30.0


@dataclass
class IncludeSyncResult:
    """Outcome of one :func:`sync_include_files` call."""

    copied: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)

    @property
    def attempted(self) -> bool:
        return bool(self.copied or self.skipped_existing or self.failed)


def _parse_patterns(manifest_text: str) -> list[tuple[str, bool]]:
    """Parse ``.worktreeinclude`` text into ``(pattern, is_dir)``
    pairs, dropping comments and blank lines. See module docstring for
    the supported syntax."""
    patterns: list[tuple[str, bool]] = []
    for raw in manifest_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        is_dir = line.endswith("/")
        if is_dir:
            line = line.rstrip("/")
        line = line.lstrip("/")
        if line:
            patterns.append((line, is_dir))
    return patterns


def _matches(rel_path: str, patterns: list[tuple[str, bool]]) -> bool:
    """True if ``rel_path`` (posix-style, relative to repo root)
    matches any manifest pattern."""
    p = PurePosixPath(rel_path)
    parts = p.parts
    for pattern, is_dir in patterns:
        if "/" in pattern:
            # Anchored: match the full relative path (or, for a
            # directory pattern, any path under it).
            if is_dir:
                pat_parts = tuple(pattern.split("/"))
                if parts[: len(pat_parts)] == pat_parts:
                    return True
                if fnmatch.fnmatch("/".join(parts[: len(pat_parts)]), pattern):
                    return True
            elif fnmatch.fnmatch(rel_path, pattern):
                return True
        else:
            # Unanchored: match the basename, or (for a directory
            # pattern) any ancestor directory name.
            if is_dir:
                if any(fnmatch.fnmatch(part, pattern) for part in parts[:-1]):
                    return True
            elif fnmatch.fnmatch(p.name, pattern):
                return True
    return False


def sync_include_files(source_repo: str, worktree_path: str) -> IncludeSyncResult:
    """Copy files named by ``source_repo/.worktreeinclude`` into
    ``worktree_path``.

    * Only **untracked** paths in ``source_repo`` are ever candidates
      (via ``git ls-files --others``) — tracked files are already
      checked out by ``git worktree add`` itself.
    * A destination path that already exists is left alone.
    * Symlinks are copied as links, not followed.
    * File permissions are preserved (``shutil.copy2``).
    * One file's failure is recorded and does not stop the rest.
    """
    result = IncludeSyncResult()
    manifest = Path(source_repo) / ".worktreeinclude"
    if not manifest.is_file():
        return result

    try:
        patterns = _parse_patterns(manifest.read_text(errors="replace"))
    except OSError as e:
        result.failed.append((".worktreeinclude", f"could not read manifest: {e}"))
        return result
    if not patterns:
        return result

    try:
        proc = subprocess.run(
            ["git", "ls-files", "--others", "-z"],
            cwd=source_repo,
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECS,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        result.failed.append((".worktreeinclude", f"git ls-files failed: {e}"))
        return result
    if proc.returncode != 0:
        result.failed.append((
            ".worktreeinclude",
            f"git ls-files failed (rc={proc.returncode}): "
            f"{proc.stderr.decode(errors='replace').strip()}",
        ))
        return result

    all_untracked = [
        p.decode(errors="replace")
        for p in proc.stdout.split(b"\x00")
        if p
    ]
    rel_paths = [p for p in all_untracked if _matches(p, patterns)]

    src_root = Path(source_repo)
    dst_root = Path(worktree_path)
    for rel in rel_paths:
        src = src_root / rel
        dst = dst_root / rel
        try:
            if dst.exists() or dst.is_symlink():
                result.skipped_existing.append(rel)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_symlink():
                link_target = os.readlink(src)
                os.symlink(link_target, dst)
            else:
                shutil.copy2(src, dst, follow_symlinks=False)
            result.copied.append(rel)
        except OSError as e:
            result.failed.append((rel, str(e)))

    return result


__all__ = ["IncludeSyncResult", "sync_include_files"]
