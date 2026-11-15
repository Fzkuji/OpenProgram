"""Going back, and adopting a version move the controller did not make.

``plan`` decides what an undo target means. A version id is the whole state to
return to (``undo_to``). A commit is one change to remove while later ones stay
(``undo_commit``), except the newest commit when the version just before it is
still on disk, where going back to that version needs no build.

Going back to a version moves ``local`` only when the switch commits, and the
old tip stays reachable at ``refs/openprogram/undone/<update>``. A version that
is no longer on disk is rebuilt from its commit, so nothing that was ever on
``local`` or under ``undone`` is lost.

The launcher's guard, the shell's recovery page and ``openprogram versions use``
move the pointer without touching ``local``. ``reconcile_manual_move`` catches
``local`` up at the next healthy start, so the next task does not begin from the
code that was left, and the guard does not return to it.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
from typing import Callable

from ..versions import pointer as pointers
from ..versions import store as versions
from ..versions.layout import Layout, valid_id

UNDO_TO = "undo_to"
UNDO_COMMIT = "undo_commit"
LOCAL = "refs/heads/local"
UNDONE_PREFIX = "refs/openprogram/undone/"
VERSION_PREFIX = "refs/openprogram/version/"
WORK_BRANCH_PREFIX = "op/wt/"
MANUAL_WRITERS = frozenset({"launcher", "shell", "bootstrap"})
RECONCILE_WRITER = "reconcile"

_ABBREVIATED_COMMIT = re.compile(r"[0-9a-f]{7,40}")
_VERSION_ID = re.compile(r"\d{8}T\d{6}Z-([0-9a-f]{12})")
_NAME_ATTEMPTS = 20
_REMOVED_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")


class UndoError(RuntimeError):
    """The undo cannot be done; the message says why."""


class RevertConflict(UndoError):
    """The later commits touch the same lines as the commit being undone."""

    def __init__(self, commit: str, files: list[str]) -> None:
        self.commit = commit
        self.files = files
        super().__init__(
            f"Commit {commit[:12]} cannot be undone on its own, because later commits changed "
            f"the same lines: {', '.join(files)}."
        )


def _run(cwd: Path, *arguments: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if key not in _REMOVED_VARIABLES}
    environment["LC_ALL"] = "C"
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), *arguments],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=environment,
        )
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        raise UndoError(f"git could not run: {type(exc).__name__}") from exc


def _git(cwd: Path, *arguments: str, timeout: float = 60) -> str:
    result = _run(cwd, *arguments, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:500]
        raise UndoError(f"git {arguments[0]} failed: {detail or result.returncode}")
    return result.stdout.strip()


def _commit(repository: Path, name: str) -> str | None:
    result = _run(repository, "rev-parse", "--verify", "--quiet", f"{name}^{{commit}}")
    return result.stdout.strip() if result.returncode == 0 else None


def _is_ancestor(repository: Path, commit: str, line: str) -> bool:
    return _run(repository, "merge-base", "--is-ancestor", commit, line).returncode == 0


def _subject(repository: Path, commit: str) -> str:
    return _git(repository, "log", "-1", "--format=%s", commit)


@dataclass(frozen=True)
class UndoPlan:
    kind: str
    target: str
    commit: str
    """``undo_to``: the commit to run. ``undo_commit``: the commit to remove."""
    local_tip: str
    subject: str
    version: str | None = None
    """The retained version to activate, or ``None`` when it must be built."""
    base: str | None = None
    task: str | None = None
    """``undo_commit``: the ``work/<task>`` the revert is made in."""

    @property
    def goal(self) -> str:
        if self.kind == UNDO_COMMIT:
            return f"Undo commit {self.commit[:12]}: {self.subject}"
        return f"Go back to {self.version or self.target}: {self.subject}"

    @property
    def assertions(self) -> tuple[str, ...]:
        if self.kind == UNDO_COMMIT:
            return (f"The change from commit {self.commit[:12]} ({self.subject}) is no longer in the running code.",)
        return (f"OpenProgram runs the code of commit {self.commit}, and nothing committed after it.",)

    def request_fields(self) -> dict[str, object]:
        """What this undo sets on its update request."""
        fields: dict[str, object] = {
            "kind": self.kind, "base_sha": self.local_tip, "goal": self.goal,
            "assertions": self.assertions, "task": self.task or "",
            "candidate_version": self.version or "",
        }
        if self.kind == UNDO_TO:
            fields["candidate_sha"] = self.commit
        return fields


def _retained(layout: Layout) -> dict[str, dict]:
    """Sealed versions that can run: a version whose base is gone cannot."""
    return {
        str(manifest["id"]): manifest for manifest in versions.list_versions(layout)
        if valid_id(manifest.get("base")) and layout.base(str(manifest["base"])).is_dir()
    }


def commit_on_line(repository: Path, target: str, line: str) -> tuple[str, str]:
    """Full id and subject of a commit that a revert on ``line`` can remove."""
    text = target.strip().lower()
    if _ABBREVIATED_COMMIT.fullmatch(text) is None:
        raise UndoError(f"{target!r} is not a commit id.")
    commit = _commit(repository, text)
    if commit is None or not _is_ancestor(repository, commit, line):
        raise UndoError(f"Commit {target} is not part of the code in use.")
    parents = _git(repository, "rev-list", "--parents", "-n", "1", commit).split()[1:]
    if not parents:
        raise UndoError(f"Commit {commit[:12]} is the first commit, so there is nothing to go back to.")
    if len(parents) > 1:
        raise UndoError(f"Commit {commit[:12]} merges two lines, so it cannot be undone on its own.")
    return commit, _subject(repository, commit)


def _reachable(repository: Path, commit: str) -> bool:
    return bool(_git(repository, "for-each-ref", "--contains", commit, LOCAL, UNDONE_PREFIX))


def _version_plan(layout: Layout, target: str, manifest: dict, *, tip: str) -> UndoPlan:
    if target == pointers.read(layout).version:
        raise UndoError(f"{target} is already the running version.")
    commit = str(manifest["commit"])
    if commit == tip:
        raise UndoError(f"{target} is built from the commit already in use, so going back changes nothing.")
    if _commit(layout.repo, commit) is None:
        raise UndoError(f"The commit of {target} is missing from the repository.")
    return UndoPlan(
        UNDO_TO, target, commit, tip, _subject(layout.repo, commit),
        version=target, base=str(manifest["base"]),
    )


def _rebuild_plan(layout: Layout, target: str, abbreviated: str, *, tip: str) -> UndoPlan:
    """A version that is gone: its commit is built again."""
    commit = _commit(layout.repo, f"{VERSION_PREFIX}{target}") or _commit(layout.repo, abbreviated)
    if commit is None or not _reachable(layout.repo, commit):
        raise UndoError(f"Version {target} is no longer on disk and its commit cannot be found.")
    if commit == tip:
        raise UndoError(f"{target} is built from the commit already in use, so going back changes nothing.")
    return UndoPlan(UNDO_TO, target, commit, tip, _subject(layout.repo, commit))


def _free_task(layout: Layout, commit: str) -> str:
    """``undo-<short>``, numbered when an earlier attempt left its worktree."""
    stem = f"undo-{commit[:12]}"
    for attempt in range(_NAME_ATTEMPTS):
        name = stem if attempt == 0 else f"{stem}-{attempt + 1}"
        taken = (layout.work / name).exists() or _commit(layout.repo, f"refs/heads/{WORK_BRANCH_PREFIX}{name}")
        if not taken:
            return name
    raise UndoError(f"Too many earlier attempts to undo {commit[:12]} are still on disk.")


def plan(layout: Layout, target: str) -> UndoPlan:
    """What ``self_update(action="undo", target=...)`` means right now."""
    try:
        return _plan(layout, target)
    except (pointers.PointerError, versions.SealError) as exc:
        raise UndoError(str(exc)) from exc


def _plan(layout: Layout, target: str) -> UndoPlan:
    text = target.strip() if isinstance(target, str) else ""
    if not text:
        raise UndoError("target is required: a version id, or a commit to undo.")
    tip = _commit(layout.repo, LOCAL)
    if tip is None:
        raise UndoError("The repository has no local line.")
    retained = _retained(layout)
    if text in retained:
        return _version_plan(layout, text, retained[text], tip=tip)
    version_id = _VERSION_ID.fullmatch(text)
    if version_id is not None:
        return _rebuild_plan(layout, text, version_id[1], tip=tip)
    commit, subject = commit_on_line(layout.repo, text, LOCAL)
    if commit == tip:
        running = pointers.read(layout).version
        parent = _git(layout.repo, "rev-parse", f"{commit}^")
        for name, manifest in retained.items():  # newest first
            if manifest["commit"] == parent and name != running:
                return _version_plan(layout, name, manifest, tip=tip)
    return UndoPlan(UNDO_COMMIT, text, commit, tip, subject, task=_free_task(layout, commit))


def revert_in(worktree: Path, commit: str) -> str:
    """Revert ``commit`` in a worktree and return the new ``HEAD``."""
    identity: list[str] = []
    if _run(worktree, "config", "user.email").returncode != 0:
        identity = ["-c", "user.name=OpenProgram", "-c", "user.email=openprogram@localhost"]
    result = _run(
        worktree, *identity, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
        "revert", "--no-edit", commit,
    )
    if result.returncode == 0:
        return _git(worktree, "rev-parse", "HEAD")
    conflicted = _git(worktree, "diff", "--name-only", "--diff-filter=U").splitlines()
    _run(worktree, "revert", "--abort")
    if conflicted:
        raise RevertConflict(commit, conflicted)
    detail = (result.stderr or result.stdout).strip()[:500]
    raise UndoError(f"Commit {commit[:12]} could not be reverted: {detail}")


@dataclass(frozen=True)
class RevertWork:
    task: str
    path: Path
    branch: str
    head: str
    worktree_id: str = ""


def create_revert(layout: Layout, undo: UndoPlan, *, manager=None, parent_session: str | None = None) -> RevertWork:
    """Make the revert commit of an ``undo_commit`` in ``work/<task>``, from ``local``.

    ``manager`` registers the worktree, which the lifecycle needs to find it again.
    """
    if undo.kind != UNDO_COMMIT or undo.task is None:
        raise ValueError("only an undo_commit plan has a revert to make")
    path = layout.work / undo.task
    branch = f"{WORK_BRANCH_PREFIX}{undo.task}"
    layout.work.mkdir(parents=True, exist_ok=True)
    registered = None
    if manager is None:
        _git(layout.repo, "worktree", "add", "-q", "-b", branch, str(path), undo.local_tip, timeout=300)
    else:
        registered = manager.create_worktree(
            str(layout.repo), branch_name=branch, base_ref=undo.local_tip, label=undo.task,
            parent_session=parent_session, destination=str(path),
        )
    try:
        return RevertWork(undo.task, path, branch, revert_in(path, undo.commit),
                          "" if registered is None else registered.id)
    except UndoError:
        if registered is not None:
            manager.discard_worktree(registered.id, force=True)
        else:
            _run(layout.repo, "worktree", "remove", "--force", str(path), timeout=120)
            _run(layout.repo, "branch", "-D", branch)
        raise


def _save_tip(layout: Layout, name: str, tip: str) -> str:
    reference = f"{UNDONE_PREFIX}{name}"
    _git(layout.repo, "update-ref", reference, tip)
    return reference


def commit_going_back(layout: Layout, update_id: str, commit: str, *, expected_tip: str) -> str:
    """Move ``local`` to the version gone back to, as an ``undo_to`` switch commits.

    The old tip is stored first, and each step can run again after a crash.
    """
    reference = f"{UNDONE_PREFIX}{update_id}"
    tip = _commit(layout.repo, LOCAL)
    if tip == commit:
        return reference
    if tip != expected_tip:
        raise UndoError(f"The local line moved to {tip}; expected {expected_tip}.")
    _save_tip(layout, update_id, expected_tip)
    _git(layout.repo, "update-ref", LOCAL, commit, expected_tip)
    return reference


@dataclass(frozen=True)
class Reconciled:
    version: str
    moved_local: bool
    undone: str | None
    returned_from: str | None
    """The version the launcher's guard left, when the owner should hear about it."""


