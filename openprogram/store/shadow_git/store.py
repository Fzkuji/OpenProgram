"""Shadow git store — independent git history for agent file changes.

Records every turn's file modifications in a standalone git repo at
``~/.openprogram/shadow-git/<project-hash>/``, completely separate from
the user's ``.git``.  The user's git history stays clean; this store
provides diff, log, and single-file restore without any coupling to the
user's workflow.

Inspired by Hermes's shadow checkpoint store.  All operations are
best-effort: failures log warnings but never break the main agent loop.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_TIMEOUT = 5.0


def _shadow_root() -> Path:
    from openprogram.paths import get_state_dir
    return Path(get_state_dir()) / "shadow-git"


def _repo_dir_for(project_path: str) -> Path:
    norm = os.path.normpath(os.path.expanduser(project_path))
    h = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
    return _shadow_root() / h


class ShadowGitStore:
    """Independent git store that mirrors agent file changes.

    One instance per project directory.  The shadow repo lives at
    ``~/.openprogram/shadow-git/<hash>/`` and is never visible to the
    user's git tooling.
    """

    AGENT_NAME = "OpenProgram Agent"
    AGENT_EMAIL = "agent@openprogram.local"

    def __init__(self, project_path: str):
        self.project_path = Path(project_path).expanduser().resolve()
        self.repo_path = _repo_dir_for(str(self.project_path))
        self._lock = threading.Lock()
        self._initialized = False

    def _ensure_init(self) -> bool:
        if self._initialized:
            return True
        with self._lock:
            if self._initialized:
                return True
            try:
                self.repo_path.mkdir(parents=True, exist_ok=True)
                if not (self.repo_path / ".git").exists():
                    self._git("init", "--quiet", "--initial-branch=main")
                    self._git("config", "user.name", self.AGENT_NAME)
                    self._git("config", "user.email", self.AGENT_EMAIL)
                    self._git("commit", "--allow-empty", "-m",
                              "shadow-git: init")
                self._initialized = True
                return True
            except Exception:
                logger.debug("shadow-git init failed for %s",
                             self.project_path, exc_info=True)
                return False

    def commit_turn(
        self,
        turn_id: str,
        changed_files: list[str],
        message: str = "",
    ) -> Optional[str]:
        """Copy changed files into the shadow repo and commit.

        ``changed_files`` are absolute paths in the real project.
        Returns the commit sha, or None if nothing changed / error.
        """
        if not self._ensure_init():
            return None
        if not changed_files:
            return None
        try:
            copied = 0
            for abs_path in changed_files:
                src = Path(abs_path)
                if not src.is_file():
                    rel = _safe_rel(src, self.project_path)
                    if rel is None:
                        continue
                    shadow_path = self.repo_path / rel
                    if shadow_path.exists():
                        shadow_path.unlink()
                        self._git("add", str(rel))
                        copied += 1
                    continue
                rel = _safe_rel(src, self.project_path)
                if rel is None:
                    continue
                shadow_path = self.repo_path / rel
                shadow_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(shadow_path))
                self._git("add", str(rel))
                copied += 1

            if copied == 0:
                return None

            staged = self._git("diff", "--cached", "--name-only").strip()
            if not staged:
                return None

            label = message or f"turn {turn_id[:8]}"
            self._git("commit", "-m", f"[{turn_id[:8]}] {label}", "--quiet")
            sha = self._git("rev-parse", "HEAD").strip()
            logger.debug("shadow-git commit %s for turn %s", sha[:8], turn_id[:8])
            return sha
        except Exception:
            logger.debug("shadow-git commit failed for turn %s",
                         turn_id, exc_info=True)
            self._git("reset", "--mixed", "HEAD", check=False)
            return None

    def diff(self, sha1: str, sha2: str = "HEAD") -> str:
        """Diff between two commits in the shadow repo."""
        if not self._ensure_init():
            return ""
        try:
            return self._git("diff", sha1, sha2)
        except Exception:
            return ""

    def restore_file(self, sha: str, file_path: str, dest: str) -> bool:
        """Restore a single file from a shadow commit to ``dest``.

        ``file_path`` is relative to the project root.
        Returns True on success.
        """
        if not self._ensure_init():
            return False
        try:
            content = self._git("show", f"{sha}:{file_path}")
            dest_path = Path(dest)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_text(content, encoding="utf-8")
            return True
        except Exception:
            logger.debug("shadow-git restore failed: %s@%s → %s",
                         file_path, sha[:8], dest, exc_info=True)
            return False

    def log(self, n: int = 10) -> list[dict]:
        """Recent commits: ``[{sha, short_sha, timestamp, message}]``."""
        if not self._ensure_init():
            return []
        try:
            out = self._git(
                "log", f"--max-count={n}",
                "--pretty=format:%H\t%h\t%at\t%s",
            )
            result: list[dict] = []
            for line in out.splitlines():
                parts = line.split("\t", 3)
                if len(parts) != 4:
                    continue
                try:
                    ts = float(parts[2])
                except ValueError:
                    ts = 0.0
                result.append({
                    "sha": parts[0],
                    "short_sha": parts[1],
                    "timestamp": ts,
                    "message": parts[3],
                })
            return result
        except Exception:
            return []

    def _git(self, *args: str, check: bool = True) -> str:
        cmd = ["git", "-C", str(self.repo_path), *args]
        try:
            cp = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=_TIMEOUT, encoding="utf-8", errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            if check:
                raise
            logger.debug("shadow-git command failed: %s", e)
            return ""
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"shadow-git {' '.join(args)} → exit {cp.returncode}: "
                f"{(cp.stderr or '').strip()}"
            )
        return cp.stdout


def _safe_rel(path: Path, base: Path) -> Optional[str]:
    """Relative path of ``path`` under ``base``, or None if outside."""
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return None
