"""Project entity memory — the second half of the entity layer.

The entity (实体) tier of OpenProgram memory has two kinds of
stores (see ``docs/design/memory/overview.md`` §2):

  * **Session log** (``state/store/session``) — one append-only log per
    conversation.
  * **Project-Git** (this module) — one repo per *project*, where a
    project is a long-lived working unit bound to a filesystem
    directory. Agent file edits in that directory get committed here.

Every session belongs to exactly one project. Two cases:

  * **Bound project** — the user worked in a real directory (their
    code / docs repo). That directory IS the project git repo; we
    reuse its ``.git`` if it has one, else ``git init`` it.
  * **home project** (id ``default``) — the catch-all for ad-hoc
    chats that never named a directory. It has no git repo; its folder
    ``<state>/projects/default/`` holds those chats and the personal
    memory, so there is never a session with no project home.

A bound project's git repo is created on first use. Every
project's chats, memory and settings live in ``<state>/projects/<id>/``
(see :mod:`.layout`).

Registry: ``<state>/projects/projects.json`` maps ``project_id`` →
metadata (name, path, sessions, status). The registry is the index;
the git repos are the truth.

Cross-platform: all git calls go through ``git -C <path> ...`` via
subprocess (no cwd dependence).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from openprogram.host._platform.filesystem import replace_file


DEFAULT_PROJECT_ID = "default"


_log = logging.getLogger(__name__)


class ProjectStoreError(RuntimeError):
    """Raised on unrecoverable git / registry failure."""


# paths


def projects_dir() -> Path:
    """``<state>/projects/`` — holds the registry + the default repo."""
    from openprogram.state.paths import get_state_dir
    d = Path(get_state_dir()) / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _registry_path() -> Path:
    return projects_dir() / "projects.json"


# Project record


@dataclass
class Project:
    """A long-lived working unit. See module docstring.

    ``path`` is the absolute path to the git repo backing this project:
    the default project's home-dir repo, or the user's real working
    directory. ``session_ids`` is a reverse index (which conversations
    happened in this project).
    """
    id: str
    name: str
    path: str
    is_default: bool = False
    session_ids: list[str] = field(default_factory=list)
    status: str = "active"          # active | paused | done
    created_at: float = field(default_factory=time.time)
    icon: str = ""
    hidden: bool = False
    custom_name: bool = False
    description: str = ""
    source_folders: list[str] = field(default_factory=list)
    directory_identity: str = ""
    native_bookmark: str = ""
    volume_id: str = ""
    location_revision: int = 0
    location_state: str = "available"
    migration_error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        return cls(
            id=d["id"],
            name=d.get("name", d["id"]),
            path=d.get("path", ""),
            is_default=bool(d.get("is_default", False)),
            session_ids=list(d.get("session_ids", []) or []),
            status=d.get("status", "active"),
            hidden=bool(d.get("hidden", False)),
            created_at=float(d.get("created_at", time.time())),
            icon=d.get("icon", ""),
            custom_name=bool(d.get("custom_name", False)),
            description=d.get("description", ""),
            source_folders=list(d.get("source_folders", []) or []),
            directory_identity=d.get("directory_identity", ""),
            native_bookmark=d.get("native_bookmark", ""),
            volume_id=d.get("volume_id", ""),
            location_revision=int(d.get("location_revision") or 0),
            location_state=d.get("location_state", "available") or "available",
            migration_error=d.get("migration_error", "") or "",
        )


# ProjectGit: the git wrapper for a project's working directory


class ProjectGit:
    """Wraps the git repo backing a project's working directory.

    Lazy: construction is cheap, disk work happens in
    :meth:`ensure_init`. A project repo is often a *pre-existing user repo* we must NOT
    clobber. So :meth:`ensure_init` only ``git init``s when there is no
    ``.git`` yet, and never rewrites the user's git identity — instead
    agent commits carry an explicit author/committer so they're
    distinguishable from the user's own commits.
    """

    AGENT_NAME = "OpenProgram Agent"
    AGENT_EMAIL = "agent@openprogram.local"

    def __init__(self, repo_path: str | Path):
        self.path = Path(repo_path).expanduser()
        self._lock = threading.Lock()
        self._initialized: Optional[bool] = None

    # -- lifecycle --

    def is_git_repo(self) -> bool:
        return (self.path / ".git").exists()

    def exists(self) -> bool:
        if self._initialized is True:
            return True
        ok = self.is_git_repo()
        if ok:
            self._initialized = True
        return ok

    def ensure_init(self, *, allow_init: bool = True) -> bool:
        """Make sure the directory is a git repo.

        Returns True if the repo is ready (already was, or we just
        created it), False if it isn't a repo and ``allow_init`` is
        False.

        ``allow_init=False`` is for the "bind to a user directory but
        don't touch it unless it's already version-controlled" mode —
        not used yet, kept for the UI flow that asks before init'ing a
        user's folder.
        """
        if self._initialized:
            return True
        with self._lock:
            if self._initialized:
                return True
            self.path.mkdir(parents=True, exist_ok=True)
            if not self.is_git_repo():
                if not allow_init:
                    return False
                self._run("init", "--quiet", "--initial-branch=main")
                # Only stamp a local identity if the repo has none —
                # never override a user's existing global/local config.
                if not self._has_user_identity():
                    self._run("config", "user.email", self.AGENT_EMAIL)
                    self._run("config", "user.name", self.AGENT_NAME)
                # Seed a HEAD so `git log` works before the first real
                # commit. Empty so we don't add files to a user dir.
                self._run("commit", "--allow-empty", "-m",
                          "openprogram: project init", "--quiet")
            # Make OpenProgram's own footprint invisible to the user's
            # git. A project-bound session lives at
            # ``<project>/.openprogram/sessions/<id>/`` — i.e. INSIDE the
            # user's working tree — so without this it shows up as an
            # untracked dir in ``git status`` and, worse, gets swept into
            # commits by ``git add -A``. A ``.gitignore`` that ignores
            # the whole ``.openprogram/`` subtree keeps it out of the
            # user's repo entirely. Idempotent + applied to pre-existing
            # repos too (not only freshly-init'd ones).
            self._ensure_self_ignored()
            self._initialized = True
            return True

    def _ensure_self_ignored(self) -> None:
        """Hide OpenProgram's footprint from the user's git. Delegates to
        the standalone :func:`ensure_footprint_ignored` so the same logic
        runs whether or not we ever git-init this folder."""
        ensure_footprint_ignored(self.path)

    def _has_user_identity(self) -> bool:
        name = self._run("config", "user.name", check=False).strip()
        email = self._run("config", "user.email", check=False).strip()
        return bool(name and email)

    # Directories whose presence in a NON-git folder makes auto-init
    # dangerous: ``git add -A`` would sweep gigabytes of deps/build
    # output into the very first commit. If any is present we refuse to
    # auto-init and let the user decide (they can git init + add a
    # .gitignore themselves).
    _AUTOINIT_BLOCKERS = (
        "node_modules", ".venv", "venv", "env", "__pycache__",
        "target", "build", "dist", ".next", ".gradle", "vendor",
    )

    def autoinit_blocker(self) -> Optional[str]:
        """The first heavy dir (node_modules/.venv/…) present that would
        block auto-init, or None if nothing blocks. Lets the caller name
        the offender in a warning without re-running init."""
        for blocker in self._AUTOINIT_BLOCKERS:
            if (self.path / blocker).exists():
                return blocker
        return None

    def auto_init_for_agent(self) -> str:
        """Initialise a NON-git user folder so agent edits can be
        committed — safely. Returns one of:

          * ``"ready"``        — repo now exists (we just created it, with
                                 a baseline commit of the user's existing
                                 files so the agent's later commits are
                                 clean diffs, not bulk "add everything").
          * ``"already"``      — was already a git repo, nothing to do.
          * ``"blocked:<dir>"``— a heavy dir (node_modules/.venv/…) is
                                 present; refused, caller should warn.

        Rule A is now "auto-init ON": binding a real folder + the agent
        editing it will turn the folder into a git repo. The two safety
        rails: (1) skip if a dep/build dir would bloat the first commit;
        (2) commit the user's PRE-EXISTING files as a baseline FIRST, so
        every subsequent agent commit is an honest diff attributable to
        the agent — never a giant "agent added your whole project".
        """
        if self.is_git_repo():
            return "already"
        with self._lock:
            if self.is_git_repo():
                return "already"
            self.path.mkdir(parents=True, exist_ok=True)
            for blocker in self._AUTOINIT_BLOCKERS:
                if (self.path / blocker).exists():
                    return f"blocked:{blocker}"
            self._run("init", "--quiet", "--initial-branch=main")
            if not self._has_user_identity():
                self._run("config", "user.email", self.AGENT_EMAIL)
                self._run("config", "user.name", self.AGENT_NAME)
            # Make sure our own footprint is ignored BEFORE the baseline
            # add, so .openprogram/ never enters the user's history.
            ensure_footprint_ignored(self.path)
            # Baseline commit of whatever the user already had. Authored
            # as the user (it's their pre-existing code), not the agent.
            self._run("add", "-A")
            staged = self._run("diff", "--cached", "--name-only").strip()
            if staged:
                self._run("commit", "-m",
                          "openprogram: baseline (pre-existing files)",
                          "--quiet")
            else:
                # Empty folder — seed a HEAD so later commits have a parent.
                self._run("commit", "--allow-empty", "-m",
                          "openprogram: project init", "--quiet")
            self._initialized = True
            return "ready"

    # -- agent commits (Strategy A: don't pollute a dirty user tree) --

    # Sentinel return: the tree had pre-existing user changes, so we
    # refused to commit (vs. ``None`` = nothing to commit at all).
    SKIPPED_DIRTY = "skipped-dirty-tree"

    def commit_agent_changes(
        self, message: str, *, baseline: Optional[set[str]] = None
    ) -> Optional[str]:
        """Commit the agent's file edits — but only if the working tree
        had no *user* changes we'd be sweeping up.

        Strategy A from the design doc (§2.4): we can't perfectly tell
        which lines the agent vs. the user wrote, so we use a coarse but
        safe rule — **if the tree was already dirty before the agent's
        turn, refuse to commit** rather than fold the user's
        half-finished work into an agent commit. The caller captures the
        pre-turn dirty set via :meth:`dirty_paths` and passes it as
        ``baseline``; any path already dirty then is treated as the
        user's and blocks the commit.

        Returns:
          * a commit sha          — committed the agent's changes
          * ``None``              — nothing to commit (clean tree)
          * :attr:`SKIPPED_DIRTY` — refused: pre-existing user changes

        If ``baseline`` is omitted we fall back to "is the tree dirty at
        all right now?" — i.e. treat ANY dirt as the user's. That's the
        conservative default for callers that didn't snapshot a baseline.

        The commit carries the agent identity via ``-c`` overrides so
        it's attributable even in the user's own repo with their global
        identity configured.
        """
        if not self.exists():
            return None
        with self._lock:
            current = self._dirty_paths_locked()
            if not current:
                return None  # nothing changed at all
            # Decide whether the tree carries the user's uncommitted work.
            #   * no baseline  → conservative: ANY dirt is treated as the
            #     user's, so refuse.
            #   * baseline set → the user's work is any baseline path
            #     that's STILL dirty. If none remain dirty, every change
            #     present is the agent's and is safe to commit.
            if baseline is None:
                return self.SKIPPED_DIRTY
            if current & set(baseline):
                return self.SKIPPED_DIRTY
            self._run("add", "-A")
            staged = self._run("diff", "--cached", "--name-only").strip()
            if not staged:
                return None
            self._run(
                "-c", f"user.name={self.AGENT_NAME}",
                "-c", f"user.email={self.AGENT_EMAIL}",
                "commit", "-m", message, "--quiet",
            )
            return self._run("rev-parse", "HEAD").strip()

    def revert_agent_commit(self, sha: str) -> dict:
        """Undo an agent commit, choosing the safest git operation.

        Returns ``{"action": <str>, "ok": bool, "detail": <str>}`` where
        ``action`` is one of:

          * ``"reset"``   — ``git reset --hard <sha>^``. Used ONLY when
            it's provably safe: the commit is HEAD, the tree is clean,
            and the commit hasn't been pushed. Cleanest undo — the commit
            vanishes as if it never happened.
          * ``"revert"``  — ``git revert --no-edit <sha>``. The safe
            fallback: appends an inverse commit, touching nothing else.
            Used when reset would be unsafe (commit isn't HEAD / tree
            dirty / already pushed) but the change can still be undone
            additively.
          * ``"skipped"`` — couldn't safely undo (e.g. revert hit a
            conflict). The file-snapshot restore is the caller's
            fallback; git history is left untouched. ``detail`` explains.
          * ``"absent"``  — the sha isn't in this repo (already gone, or
            never landed). No-op.

        Never raises — git failures come back as ``ok=False`` so the
        caller can surface them and fall back to the snapshot.
        """
        if not self.exists():
            return {"action": "absent", "ok": False, "detail": "not a git repo"}
        with self._lock:
            # Is the sha actually in this repo?
            check = self._run("cat-file", "-t", sha, check=False).strip()
            if check != "commit":
                return {"action": "absent", "ok": False,
                        "detail": f"commit {sha[:8]} not found"}

            head = self._run("rev-parse", "HEAD", check=False).strip()
            is_head = (head == sha) or (
                self._run("rev-parse", sha, check=False).strip() == head
            )
            tree_clean = not self._run("status", "--porcelain").strip()
            pushed = bool(
                self._run("branch", "-r", "--contains", sha, check=False).strip()
            )

            # Safe-reset path: only when this commit is the tip, nothing
            # else sits on top, the tree is clean (no user work to lose),
            # and it was never published.
            if is_head and tree_clean and not pushed:
                parent = self._run("rev-parse", f"{sha}^", check=False).strip()
                if parent and parent != f"{sha}^":
                    self._run("reset", "--hard", parent, check=False)
                    return {"action": "reset", "ok": True,
                            "detail": f"reset to {parent[:8]}"}
                # No parent (initial commit) → reset to the empty tree.
                self._run("update-ref", "-d", "HEAD", check=False)
                return {"action": "reset", "ok": True,
                        "detail": "removed initial commit"}

            # Safe-revert path: additive, never rewrites published history
            # and never touches the user's other commits.
            self._run(
                "-c", f"user.name={self.AGENT_NAME}",
                "-c", f"user.email={self.AGENT_EMAIL}",
                "revert", "--no-edit", "--no-commit", sha, check=False,
            )
            # --no-commit so we can detect conflicts before finalizing.
            conflicts = self._run("diff", "--name-only", "--diff-filter=U",
                                   check=False).strip()
            if conflicts:
                # Abort — leave the repo exactly as it was; snapshot
                # restore (caller) handles the files.
                self._run("revert", "--abort", check=False)
                self._run("reset", "--hard", "HEAD", check=False)
                return {"action": "skipped", "ok": False,
                        "detail": f"revert conflicts: {conflicts.splitlines()[:3]}"}
            self._run(
                "-c", f"user.name={self.AGENT_NAME}",
                "-c", f"user.email={self.AGENT_EMAIL}",
                "commit", "-m", f"Revert agent commit {sha[:8]}", "--quiet",
                check=False,
            )
            return {"action": "revert", "ok": True,
                    "detail": f"reverted {sha[:8]}"}

    def dirty_paths(self) -> set[str]:
        """The set of paths git reports as changed (porcelain). Caller
        snapshots this BEFORE the agent's turn and passes it back to
        :meth:`commit_agent_changes` as the ``baseline``."""
        if not self.exists():
            return set()
        with self._lock:
            return self._dirty_paths_locked()

    def _dirty_paths_locked(self) -> set[str]:
        out = self._run("status", "--porcelain")
        paths: set[str] = set()
        for line in out.splitlines():
            # porcelain v1: "XY <path>" (XY = 2 status cols, then space)
            if len(line) > 3:
                paths.add(line[3:].strip().strip('"'))
        return paths

    def is_dirty(self) -> bool:
        """True if the working tree has uncommitted changes."""
        if not self.exists():
            return False
        return bool(self._run("status", "--porcelain").strip())

    def log(self, limit: int = 100) -> list[dict]:
        """Recent commits, newest first: ``[{sha, short_sha, ts, msg}]``."""
        if not self.exists():
            return []
        out = self._run(
            "log", f"--max-count={limit}",
            "--pretty=format:%H\t%h\t%at\t%s", check=False,
        )
        commits: list[dict] = []
        for line in out.splitlines():
            parts = line.split("\t", 3)
            if len(parts) != 4:
                continue
            try:
                ts = float(parts[2])
            except ValueError:
                ts = 0.0
            commits.append({
                "sha": parts[0], "short_sha": parts[1],
                "timestamp": ts, "message": parts[3],
            })
        return commits

    # -- git plumbing --

    def _run(self, *args: str, check: bool = True, timeout: float = 30.0) -> str:
        cmd = ["git", "-C", str(self.path), *args]
        try:
            cp = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ProjectStoreError(f"git {' '.join(args)} failed: {e}") from e
        if check and cp.returncode != 0:
            raise ProjectStoreError(
                f"git {' '.join(args)} → exit {cp.returncode}: "
                f"{(cp.stderr or '').strip()}"
            )
        return cp.stdout


# Registry (projects.json)

_reg_lock = threading.RLock()
_reg_state = threading.local()
_worktree_lock = threading.Lock()


@contextmanager
def _registry_transaction():
    """Hold the registry for one read-modify-write.

    The worker, the CLI and agent subprocesses all write ``projects.json``,
    so a thread lock alone lets one process overwrite another's change. The
    outermost entry in a thread also takes the cross-process file lock;
    nested entries (``resolve_project`` calling ``_upsert``) reuse it.
    """
    with _reg_lock:
        depth = getattr(_reg_state, "depth", 0)
        if depth:
            _reg_state.depth = depth + 1
            try:
                yield
            finally:
                _reg_state.depth = depth
            return
        from openprogram.state.store.session.session_lock import registry_file_lock
        with registry_file_lock(projects_dir(), "projects-registry"):
            _reg_state.depth = 1
            try:
                yield
            finally:
                _reg_state.depth = 0


def _load_registry_file(p: Path) -> Optional[dict]:
    """Parse the registry file; ``None`` when it is not a JSON object."""
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ProjectStoreError(f"cannot read project registry {p}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    return data if isinstance(data, dict) else None


def _read_registry() -> dict[str, dict]:
    """Read the registry; never report an unreadable one as empty.

    An empty result is written back by the next change, which would erase
    every project and session binding. A read error therefore raises, and
    a file that is not a JSON object is moved aside for recovery first.
    """
    p = _registry_path()
    data = _load_registry_file(p)
    if data is not None:
        return data
    with _registry_transaction():
        # Read again under the lock: the file seen above may already have
        # been replaced by a writer, and a good registry is never set aside.
        data = _load_registry_file(p)
        if data is not None:
            return data
        kept = p.with_name(f"{p.name}.unreadable-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}")
        try:
            replace_file(p, kept)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ProjectStoreError(f"cannot set aside unreadable project registry {p}: {exc}") from exc
        else:
            _log.error("project registry %s was not a JSON object; kept it as %s", p, kept)
    return {}


# One parsed registry per published file, for lookups that change nothing.
_snapshot_lock = threading.Lock()
_snapshot: Optional[tuple[Path, tuple, int, dict]] = None
# A file changed again within this long of being read can keep the same
# size and timestamp on a file system with coarse timestamps, so a file
# that young is read again instead of trusted.
_SNAPSHOT_SETTLE_NS = 1_000_000_000


def _file_identity(stat: os.stat_result) -> tuple:
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _registry_snapshot() -> dict[str, dict]:
    """The registry as last published, for a lookup that changes nothing.

    Every writer publishes the whole file by rename, so the path always
    names one complete registry and a reader needs neither the thread lock
    nor the cross-process file lock: it sees the state before a concurrent
    change or the state after it. The parsed file is kept until a different
    file is published. The result is shared; callers must not modify it.
    """
    global _snapshot
    p = _registry_path()
    try:
        identity = _file_identity(os.stat(p))
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ProjectStoreError(f"cannot read project registry {p}: {exc}") from exc
    held = _snapshot
    if (held is not None and held[0] == p and held[1] == identity
            and identity[3] + _SNAPSHOT_SETTLE_NS < held[2]):
        return held[3]
    # The identity is taken before the read. If a writer publishes in
    # between, the newer content is kept under the older identity, which the
    # next lookup no longer finds on disk and so reads again.
    read_at = time.time_ns()
    data = _load_registry_file(p)
    if data is None:
        return _read_registry()
    with _snapshot_lock:
        _snapshot = (p, identity, read_at, data)
    return data


def _write_registry(reg: dict[str, dict]) -> None:
    """Publish the whole registry by rename so a reader never sees half of it."""
    global _snapshot
    p = _registry_path()
    fd, temporary = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(json.dumps(reg, indent=2, ensure_ascii=False, default=str))
            output.flush()
            os.fsync(output.fileno())
        replace_file(temporary, p)
    finally:
        Path(temporary).unlink(missing_ok=True)
        with _snapshot_lock:
            _snapshot = None


def _new_project_id() -> str:
    """Opaque id for a newly registered project. Existing path-derived
    ids stay on disk; this is never used to rewrite them."""
    return str(uuid.uuid4())


def ensure_footprint_ignored(project_path: str | Path) -> None:
    """Write ``<project>/.openprogram/.gitignore`` = ``*`` so the user's
    git ignores OpenProgram's entire footprint (the sessions we
    store at ``<project>/.openprogram/sessions/<id>/`` live INSIDE the
    user's working tree).

    Crucially this is **independent of git-init** (rule A): we never
    create the user's ``.git``, but we DO need the ``.gitignore`` in
    place if the folder is — or later becomes — a git repo, otherwise
    ``.openprogram/`` shows up as untracked and gets swept into commits.
    Called when a session is relocated into a project dir, and again
    from ``ProjectGit.ensure_init``. Best-effort; never raises.
    """
    try:
        op_dir = Path(project_path).expanduser() / ".openprogram"
        op_dir.mkdir(parents=True, exist_ok=True)
        gi = op_dir / ".gitignore"
        if op_dir.is_symlink() or gi.is_symlink():
            return
        current = gi.read_text(encoding="utf-8") if gi.exists() else ""
        if current != "*\n":
            gi.write_text("*\n", encoding="utf-8")
    except OSError:
        pass


def list_projects() -> list[Project]:
    return [Project.from_dict(d) for d in _registry_snapshot().values()]


def get_project(project_id: str) -> Optional[Project]:
    d = _registry_snapshot().get(project_id)
    return Project.from_dict(d) if d else None


def _upsert(project: Project, *, capture_identity: bool = False, replace_identity: bool = False) -> Project:
    """Write a project record. Identity is captured only when asked.

    Metadata edits and ordinary registry maintenance must not restamp
    folder identity onto a replacement folder at the same path.
    """
    with _registry_transaction():
        reg = _read_registry()
        if capture_identity and not project.is_default and project.path:
            from .identity import capture_directory_identity, read_marker
            marker = read_marker(project.path)
            if marker and not replace_identity and any(
                    row.get("id") != project.id and row.get("directory_identity") == marker
                    for row in reg.values()):
                raise ProjectStoreError("destination identity belongs to another project")
            try:
                captured = capture_directory_identity(project.path, replace=replace_identity)
            except (OSError, ValueError, TimeoutError) as exc:
                raise ProjectStoreError(f"cannot save project identity: {exc}") from exc
            ensure_footprint_ignored(project.path)
            project.directory_identity = captured["directory_identity"]
            project.native_bookmark = captured["native_bookmark"]
            project.volume_id = captured["volume_id"]
        reg[project.id] = project.to_dict()
        _write_registry(reg)
    return project


def set_location_state(project_id: str, state: str, error: str = "") -> Optional[Project]:
    with _registry_transaction():
        reg = _read_registry()
        row = reg.get(project_id)
        if row is None:
            return None
        row["location_state"] = state
        if error or state != "error":
            row["migration_error"] = error
        reg[project_id] = row
        _write_registry(reg)
        return Project.from_dict(row)


def update_project(project_id: str, patch: dict) -> Project:
    """Validate and atomically edit display metadata; preserve main path/bindings."""
    if not isinstance(project_id, str) or not project_id:
        raise ValueError("project_id is required")
    if not isinstance(patch, dict) or not patch or set(patch) - {"name", "icon", "description", "source_folders"}:
        raise ValueError("unsupported project fields")
    values = dict(patch)
    for key, limit in (("name", 200), ("icon", 32), ("description", 2000)):
        if key in values:
            value = values[key]
            if not isinstance(value, str) or len(value) > limit or (key == "name" and not value.strip()):
                raise ValueError(f"invalid project {key}")
            values[key] = value.strip()
    if "source_folders" in values:
        folders = values["source_folders"]
        if not isinstance(folders, list) or len(folders) > 32:
            raise ValueError("source_folders must be a list of at most 32 directories")
        normalized = []
        for value in folders:
            if not isinstance(value, str) or not Path(value).expanduser().is_absolute():
                raise ValueError("source folder paths must be absolute")
            path = Path(value).expanduser().resolve()
            if not path.is_dir():
                raise ValueError(f"source folder is not a directory: {value}")
            if str(path) not in normalized:
                normalized.append(str(path))
        values["source_folders"] = normalized
    with _registry_transaction():
        registry = _read_registry()
        if project_id not in registry:
            raise ValueError("unknown project")
        project = Project.from_dict(registry[project_id])
        for key, value in values.items():
            setattr(project, key, value)
        if "name" in values:
            project.custom_name = True
        project.source_folders = [p for p in project.source_folders if p != project.path]
        registry[project_id] = project.to_dict()
        _write_registry(registry)
        return project


# ── project-level settings ──────────────────────────────────────────────
# 项目级配置（权限规则、以后的项目级工具/模型偏好等）。
# 每个项目 (含主目录项目) 的配置落在 <state>/projects/<id>/settings.json;
# 旧版写在 <project>/.openprogram/settings.json 的仍可读.

def _settings_path_for(project: Project) -> Path:
    if project.is_default:
        return projects_dir() / project.id / "settings.json"
    owned = projects_dir() / project.id / "settings.json"
    if owned.exists():
        return owned
    if project.path:
        legacy = Path(project.path).expanduser() / ".openprogram" / "settings.json"
        if legacy.exists() and not owned.exists():
            return legacy
    return owned


def load_project_settings(project_id: str) -> dict:
    """读项目级配置 dict。项目不存在 / 无配置 → {}。"""
    proj = get_project(project_id)
    if proj is None:
        return {}
    p = _settings_path_for(proj)
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        _log.warning("project settings unreadable at %s: %s", p, e)
    return {}


def save_project_settings(project_id: str, settings: dict) -> None:
    """写项目级配置 dict（整体覆盖）。项目不存在则忽略。"""
    proj = get_project(project_id)
    if proj is None:
        return
    p = projects_dir() / proj.id / "settings.json"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8",
        )
    except (OSError, TypeError, ValueError) as e:
        _log.warning("project settings NOT saved for %s at %s: %s",
                     project_id, p, e)


def get_default_project() -> Project:
    """The home project: the catch-all for chats with no bound directory.

    Named after the home folder (``alex`` for ``/Users/alex``) unless
    the user renamed it, and listed first. Its folder
    under ``<state>/projects/default/`` holds those chats and the personal
    memory every project reads. ``path`` stays the home folder, the
    working scope of such a chat.

    It still gets **no git repo of its own** — the ``is_default`` flag
    short-circuits every project-git path (``project_commit`` no-ops on
    it), so setting ``path`` to the real home directory never ``git
    init``s the user's home. An ad-hoc conversation's entity memory is
    the session repo at ``<state>/sessions/<id>/``; a real project repo
    appears only when a session binds an actual working directory (see
    :func:`resolve_project`).
    """
    home = Path.home()
    existing = get_project(DEFAULT_PROJECT_ID)
    if existing is not None:
        if not existing.path:
            existing.path = str(home)
            _upsert(existing)
        return existing
    proj = Project(
        id=DEFAULT_PROJECT_ID,
        name=home.name,
        path=str(home),     # display/scope only — is_default guards git
        is_default=True,
    )
    return _upsert(proj)


def resolve_project(path: str | Path | None = None, *, name: str | None = None) -> Project:
    """Resolve (and register) the project for a working directory.

    New registrations use opaque UUIDs. Existing path-derived ids are
    never recomputed. Portable folder markers reconnect moved projects.
    A copy opened while the original is present receives a new identity.
    """
    if path is None:
        return get_default_project()

    p = Path(path).expanduser()
    try:
        reattach_enclosing_project(p)
    except Exception:  # noqa: BLE001 - reattaching is a courtesy, opening must not fail
        _log.debug("enclosing project lookup failed for %s", p, exc_info=True)
    # The path/token decision and publication are one registry transaction.
    # Session relocation runs after releasing it so readers remain available.
    with _registry_transaction():
        existing = next((project for project in list_projects()
                         if not project.is_default and project.path
                         and Path(project.path).expanduser().resolve() == p.resolve()), None)
        if existing is not None:
            from .identity import is_portable, captured_identity_matches
            if p.is_dir() and is_portable(existing) and not captured_identity_matches(existing, p):
                raise ProjectStoreError("project location has changed; locate the original folder or confirm a replacement")
            # A removed project made again comes back under the name it is made with.
            return _reopened(existing.id, name) if existing.hidden else existing

        from .identity import read_marker, captured_identity_matches
        marker = read_marker(p)
        owners = [project for project in list_projects()
                  if marker and not project.is_default and project.directory_identity == marker]
        if len(owners) > 1:
            raise ProjectStoreError("project identity belongs to multiple registrations; locate the intended project")
        if not (owners and not captured_identity_matches(owners[0], owners[0].path)):
            pid = _new_project_id()
            while get_project(pid) is not None:
                pid = _new_project_id()
            proj = Project(
                id=pid,
                name=name or p.name or pid,
                path=str(p.resolve()),
                is_default=False,
                location_state="available",
            )
            return _upsert(proj, capture_identity=True, replace_identity=bool(owners))
    owner = owners[0]
    moved = relocate_project(owner.id, p, expected_path=owner.path,
                             expected_revision=owner.location_revision, require_identity=True)
    return _reopened(moved.id, name) if moved.hidden else moved


def _reopened(project_id: str, name: str | None) -> Project:
    """Show a removed project again, under ``name`` when one is given."""
    with _registry_transaction():
        reg = _read_registry()
        if project_id not in reg:
            raise ValueError("unknown project")
        reg[project_id]["hidden"] = False
        if name and name.strip():
            reg[project_id]["name"] = name.strip()[:200]
        _write_registry(reg)
        return Project.from_dict(reg[project_id])


def _fold_into(stale: Project, owner: Project) -> Project:
    """Drop a registration whose folder is gone in favour of the project
    that already owns the folder the user located.

    This happens when a folder was moved and then opened again, which
    registers it a second time. Only a registration without conversations
    folds: conversations stay bound to the project they started in.
    """
    if stale.is_default or owner.is_default:
        raise ProjectStoreError("destination belongs to another project")
    if stale.path and Path(stale.path).expanduser().is_dir():
        raise ProjectStoreError(f"this folder is already the project “{owner.name}”")
    if stale.session_ids:
        raise ProjectStoreError(
            f"this folder is already the project “{owner.name}”; "
            "the missing project keeps its conversations until its own folder is found")
    with _registry_transaction():
        reg = _read_registry()
        reg.pop(stale.id, None)
        _write_registry(reg)
    return owner


def relocate_project(
    project_id: str,
    new_path: str | Path,
    *,
    expected_path: str | None = None,
    expected_revision: int | None = None,
    require_identity: bool = False,
    replace_identity: bool = False,
    follow_siblings: bool = True,
) -> Project:
    """Point an existing project at a new directory, keeping its id."""
    from .identity import captured_identity_matches

    p = Path(new_path).expanduser()
    if not p.is_dir():
        raise ProjectStoreError(f"not a directory: {new_path}")
    with _registry_transaction():
        proj = get_project(project_id)
        if proj is None:
            raise ProjectStoreError(f"unknown project: {project_id}")
        if expected_path is not None and proj.path != expected_path:
            raise ProjectStoreError("project changed during location update")
        if expected_revision is not None and int(proj.location_revision) != int(expected_revision):
            raise ProjectStoreError("project changed during location update")
        owner = next((other for other in list_projects()
                      if other.id != project_id and other.path
                      and Path(other.path).expanduser().resolve() == p.resolve()), None)
        if owner is not None:
            return _fold_into(proj, owner)
        if proj.is_default:
            raise ProjectStoreError("the default project cannot be relocated")
        from .identity import read_marker
        marker = read_marker(p)
        if marker and any(other.id != project_id and other.directory_identity == marker
                          for other in list_projects()):
            raise ProjectStoreError("destination identity belongs to another project")
        matches = captured_identity_matches(proj, p)
        if require_identity and not matches:
            raise ProjectStoreError("directory identity does not match")
        old_path = proj.path
        remapped = []
        old_root = Path(old_path).expanduser()
        for folder in list(proj.source_folders or []):
            try:
                rel = Path(folder).expanduser().resolve().relative_to(old_root.resolve())
                candidate = p / rel
                remapped.append(str(candidate) if candidate.is_dir() else folder)
            except (ValueError, OSError):
                remapped.append(folder)
        proj.source_folders = remapped
        proj.path = str(p.resolve())
        proj.location_revision = int(proj.location_revision or 0) + 1
        proj.location_state = "migrating"
        proj.migration_error = ""
        capture = replace_identity or matches or not require_identity
        moved = _upsert(proj, capture_identity=capture)
    try:
        from openprogram.state.store.session.session_store import default_store
        default_store().relocate_project_sessions(
            moved.session_ids, p, project_id=moved.id, old_path=old_path)
    except Exception as e:  # noqa: BLE001
        _log.warning("session locations NOT rewritten for %s: %s",
                     project_id, e)
        set_location_state(project_id, "pending", error=str(e))
        raise ProjectStoreError(f"project location repair pending: {e}") from e
    current = set_location_state(project_id, "available")
    if follow_siblings and old_path:
        follow_sibling_moves(old_path, str(p.resolve()), moved_id=project_id)
    return current or moved


def _missing(project: Project) -> bool:
    from .identity import captured_identity_matches, is_portable

    return (not project.is_default and bool(project.path) and is_portable(project)
            and not captured_identity_matches(project, project.path))


def follow_sibling_moves(old_path: str, new_path: str, *, moved_id: str = "") -> list[str]:
    """Reattach other missing projects that moved the same way.

    Folders usually move together: a whole ``Projects`` directory renamed,
    or several repositories dragged into a new parent. When one project
    is found at its new place, every missing project under the same old
    parent is tried under the matching new parent, outermost names
    included, and taken only where its own marker is found.
    """
    old, new = Path(old_path).expanduser(), Path(new_path).expanduser()
    pairs = []
    a, b = old.parent, new.parent
    while a != b:
        pairs.append((a, b))
        if a.name != b.name or a == a.parent or b == b.parent:
            break
        a, b = a.parent, b.parent
    if not pairs:
        return []
    from .identity import read_marker

    followed = []
    for project in list_projects():
        if project.id == moved_id or not _missing(project):
            continue
        recorded = Path(project.path).expanduser()
        for before, after in pairs:
            try:
                candidate = after / recorded.relative_to(before)
            except ValueError:
                continue
            if not candidate.is_dir() or read_marker(candidate) != project.directory_identity:
                continue
            try:
                relocate_project(project.id, candidate, expected_path=project.path,
                                 require_identity=True, follow_siblings=False)
                followed.append(project.id)
            except ProjectStoreError as exc:
                _log.info("sibling move not followed for %s: %s", project.id, exc)
            break
    return followed


def reattach_enclosing_project(path: str | Path) -> str | None:
    """A folder opened inside a moved project brings that project back.

    The nearest ancestor carrying a marker decides. When the marker's
    project is missing from its recorded place, it moves here; the folder
    that was opened is resolved as usual afterwards.
    """
    from .identity import read_marker

    folder = Path(path).expanduser()
    home = Path.home()
    for ancestor in folder.parents:
        if ancestor == home or ancestor == ancestor.parent:
            return None
        marker = read_marker(ancestor)
        if not marker:
            continue
        owner = next((project for project in list_projects()
                      if not project.is_default and project.directory_identity == marker), None)
        if owner is None or not _missing(owner):
            return None
        try:
            relocate_project(owner.id, ancestor, expected_path=owner.path, require_identity=True)
        except ProjectStoreError as exc:
            _log.info("enclosing project not reattached for %s: %s", owner.id, exc)
            return None
        return owner.id
    return None


def bind_session(session_id: str, project_id: str) -> None:
    """Record that ``session_id`` happened in ``project_id`` (reverse
    index on the project record). Idempotent.
    """
    with _registry_transaction():
        reg = _read_registry()
        d = reg.get(project_id)
        if d is None:
            return
        sids = list(d.get("session_ids", []) or [])
        if session_id not in sids:
            sids.append(session_id)
            d["session_ids"] = sids
            reg[project_id] = d
            _write_registry(reg)


def unbind_session(session_id: str, project_id: Optional[str] = None) -> None:
    """从项目的反向索引里移除一个 session（删会话时调用，配对 bind_session）。
    project_id=None → 从所有项目移除（不必知道它归哪个项目）。"""
    with _registry_transaction():
        reg = _read_registry()
        changed = False
        for pid, d in reg.items():
            if project_id is not None and pid != project_id:
                continue
            sids = list(d.get("session_ids", []) or [])
            if session_id in sids:
                sids.remove(session_id)
                d["session_ids"] = sids
                changed = True
        if changed:
            _write_registry(reg)


def prune_sessions(alive_ids: set[str]) -> int:
    """把所有项目 session_ids 里不在 alive_ids 的孤立记录清掉，返回清理条数。
    修 882-bug：session_ids 曾只增不减，累积了大量已删会话的死引用。

    Runs behind a reply. It looks at the published registry first, without
    the registry lock, and checks a folder only for a project that holds a
    dead reference; the lock is taken only when there is something to
    remove, so a pass that finds nothing never makes a turn wait.
    """
    stale: set[str] = set()
    for project_id, d in _registry_snapshot().items():
        if all(s in alive_ids for s in d.get("session_ids") or ()):
            continue
        # Missing project storage is unavailable, not evidence of deleted sessions.
        if not d.get("is_default") and d.get("path") and not Path(d["path"]).is_dir():
            continue
        stale.add(project_id)
    if not stale:
        return 0
    removed = 0
    with _registry_transaction():
        reg = _read_registry()
        changed = False
        for project_id in stale:
            d = reg.get(project_id)
            if d is None:
                continue
            sids = list(d.get("session_ids", []) or [])
            kept = [s for s in sids if s in alive_ids]
            if len(kept) != len(sids):
                removed += len(sids) - len(kept)
                d["session_ids"] = kept
                changed = True
        if changed:
            _write_registry(reg)
    return removed


def project_for_session(session_id: str) -> Optional[Project]:
    """Reverse lookup: which project owns this session, if any."""
    for d in _registry_snapshot().values():
        if session_id in (d.get("session_ids") or []):
            return Project.from_dict(d)
    return None


__all__ = [
    "Project",
    "ProjectGit",
    "ProjectStoreError",
    "DEFAULT_PROJECT_ID",
    "projects_dir",
    "list_projects",
    "get_project",
    "get_default_project",
    "resolve_project",
    "bind_session",
    "relocate_project",
    "follow_sibling_moves",
    "reattach_enclosing_project",
    "set_location_state",
    "unbind_session",
    "prune_sessions",
    "project_for_session",
    "load_project_settings",
    "save_project_settings",
    "ensure_footprint_ignored",
]


def set_project_hidden(project_id: str, hidden: bool) -> Project:
    """Remove/restore a navigation entry; keep paths and session ownership."""
    with _registry_transaction():
        reg = _read_registry()
        if not isinstance(project_id, str) or project_id not in reg:
            raise ValueError("unknown project")
        if hidden and reg[project_id].get("is_default"):
            raise ValueError("the default project cannot be removed")
        reg[project_id]["hidden"] = hidden
        _write_registry(reg)
        return Project.from_dict(reg[project_id])


def create_project_worktree(project_id: str, path: str, branch: str, *, progress=None,
                            cancel: threading.Event | None = None) -> Project:
    """Create a persistent worktree from HEAD without changing the source checkout.

    ``progress(stage, percent)`` hears the stages (branch, checkout with git's
    percentage, open); setting ``cancel`` during the checkout stops it and
    removes the half-made worktree and its branch (WorktreeStopped).
    """
    project = get_project(project_id)
    if not project:
        raise ValueError("unknown project")
    if not isinstance(path, str) or not Path(path).expanduser().is_absolute():
        raise ValueError("an absolute destination path is required")
    if not isinstance(branch, str) or not branch.strip() or branch.startswith("-"):
        raise ValueError("a new branch name is required")
    branch = branch.strip()
    target = Path(path).expanduser().resolve()
    source = Path(project.path).resolve()
    if target == source or source in target.parents:
        raise ValueError("choose a destination outside the source project")
    if not target.parent.is_dir():
        raise ValueError("destination parent directory does not exist")

    def git(*args):
        result = subprocess.run(["git", "-C", str(source), *args], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise ValueError(result.stderr.strip() or "git command failed")
        return result.stdout.strip()

    checkout = Path(git("rev-parse", "--show-toplevel")).resolve()
    if target == checkout or checkout in target.parents:
        raise ValueError("choose a destination outside the source Git checkout")
    git("check-ref-format", "--branch", branch)
    # Serialize worktree creates. An ACK retry recognizes this exact
    # branch/path pair, so it cannot produce a second worktree.
    with _worktree_lock:
        if target.exists():
            records = git("worktree", "list", "--porcelain").split("\n\n")
            expected = {f"worktree {target}", f"branch refs/heads/{branch}"}
            if not any(expected.issubset(set(record.splitlines())) for record in records):
                raise ValueError("destination already exists")
        else:
            _report(progress, "branch")
            git("worktree", "add", "--no-checkout", "-b", branch, "--", str(target), "HEAD")
            try:
                _checkout_files(target, progress, cancel)
            except BaseException:
                # A stopped or failed checkout leaves neither the folder nor the branch.
                subprocess.run(["git", "-C", str(source), "worktree", "remove", "--force", "--", str(target)],
                               capture_output=True, timeout=120)
                subprocess.run(["git", "-C", str(source), "branch", "-D", branch], capture_output=True, timeout=60)
                raise
        _report(progress, "open")
        return resolve_project(target)


class WorktreeStopped(Exception):
    """The user stopped a worktree while its files were being checked out."""


def _report(progress, stage: str, percent: int | None = None) -> None:
    if progress is not None:
        try:
            progress(stage, percent)
        except Exception:
            _log.debug("worktree progress callback failed", exc_info=True)


def _checkout_files(target: Path, progress, cancel) -> None:
    """Fill a --no-checkout worktree, reporting git's percentage and stopping on ``cancel``."""
    _report(progress, "checkout", 0)
    proc = subprocess.Popen(["git", "-C", str(target), "checkout", "-f", "--progress"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    stderr: list[bytes] = []
    reader = threading.Thread(target=_read_progress, args=(proc, progress, stderr), daemon=True)
    reader.start()
    while proc.poll() is None:
        if cancel is not None and cancel.wait(0.1):
            proc.kill()
            proc.wait()
            raise WorktreeStopped("stopped")
        if cancel is None:
            time.sleep(0.1)
    reader.join(timeout=2)
    tail = b"".join(stderr)[-2000:]
    if proc.returncode:
        raise ValueError(tail.decode("utf-8", "replace").strip() or "git checkout failed")
    _report(progress, "checkout", 100)


_CHECKOUT_PERCENT = re.compile(rb"Updating files:\s+(\d+)%")


def _read_progress(proc, progress, sink: list[bytes]) -> None:
    pattern = _CHECKOUT_PERCENT
    buffer = b""
    while True:
        chunk = proc.stderr.read1(4096) if hasattr(proc.stderr, "read1") else proc.stderr.read(4096)
        if not chunk:
            break
        sink.append(chunk)
        buffer = (buffer + chunk)[-256:]
        found = pattern.findall(buffer)
        if found:
            _report(progress, "checkout", int(found[-1]))