def _left_behind(layout: Layout) -> tuple[str | None, bool]:
    """The version the last pointer move left, and whether it was on probation."""
    try:
        previous = pointers.parse(layout.previous_pointer.read_text(encoding="ascii"))
    except (OSError, UnicodeDecodeError, pointers.PointerError):
        return None, False
    return previous.version, bool(previous.probation)


def _returned_notice(returned_from: str | None, version: str) -> str:
    left = f"Version {returned_from}" if returned_from else "The newer version"
    return f"{left} did not start, so OpenProgram went back to version {version}."


def _fallback_for(layout: Layout, present: pointers.Pointer, abandoned: set[str]) -> tuple[str, str]:
    """A ``previous_good`` that is neither the running version nor the code left behind."""
    if present.previous_good and present.previous_good not in {present.version, *abandoned}:
        return present.previous_good, present.previous_good_base
    committed = versions.committed_versions(layout) or set()
    for name, manifest in _retained(layout).items():
        if name in committed and name != present.version and name not in abandoned:
            return name, str(manifest["base"])
    return "", ""


def reconcile_manual_move(
    layout: Layout, running: str, *, tell: Callable[[str, str], object] | None = None,
) -> Reconciled | None:
    """Adopt a pointer move made outside the controller; ``None`` if there is none.

    Run once this version has answered ``/healthz``. A switch still on probation
    belongs to its controller. Each step can run again after a crash: the old
    tip is saved before ``local`` moves, and the pointer is marked last.
    """
    present = pointers.read(layout)
    if present.writer not in MANUAL_WRITERS or present.probation or present.version != running:
        return None
    commit = str(versions.read_manifest(layout, running)["commit"])
    tip = _commit(layout.repo, LOCAL)
    undone = None
    if tip != commit:
        if tip is None:
            raise UndoError("The repository has no local line.")
        undone = _save_tip(layout, f"manual-{present.generation}", tip)
        _git(layout.repo, "update-ref", LOCAL, commit, tip)
    _git(layout.repo, "update-ref", f"{VERSION_PREFIX}{running}", commit)

    left, was_on_probation = _left_behind(layout)
    returned_from = None
    if present.writer == "launcher" and not was_on_probation:
        returned_from = left if left != running else None
        if tell is not None:
            # On probation the update's own result has already said this.
            try:
                tell(f"guard-{present.generation}", _returned_notice(returned_from, running))
            except Exception:  # noqa: BLE001 - telling must not stop the line from moving
                import logging

                logging.getLogger(__name__).warning("could not report the return to a working version", exc_info=True)

    abandoned = {name for name in (present.last_good, left) if name and name != running}
    previous_good, previous_good_base = _fallback_for(layout, present, abandoned)
    try:
        pointers.compare_and_set(
            layout, present.generation, writer=RECONCILE_WRITER,
            last_good=running, last_good_base=present.base,
            previous_good=previous_good, previous_good_base=previous_good_base,
        )
    except pointers.PointerConflict:
        return None  # Another writer moved it; the next start looks again.
    return Reconciled(running, undone is not None, undone, returned_from)
