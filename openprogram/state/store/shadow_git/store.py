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

# The shadow repository holds exact copies of project files and is read back
# by this module alone, so it never follows the user's Git configuration:
# Git for Windows ships ``core.autocrlf=true``, which would rewrite line
# endings on the way in; ``core.quotepath`` turns a non-ASCII file name into
# an octal escape in every listing; a global ``commit.gpgsign`` would ask for
# a key on each turn; and a mirrored path can pass 260 characters.
_GIT_CONFIG = (
    "-c", "core.autocrlf=false",
    "-c", "core.safecrlf=false",
    "-c", "core.quotepath=false",
    "-c", "core.longpaths=true",
    "-c", "commit.gpgsign=false",
)


def _mirror(src: str, dst: Path) -> None:
    """Copy ``src`` into the shadow work tree at ``dst``.

    ``shutil.copy`` (content + mode), never ``copy2``: copy2 also
    replays the SOURCE's mtime onto the shadow file, and git's index
    caches (size, mtime) with **one-second** resolution. A same-size
    edit whose replayed mtime falls in a second git already recorded as
    clean is invisible to ``git add`` — the turn then stages nothing and
    ``commit_turn`` returns None, so the whole turn's diff silently
    disappears from the file-review UI. Letting the copy carry the
    current time keeps the shadow file's mtime at or after the index's
    own, which is exactly the condition that makes git re-hash instead
    of trusting the stat cache.
    """
    shutil.copy(src, str(dst))


def _shadow_root() -> Path:
    from openprogram.state.paths import get_state_dir
    return Path(get_state_dir()) / "shadow-git"


def _repo_dir_for(project_path: str) -> Path:
    norm = os.path.normpath(os.path.expanduser(project_path))
    h = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
    return _shadow_root() / h


#: Inside a shadow repository's ``.git``: one empty file per conversation
#: that committed into it, named by the conversation id. Made with the
#: repository, so every conversation that ever wrote into it is listed.
OWNERS_DIR = "openprogram-sessions"


def _owners(repo: Path) -> Optional[set[str]]:
    """The conversations listed in ``repo``, or None when it keeps no list."""
    try:
        return set(os.listdir(repo / ".git" / OWNERS_DIR))
    except OSError:
        return None


#: A repository on its way out is renamed to this suffix first, so its path
#: is free at once for a conversation that starts a new history there.
_REMOVED_SUFFIX = ".removed"


def _release(repo: Path, gone: set[str]) -> bool:
    """Drop ``gone`` from ``repo``'s list; remove the repository when the
    list empties. True when the repository was removed.

    Removing the emptied list directory is what claims the repository: it
    fails while any conversation is listed, so one that lists itself at the
    same moment (:meth:`ShadowGitStore.note_session`) either keeps the
    repository or finds the list gone and starts a new one.
    """
    owners = _owners(repo)
    if owners is None or not owners & gone:
        return False
    for session_id in owners & gone:
        try:
            os.unlink(repo / ".git" / OWNERS_DIR / session_id)
        except OSError:
            return False
    try:
        os.rmdir(repo / ".git" / OWNERS_DIR)
    except OSError:
        return False
    return _remove_repo(repo)


def _remove_repo(repo: Path) -> bool:
    removed = repo.with_name(repo.name + _REMOVED_SUFFIX)
    shutil.rmtree(removed, ignore_errors=True)
    try:
        os.rename(repo, removed)
    except OSError:
        return False
    shutil.rmtree(removed, ignore_errors=True)
    return True


def release_session(session_id: str) -> int:
    """A conversation was deleted: its file history goes with it.

    A shadow repository belongs to a directory and is shared by every
    conversation that edited files there, and one conversation's commits
    cannot be cut out of a history the others' diffs are read from. So the
    repository is removed when the last conversation listed in it is
    deleted. Returns how many repositories were removed.
    """
    return sweep_released(lambda owner: owner == session_id)


