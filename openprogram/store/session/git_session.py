"""One git repo per session — thin wrapper over ``git`` CLI.

Git is the truth-of-storage for session memory. Each session owns one
repo at ``<root>/<session_id>/``. This class owns:

  * lazy-init (no repo on disk yet → create on first commit)
  * append a file under ``history/`` (sequential, no overwrite)
  * read files under ``context/`` (``read_context_file`` — the per-turn
    ContextCommit files under ``context/commits/`` are written directly
    by ``context.commit.store.save_commit``, not through this class)
  * commit (one per turn — same threading.Lock per session)
  * log / checkout / branch (used by UI rewind + retry)

Concurrency model: per-session ``threading.Lock`` serializes commit
operations. Different sessions are independent repos so they don't
contend. Future multi-process support would add a ``flock`` on
``.git/agentic.lock`` — not in scope here.

Subprocess overhead is the dominant cost (~5-15ms per ``git`` call).
A single ``commit`` invokes git twice (add + commit) so a turn-end
commit lands in ~30-50ms. Acceptable given a typical turn is multi-
second.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from openprogram._compat import filesystem_path


# Errors


class GitSessionError(RuntimeError):
    """Raised when a git command fails or repo state is unexpected."""


# Atomic file replace


def _retry_windows_file_access(operation):
    """Retry the brief sharing violations around a Windows file replace."""
    deadline = time.monotonic() + 1.0
    delay = 0.002
    while True:
        try:
            return operation()
        except OSError as exc:
            retryable = os.name == "nt" and (
                isinstance(exc, PermissionError)
                or getattr(exc, "winerror", None) in {5, 32, 33}
            )
            if not retryable or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.05)


def read_text_with_retry(fpath: Path) -> str:
    """Read UTF-8 text through Windows' transient replace window."""
    native_path = filesystem_path(fpath)
    return _retry_windows_file_access(
        lambda: Path(native_path).read_text(encoding="utf-8")
    )


