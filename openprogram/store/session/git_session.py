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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


# Errors


class GitSessionError(RuntimeError):
    """Raised when a git command fails or repo state is unexpected."""


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
        # Atomic-ish: write to tmp then rename. Avoids partial reads if
        # another thread reads the file mid-write.
        tmp = fpath.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(fpath)
        return fpath

    def write_meta(self, meta: dict) -> Path:
        """Overwrite ``meta.json`` at repo root. Stores session-level
        fields (title, agent_id, head_id, created_at, ...).
        """
        self._ensure_init()
        fpath = self.path / "meta.json"
        tmp = fpath.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(fpath)
        return fpath

    def read_meta(self) -> dict:
        fpath = self.path / "meta.json"
        if not fpath.exists():
            return {}
        try:
            return json.loads(fpath.read_text(encoding="utf-8"))
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
            return json.loads(fpath.read_text(encoding="utf-8"))
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