def sweep_released(is_gone, root: Optional[Path] = None) -> int:
    """Remove every shadow repository all of whose conversations ``is_gone``
    (a delete that stopped before :func:`release_session`). ``root`` is the
    shadow folder, this state directory's when omitted. Returns how many
    were removed."""
    try:
        repos = [entry for entry in (root or _shadow_root()).iterdir() if entry.is_dir()]
    except OSError:
        return 0
    removed = 0
    for repo in repos:
        if repo.name.endswith(_REMOVED_SUFFIX):
            # A removal that stopped after its rename.
            shutil.rmtree(repo, ignore_errors=True)
            continue
        gone = {owner for owner in (_owners(repo) or ()) if is_gone(owner)}
        if gone and _release(repo, gone):
            removed += 1
    return removed


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
                    (self.repo_path / ".git" / OWNERS_DIR).mkdir(exist_ok=True)
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

    def note_session(self, session_id: str) -> None:
        """List ``session_id`` among the conversations that write here, so
        the repository outlives it only while another one is listed
        (:func:`release_session`). Called before the conversation's commit."""
        if (not session_id or session_id in {".", ".."} or "/" in session_id
                or "\\" in session_id or not self._ensure_init()):
            return
        owner = self.repo_path / ".git" / OWNERS_DIR / session_id
        try:
            owner.touch()
            return
        except OSError:
            pass
        if (self.repo_path / ".git").exists():
            # A repository made without the list keeps none; it stays.
            logger.debug("shadow-git owner not recorded for %s", session_id)
            return
        # The last conversation listed here was deleted just now and the
        # repository went with it: this one starts the next.
        self._initialized = False
        if self._ensure_init():
            try:
                owner.touch()
            except OSError:
                logger.debug("shadow-git owner not recorded for %s", session_id, exc_info=True)

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
                _mirror(str(src), shadow_path)
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

    def seed_baseline(self, items: "list[tuple[str, str]]") -> bool:
        """Commit pre-turn images of files the shadow has never seen.

        ``items`` is ``[(abs_path_in_project, backup_src)]`` — the
        checkpoint's pre-command copies. Files already present in the
        shadow tree are skipped. Seeding BEFORE the turn commit is what
        lets a first-ever turn's edit diff as a modify (and a bash mv
        pair into a rename) instead of a bare empty-tree add.

        Returns True when a baseline commit was made.
        """
        if not self._ensure_init():
            return False
        try:
            staged = 0
            for abs_path, backup_src in items:
                rel = _safe_rel(Path(abs_path), self.project_path)
                if rel is None:
                    continue
                shadow_path = self.repo_path / rel
                if shadow_path.exists() or not Path(backup_src).is_file():
                    continue
                shadow_path.parent.mkdir(parents=True, exist_ok=True)
                _mirror(backup_src, shadow_path)
                self._git("add", str(rel))
                staged += 1
            if staged == 0:
                return False
            if not self._git("diff", "--cached", "--name-only").strip():
                return False
            self._git("commit", "-m", "[baseline] pre-turn images", "--quiet")
            return True
        except Exception:
            logger.debug("shadow-git baseline seed failed", exc_info=True)
            self._git("reset", "--mixed", "HEAD", check=False)
            return False

    def head_sha(self) -> Optional[str]:
        """Current HEAD sha, or None if the repo has no commits / failed."""
        if not self._ensure_init():
            return None
        try:
            return self._git("rev-parse", "HEAD").strip() or None
        except Exception:
            return None

    def diff(self, sha1: str, sha2: str = "HEAD", path: str = "",
             extra_paths: "list[str] | None" = None) -> str:
        """Diff between two commits, optionally limited to one path.

        ``path`` is relative to the project root. ``extra_paths`` adds
        more pathspecs — a rename needs BOTH sides in the filter for
        ``-M`` to pair them into one rename diff.
        """
        if not self._ensure_init():
            return ""
        args = ["diff", "-M", sha1, sha2]
        paths = [p for p in ([path] + list(extra_paths or [])) if p]
        if paths:
            args += ["--", *paths]
        try:
            return self._git(*args)
        except Exception:
            return ""

    def name_status(self, sha1: str, sha2: str = "HEAD") -> list[dict]:
        """``[{"status", "rel", "old_rel"}]`` between two commits, with
        rename detection: status is one of ``A``/``D``/``M``/``R``
        (copies map to A); ``old_rel`` is set only for renames."""
        if not self._ensure_init():
            return []
        try:
            out = self._git("diff", "--name-status", "-M", sha1, sha2)
        except Exception:
            return []
        rows: list[dict] = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            code = parts[0][:1]
            if code == "R" and len(parts) >= 3:
                rows.append({"status": "R", "rel": parts[2], "old_rel": parts[1]})
            elif code == "C" and len(parts) >= 3:
                rows.append({"status": "A", "rel": parts[2], "old_rel": ""})
            elif code in ("A", "D", "M"):
                rows.append({"status": code, "rel": parts[1], "old_rel": ""})
        return rows

    def numstat(self, sha1: str, sha2: str = "HEAD") -> dict[str, tuple[int, int]]:
        """``{rel_path: (added, removed)}`` between two commits.

        Binary files report ``-``/``-`` in numstat; they map to (0, 0).
        """
        if not self._ensure_init():
            return {}
        try:
            out = self._git("diff", "--numstat", "-M", sha1, sha2)
        except Exception:
            return {}
        result: dict[str, tuple[int, int]] = {}
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            add, rem, rel = parts
            # -M prints renames as "old => new" or "dir/{old => new}";
            # key the stats by the NEW path.
            if " => " in rel:
                if "{" in rel and "}" in rel:
                    head, _, rest = rel.partition("{")
                    inner, _, tail = rest.partition("}")
                    rel = head + inner.split(" => ")[-1] + tail
                else:
                    rel = rel.split(" => ")[-1]
            try:
                result[rel] = (int(add), int(rem))
            except ValueError:
                result[rel] = (0, 0)
        return result

    def restore_file(self, sha: str, file_path: str, dest: str) -> bool:
        """Restore a single file from a shadow commit to ``dest``.

        ``file_path`` is relative to the project root.
        Returns True on success.
        """
        if not self._ensure_init():
            return False
        try:
            # Bytes, not text: the file may be binary or in another
            # encoding, and its line endings are part of it.
            content = self._git_bytes("show", f"{sha}:{file_path}")
            dest_path = Path(dest)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(content)
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

    def _git_bytes(self, *args: str) -> bytes:
        cmd = ["git", *_GIT_CONFIG, "-C", str(self.repo_path), *args]
        cp = subprocess.run(cmd, capture_output=True, timeout=_TIMEOUT)
        if cp.returncode != 0:
            raise RuntimeError(
                f"shadow-git {' '.join(args)} → exit {cp.returncode}: "
                f"{cp.stderr.decode('utf-8', 'replace').strip()}"
            )
        return cp.stdout

    def _git(self, *args: str, check: bool = True) -> str:
        cmd = ["git", *_GIT_CONFIG, "-C", str(self.repo_path), *args]
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
