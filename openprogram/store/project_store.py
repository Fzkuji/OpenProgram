"""Project entity memory — the second half of the entity layer.

The entity (实体) tier of OpenProgram memory has two kinds of
git-backed stores (see ``docs/design/memory-v2.md`` §2):

  * **Session-Git** (already built, ``git_session.py``) — one repo per
    conversation, every turn a commit.
  * **Project-Git** (this module) — one repo per *project*, where a
    project is a long-lived working unit bound to a filesystem
    directory. Agent file edits in that directory get committed here.

Every session belongs to exactly one project. Two cases:

  * **Bound project** — the user worked in a real directory (their
    code / docs repo). That directory IS the project git repo; we
    reuse its ``.git`` if it has one, else ``git init`` it.
  * **Default project** — the catch-all for ad-hoc chats that never
    named a directory. Lives in the home hidden dir at
    ``<state>/projects/default/`` as its own git repo, so there is
    never a session with no project home.

Both auto-create their git repo on first use, mirroring how
``GitSession`` lazily ``git init``s a session repo on first write.

Registry: ``<state>/projects/projects.json`` maps ``project_id`` →
metadata (name, path, sessions, status). The registry is the index;
the git repos are the truth.

Cross-platform: all git calls go through ``git -C <path> ...`` via
subprocess (no cwd dependence), same as ``git_session._run``.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


DEFAULT_PROJECT_ID = "default"


class ProjectStoreError(RuntimeError):
    """Raised on unrecoverable git / registry failure."""


# ── paths ──────────────────────────────────────────────────────────


def projects_dir() -> Path:
    """``<state>/projects/`` — holds the registry + the default repo."""
    from openprogram.paths import get_state_dir
    d = Path(get_state_dir()) / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _registry_path() -> Path:
    return projects_dir() / "projects.json"


def _default_project_path() -> Path:
    return projects_dir() / "default"


# ── Project record ─────────────────────────────────────────────────


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
            created_at=float(d.get("created_at", time.time())),
        )


# ── ProjectGit: the git wrapper for a project's working directory ──


class ProjectGit:
    """Wraps the git repo backing a project's working directory.

    Lazy like ``GitSession``: construction is cheap, disk work happens
    in :meth:`ensure_init`. The difference from a session repo: a
    project repo is often a *pre-existing user repo* we must NOT
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
            # git. A project-bound session repo lives at
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

    # -- git plumbing (mirrors git_session._run) --

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


# ── Registry (projects.json) ───────────────────────────────────────

_reg_lock = threading.Lock()


def _read_registry() -> dict[str, dict]:
    p = _registry_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_registry(reg: dict[str, dict]) -> None:
    p = _registry_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2, ensure_ascii=False, default=str),
                   encoding="utf-8")
    tmp.replace(p)


def _project_id_for_path(path: Path) -> str:
    """Deterministic project id from an absolute path.

    ``proj_<8 hex>`` of the normalized path, so the same directory
    always maps to the same project across runs.
    """
    norm = str(path.resolve()).lower()
    h = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:8]
    return f"proj_{h}"


def ensure_footprint_ignored(project_path: str | Path) -> None:
    """Write ``<project>/.openprogram/.gitignore`` = ``*`` so the user's
    git ignores OpenProgram's entire footprint (the session repos we
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
        current = gi.read_text(encoding="utf-8") if gi.exists() else ""
        if current != "*\n":
            gi.write_text("*\n", encoding="utf-8")
    except OSError:
        pass


def list_projects() -> list[Project]:
    with _reg_lock:
        return [Project.from_dict(d) for d in _read_registry().values()]


def get_project(project_id: str) -> Optional[Project]:
    with _reg_lock:
        d = _read_registry().get(project_id)
        return Project.from_dict(d) if d else None


def _upsert(project: Project) -> Project:
    with _reg_lock:
        reg = _read_registry()
        reg[project.id] = project.to_dict()
        _write_registry(reg)
    return project


def get_default_project() -> Project:
    """The catch-all project for ad-hoc chats with no bound directory.

    This is a **pure logical label** — it does NOT get its own git
    repo. An ad-hoc conversation produces no project files (its file
    edits, if any, land in the session's own ``workdir/``), so a
    "default project repo" would always be empty. Such sessions just
    carry ``project_id="default"`` for grouping / scope, and their
    entity memory IS the session repo, stored at the home root
    ``<state>/sessions/<id>/``.

    A real git repo is created only when a session binds to an actual
    working directory — see :func:`resolve_project` with a path.
    """
    existing = get_project(DEFAULT_PROJECT_ID)
    if existing is not None:
        return existing
    proj = Project(
        id=DEFAULT_PROJECT_ID,
        name="Default",
        path="",            # no backing repo — logical label only
        is_default=True,
    )
    return _upsert(proj)


def resolve_project(path: str | Path | None = None, *, name: str | None = None) -> Project:
    """Resolve (and register) the project for a working directory.

    * ``path=None`` → the default project (logical label, no repo).
    * ``path=<dir>`` → the project bound to that directory. Registered
      in ``projects.json`` keyed by a path-derived id, so the same
      directory always resolves to the same project.

    **Binding a folder does not touch its git.** We don't ``git init``
    here, and don't require the folder to be a git repo. Git work is
    deferred to turn end: if auto-commit is on (default) and the agent
    edits files, ``project_commit`` will auto-init a non-git folder then
    (safely — baseline commit first, refuse on dep/build dirs). So the
    mere act of opening a folder has zero git side-effects; a repo only
    appears once the agent actually changes something. (The session's
    OWN entity memory lives at ``<dir>/.openprogram/sessions/<id>/``
    regardless — a separate git repo we manage, not the user's.)
    """
    if path is None:
        return get_default_project()

    p = Path(path).expanduser()
    # Drop our .gitignore in NOW (rule A: no git-init, but the footprint
    # must be hidden in case the folder is / becomes a git repo).
    ensure_footprint_ignored(p)
    pid = _project_id_for_path(p)
    existing = get_project(pid)
    if existing is not None:
        return existing

    proj = Project(
        id=pid,
        name=name or p.name or pid,
        path=str(p.resolve()),
        is_default=False,
    )
    return _upsert(proj)


def bind_session(session_id: str, project_id: str) -> None:
    """Record that ``session_id`` happened in ``project_id`` (reverse
    index on the project record). Idempotent.
    """
    with _reg_lock:
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


def project_for_session(session_id: str) -> Optional[Project]:
    """Reverse lookup: which project owns this session, if any."""
    with _reg_lock:
        for d in _read_registry().values():
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
    "project_for_session",
    "ensure_footprint_ignored",
]