def atomic_write_text(fpath: Path, text: str) -> Path:
    """Write ``text`` to ``fpath`` via a private temp file + rename.

    The temp file carries a random suffix because the destination is
    NOT single-writer: ``meta.json`` is rewritten by every branch-meta
    write, and the Stage-2 auto-namer runs on a background thread while
    the job runner archives the same branch. A shared ``<name>.tmp``
    let those two threads write the same staging file at once — the
    rename then published a file holding both writers' bytes (invalid
    JSON, so the next rebuild read an empty meta and lost the session's
    title / head / branches), and the loser of the rename race raised
    FileNotFoundError. ``os.replace`` is atomic per file, so a private
    temp per write makes last-writer-wins the only outcome.

    The parent directory is fsynced after the rename, not just the file.
    A rename is a directory change, so fsyncing only the file's contents
    leaves the rename itself unflushed: a power loss then reverts the
    destination to its previous bytes even though the write "succeeded".
    """
    # Profile state is owner-only on POSIX; Windows retains inherited ACLs.
    # Apply the mode at creation so even the unpublished bytes remain private.
    tmp = fpath.with_name(f"{fpath.name}.{uuid.uuid4().hex}.tmp")
    native_tmp = filesystem_path(tmp)
    native_target = filesystem_path(fpath)
    created = False
    try:
        descriptor = os.open(
            native_tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        created = True
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _retry_windows_file_access(
            lambda: os.replace(native_tmp, native_target)
        )
        _fsync_directory(fpath.parent)
    finally:
        # No-op after a successful replace; cleans up when write_text
        # or replace raised so a failed write leaves no litter.
        if created:
            try:
                os.unlink(native_tmp)
            except FileNotFoundError:
                pass
    return fpath


def _fsync_directory(directory: Path) -> None:
    """Flush a directory entry, where the platform has such a thing.

    Windows has no directory descriptor to sync and raises trying; that
    is not a failure of the write, which has already landed.
    """
    try:
        descriptor = os.open(filesystem_path(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


# GitSession


@dataclass
class CommitInfo:
    """Result of `log()`. Fields chosen to match what the UI timeline
    cares about: short sha + message + author date."""
    sha: str
    short_sha: str
    message: str
    timestamp: float   # unix seconds


class GitSession:
    """Per-session git repo wrapper.

    Construction is cheap — does **not** touch disk until a write
    happens. ``_ensure_init`` runs on the first write to create
    ``.git`` lazily. This matches how sessions get created in the
    dispatcher: cheap object first, real disk activity only when a
    message gets appended.
    """

    def __init__(self, repo_path: str | Path):
        self.path = Path(repo_path).expanduser()
        self._lock = threading.Lock()
        self._initialized: Optional[bool] = None
        # Disk fingerprint as of the last moment THIS process knew memory
        # and disk agreed (rebuild or own write). @agentic_function runs
        # execute in a fork()'d subprocess that appends history and moves
        # HEAD on disk directly — the parent's cached SessionMemoryIndex
        # can't see that. SessionStore._open compares this against
        # stat_fingerprint() and rebuilds the index when another process
        # wrote. None = never synced.
        self._synced_fingerprint: Optional[tuple[int, int, int]] = None

    # Lifecycle

    @property
    def workdir_path(self) -> Path:
        """Per-session scratch workspace tracked by the session repo.

        Agents that don't target a user-supplied ``work_dir=...`` can
        use this as their cwd so file edits land inside the session
        and get committed alongside history / context on turn-end via
        ``commit_all``'s ``git add -A``. Lives at
        ``<repo>/workdir/``. Materialized on first ``_ensure_init``.
        """
        return self.path / "workdir"

    def exists(self) -> bool:
        """True iff the on-disk repo is already initialized."""
        if self._initialized is True:
            return True
        ok = (self.path / ".git").exists()
        if ok:
            self._initialized = True
        return ok

    def _ensure_init(self) -> None:
        """Create the repo on first write. ``git init`` + initial empty
        commit + per-repo user config (avoids relying on the user's
        global git identity, which may be missing on fresh boxes).
        """
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self.path.mkdir(parents=True, exist_ok=True)
            (self.path / "history").mkdir(exist_ok=True)
            (self.path / "context").mkdir(exist_ok=True)
            wd = self.path / "workdir"
            wd.mkdir(exist_ok=True)
            keep = wd / ".gitkeep"
            if not keep.exists():
                keep.write_text("", encoding="utf-8")
            if not (self.path / ".git").exists():
                self._run("init", "--quiet", "--initial-branch=main")
                # Local identity — no fallback to ~/.gitconfig.
                self._run("config", "user.email", "openprogram@local")
                self._run("config", "user.name", "OpenProgram")
                # Initial empty commit gives us a HEAD so subsequent
                # `git log` calls don't fail when the session has no
                # turns committed yet.
                self._run("commit", "--allow-empty", "-m", "session init",
                          "--quiet")
            self._initialized = True

    # Cross-process freshness

    def stat_fingerprint(self) -> tuple[int, int, int]:
        """Cheap disk-state fingerprint: (history-dir mtime_ns,
        meta.json mtime_ns, meta.json size). Two stat calls — no
        readdir. A history append (rename into the dir) bumps the dir
        mtime; a head/meta change rewrites meta.json."""
        try:
            hist = (self.path / "history").stat().st_mtime_ns
        except OSError:
            hist = 0
        try:
            st = (self.path / "meta.json").stat()
            meta_mtime, meta_size = st.st_mtime_ns, st.st_size
        except OSError:
            meta_mtime, meta_size = 0, 0
        return (hist, meta_mtime, meta_size)

    def mark_synced(self) -> None:
        """Record that in-memory state matches disk right now."""
        self._synced_fingerprint = self.stat_fingerprint()

    def stale(self) -> bool:
        """True when another process changed this session on disk since
        we last synced (never-synced counts as stale)."""
        return self._synced_fingerprint != self.stat_fingerprint()

    # File ops

    def write_history(self, seq: int, role: str, node_id: str, payload: dict) -> Path:
        """Append a node to ``history/``. Filename encodes seq + role
        + node id so directory listing matches the chronological order.

        Returns the resulting Path. Caller decides when to commit.
        """
        self._ensure_init()
        role_letter = (role or "x")[0]
        fname = f"{seq:04d}-{role_letter}-{node_id}.json"
        hdir = self.path / "history"
        # ``_ensure_init`` only creates history/ when it first inits the
        # repo. If the repo already exists but the subdir is missing
        # (external deletion, an interrupted run, a partially-removed
        # tree), re-create it so the write below can't FileNotFoundError.
        hdir.mkdir(parents=True, exist_ok=True)
        fpath = hdir / fname
        # Write to a private tmp then rename, so a reader never sees a
        # half-written node.
        atomic_write_text(
            fpath, json.dumps(payload, ensure_ascii=False, default=str))
        self.mark_synced()
        return fpath

    def write_meta(self, meta: dict) -> Path:
        """Overwrite ``meta.json`` at repo root. Stores session-level
        fields (title, agent_id, head_id, created_at, ...).
        """
        self._ensure_init()
        fpath = self.path / "meta.json"
        atomic_write_text(
            fpath, json.dumps(meta, ensure_ascii=False, default=str))
        self.mark_synced()
        return fpath

    def read_meta(self) -> dict:
        fpath = self.path / "meta.json"
        if not fpath.exists():
            return {}
        try:
            return json.loads(read_text_with_retry(fpath))
        except (json.JSONDecodeError, OSError):
            return {}

    def list_history(self) -> list[Path]:
        """Return ``history/*.json`` sorted by filename (== seq order)."""
        d = self.path / "history"
        if not d.exists():
            return []
        return sorted(d.glob("*.json"))

    def read_context_file(self, name: str) -> Optional[Any]:
        fpath = self.path / "context" / name
        if not fpath.exists():
            return None
        try:
            return json.loads(read_text_with_retry(fpath))
        except (json.JSONDecodeError, OSError):
            return None

    # Git ops

    def commit_all(self, message: str) -> Optional[str]:
        """``git add -A && git commit -m <message>``. Returns commit sha
        or ``None`` if nothing was staged (empty diff).
        """
        if not self.exists():
            return None
        with self._lock:
            self._run("add", "-A")
            # Check if anything is staged — avoid creating empty commits.
            staged = self._run("diff", "--cached", "--name-only").strip()
            if not staged:
                return None
            self._run("commit", "-m", message, "--quiet")
            return self._run("rev-parse", "HEAD").strip()

    def log(self, limit: int = 100) -> list[CommitInfo]:
        """Recent commits, newest first. UI timeline reads this."""
        if not self.exists():
            return []
        out = self._run(
            "log", f"--max-count={limit}",
            "--pretty=format:%H\t%h\t%at\t%s",
        )
        commits: list[CommitInfo] = []
        for line in out.splitlines():
            parts = line.split("\t", 3)
            if len(parts) != 4:
                continue
            try:
                ts = float(parts[2])
            except ValueError:
                ts = 0.0
            commits.append(CommitInfo(
                sha=parts[0], short_sha=parts[1],
                timestamp=ts, message=parts[3],
            ))
        return commits

    def current_branch(self) -> str:
        if not self.exists():
            return "main"
        return self._run("rev-parse", "--abbrev-ref", "HEAD").strip()

    def list_branches(self) -> list[str]:
        if not self.exists():
            return []
        out = self._run("for-each-ref", "--format=%(refname:short)",
                        "refs/heads/")
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    def checkout(self, ref: str, create_branch: Optional[str] = None) -> None:
        """``git checkout <ref>`` optionally creating a new branch."""
        if not self.exists():
            raise GitSessionError(f"repo not initialized at {self.path}")
        with self._lock:
            if create_branch:
                self._run("checkout", "-b", create_branch, ref, "--quiet")
            else:
                self._run("checkout", ref, "--quiet")

    # Internal: subprocess plumbing

    def _run(self, *args: str, check: bool = True, timeout: float = 30.0) -> str:
        """Run ``git <args...>`` in the repo. Returns stdout text.

        Raises ``GitSessionError`` on non-zero exit. We pass ``-C path``
        instead of cwd= so we don't depend on a stable cwd.
        """
        cmd = ["git", "-C", str(self.path), *args]
        try:
            cp = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                # Force UTF-8 decode of git's output. Without this,
                # ``text=True`` decodes with the locale codec (cp1252 /
                # gbk on Windows), which crashes the subprocess reader
                # thread on any non-Latin-1 byte — a CJK commit message,
                # filename, author, or even an em-dash — leaving
                # ``stdout=None`` so callers like ``log()`` then blow up
                # on ``.splitlines()``. git speaks UTF-8 regardless of
                # platform locale. Mirrors ``project_store._run``.
                encoding="utf-8", errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise GitSessionError(f"git {' '.join(args)} failed: {e}") from e
        if check and cp.returncode != 0:
            raise GitSessionError(
                f"git {' '.join(args)} exited {cp.returncode}: "
                f"{cp.stderr.strip() or cp.stdout.strip()}"
            )
        return cp.stdout

    # Cleanup

    def destroy(self) -> None:
        """Delete the entire repo. Used by ``delete_session``."""
        with self._lock:
            if self.path.exists():
                shutil.rmtree(self.path, ignore_errors=True)
            self._initialized = None
