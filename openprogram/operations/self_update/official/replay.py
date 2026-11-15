"""Carry the owner's own commits over to an official release.

The release is a squashed line, so nothing here needs its history: only the new
release commit and the commits on ``local`` that it does not contain.

1. Check the assets and move ``refs/heads/release``.
2. Make the release's own version ready, from the prebuilt asset when the base
   is here or carried, otherwise by building it.
3. With no commits of the owner's, that version goes to trial as it is.
4. Otherwise ``work/official-<release>`` starts at the release and picks each
   commit, oldest first. A commit that conflicts is saved under
   ``unreplayable/<release>/`` and the rest carry on.
5. The result is built on top of the release's version, tried and activated.
6. If it fails before activation, the newest commit is saved and dropped and
   the rest are tried again, ending at the unmodified release. After
   :data:`MAX_DROP_RETRIES` retries the remaining commits go at once.

The trial and the switch belong to the update controller and are passed in as
:class:`Lifecycle`. Nothing before ``activate`` touches what is running, so a
controller that dies part way simply starts this again.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
import shutil

from openprogram.operations.self_update.versions import build as builder
from openprogram.operations.self_update.versions import store
from openprogram.operations.self_update.versions.build import (
    BuildError, BuildRequest, BuildResult, NotEnoughSpace,
)
from openprogram.operations.self_update.versions.layout import Layout

from . import assets
from .assets import LOCAL_REF, Release, ReleaseError, git, git_output

MAX_DROP_RETRIES = 5
ACTIVATED = "activated"
ABORTED = "aborted"
ROLLED_BACK = "rolled_back"
WAITING_FOR_SHELL = "waiting_for_shell"
UP_TO_DATE = "up_to_date"
CONFLICT = "conflict"
FAILED_TRIAL = "failed_trial"


@dataclass(frozen=True)
class Verdict:
    passed: bool
    reason: str = ""


@dataclass(frozen=True)
class Lifecycle:
    """The update controller's part. Both receive a sealed version's id."""

    trial: Callable[[str], Verdict]
    activate: Callable[[str], Verdict]
    build: Callable[[Layout, BuildRequest], BuildResult] = builder.build_version


@dataclass(frozen=True)
class OwnCommit:
    sha: str
    subject: str


@dataclass(frozen=True)
class SavedCommit:
    sha: str
    subject: str
    reason: str
    patch: str


@dataclass
class ReplayResult:
    """What the conversation is told."""

    outcome: str
    message: str
    release: str | None = None
    version: str | None = None
    replayed: list[OwnCommit] = field(default_factory=list)
    saved: list[SavedCommit] = field(default_factory=list)
    already_in_release: list[OwnCommit] = field(default_factory=list)
    attempts: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _Picked:
    commit: OwnCommit
    tip: str  # The worktree's commit once this one is on it.


def own_commits(layout: Layout, release_commit: str) -> list[OwnCommit]:
    """Commits on ``local`` the release lacks, oldest first."""
    if git(layout.repo, "merge-base", release_commit, LOCAL_REF).returncode != 0:
        raise ReleaseError("this installation has no history in common with the official line")
    output = git_output(
        layout.repo, "log", "--reverse", "--topo-order", "--no-merges", "--format=%H%x00%s",
        f"{release_commit}..{LOCAL_REF}",
    )
    return [OwnCommit(*line.split("\0", 1)) for line in output.splitlines() if line]


def _is_up_to_date(layout: Layout, release_commit: str) -> bool:
    return git(layout.repo, "merge-base", "--is-ancestor", release_commit, LOCAL_REF).returncode == 0


def _save(layout: Layout, release: Release, commit: OwnCommit, reason: str) -> SavedCommit:
    directory = layout.unreplayable(release.release)
    directory.mkdir(parents=True, exist_ok=True)
    patch = directory / f"{commit.sha}.patch"
    result = git(layout.repo, "format-patch", "-1", "--binary", "--stdout", commit.sha, raw=True)
    if result.returncode != 0:
        raise ReleaseError(f"could not save {commit.sha[:12]}: {result.stderr.decode('utf-8', 'replace').strip()}")
    patch.write_bytes(result.stdout)
    return SavedCommit(commit.sha, commit.subject, reason, str(patch))


def _remove_worktree(layout: Layout, worktree: Path) -> None:
    git(layout.repo, "worktree", "remove", "--force", str(worktree))
    shutil.rmtree(worktree, ignore_errors=True)
    git(layout.repo, "worktree", "prune")


def _pick(worktree: Path, sha: str) -> str:
    """``picked``, ``empty`` (the release already has it) or ``conflict``."""
    if git(worktree, "cherry-pick", sha).returncode == 0:
        return "picked"
    if not git_output(worktree, "status", "--porcelain"):
        git_output(worktree, "cherry-pick", "--skip")
        return "empty"
    git(worktree, "cherry-pick", "--abort")
    return CONFLICT


def _intact(layout: Layout, manifest: dict) -> bool:
    try:
        return not store.changed_files(layout.version(str(manifest["id"])), str(manifest["base"]))
    except (OSError, ValueError, KeyError):
        return False


def _ready_release_version(
    layout: Layout, release: Release, lifecycle: Lifecycle, *, update_id: str, parent: str | None,
) -> str:
    for manifest in store.list_versions(layout):
        if manifest.get("commit") == release.commit and _intact(layout, manifest):
            return str(manifest["id"])
    builder.check_space(layout, new_base=not layout.base(release.base).is_dir())
    assets.install_carried_base(layout, release)
    if layout.base(release.base).is_dir():
        return assets.install_version(layout, release, update_id=update_id, parent=parent)
    # Only a release that carries no base gets here; the machine builds it
    # from the commit instead.
    built = lifecycle.build(layout, BuildRequest(
        update_id, release.commit, parent=parent, built_by=f"official {release.release}",
    ))
    return built.version


def _message(result: ReplayResult, detail: str = "") -> str:
    name = result.release or "the release"
    counts = f"{len(result.replayed)} of your commits replayed, {len(result.saved)} saved"
    if result.outcome == ACTIVATED:
        return f"Updated to {name}: {counts}."
    if result.outcome == ROLLED_BACK:
        return f"{name} was returned to the previous version: {detail}"
    return f"{name} was not applied, and the installation is unchanged: {detail}"


def _finish(result: ReplayResult, outcome: str, detail: str = "") -> ReplayResult:
    result.outcome = outcome
    if outcome == ABORTED:
        result.replayed = []  # Nothing went live, so nothing was carried over.
    result.message = _message(result, detail)
    return result


def replay_release(
    layout: Layout, directory: Path, lifecycle: Lifecycle, *,
    update_id: str, parent: str | None = None, platform: str | None = None,
    trusted_keys: Iterable[str] | None = None,
) -> ReplayResult:
    """Apply the release in ``directory``. Failures come back in the result."""
    result = ReplayResult(outcome=ABORTED, message="")
    try:
        release = assets.read_release(directory, platform=platform, trusted_keys=trusted_keys, layout=layout)
        result.release = release.release
        waiting = assets.needs_new_shell(layout, release)
        if waiting is not None:
            result.outcome = WAITING_FOR_SHELL
            result.message = f"{waiting}; it applies once the shell update is installed."
            return result
        assets.fetch_release(layout, release)
        if _is_up_to_date(layout, release.commit):
            result.outcome = UP_TO_DATE
            result.message = f"This installation already contains {release.release}."
            return result
        return _apply(layout, release, lifecycle, result, update_id=update_id, parent=parent)
    except (ReleaseError, BuildError, store.SealError, OSError) as exc:
        return _finish(result, ABORTED, str(exc))


def _apply(
    layout: Layout, release: Release, lifecycle: Lifecycle, result: ReplayResult, *,
    update_id: str, parent: str | None,
) -> ReplayResult:
    commits = own_commits(layout, release.commit)
    release_version = _ready_release_version(layout, release, lifecycle, update_id=update_id, parent=parent)
    if not commits:
        return _try_unmodified(release_version, lifecycle, result)

    worktree = layout.work / f"official-{release.release}"
    layout.work.mkdir(parents=True, exist_ok=True)
    _remove_worktree(layout, worktree)
    git_output(layout.repo, "worktree", "add", "--detach", "--force", str(worktree), release.commit)
    try:
        assets.install_carried_toolchain(layout, release)
        stack = _pick_all(layout, release, worktree, commits, result)
        return _try_with_commits(
            layout, release, lifecycle, result, worktree, stack,
            update_id=update_id, release_version=release_version,
        )
    finally:
        _remove_worktree(layout, worktree)


def _pick_all(
    layout: Layout, release: Release, worktree: Path, commits: list[OwnCommit], result: ReplayResult,
) -> list[_Picked]:
    stack: list[_Picked] = []
    for commit in commits:
        state = _pick(worktree, commit.sha)
        if state == "picked":
            stack.append(_Picked(commit, git_output(worktree, "rev-parse", "HEAD")))
        elif state == "empty":
            result.already_in_release.append(commit)
        else:
            result.saved.append(_save(layout, release, commit, CONFLICT))
    return stack


def _settle(version: str, lifecycle: Lifecycle, result: ReplayResult, verdict: Verdict) -> ReplayResult | None:
    """Activate a version that passed its trial; ``None`` means it did not pass."""
    if not verdict.passed:
        return None
    result.version = version
    activation = lifecycle.activate(version)
    if activation.passed:
        return _finish(result, ACTIVATED)
    return _finish(result, ROLLED_BACK, activation.reason)


def _try_unmodified(version: str, lifecycle: Lifecycle, result: ReplayResult) -> ReplayResult:
    result.replayed = []
    result.attempts += 1
    verdict = lifecycle.trial(version)
    return _settle(version, lifecycle, result, verdict) or _finish(result, ABORTED, verdict.reason)


def _drop_newest(
    layout: Layout, release: Release, worktree: Path, stack: list[_Picked], result: ReplayResult, *, count: int,
) -> None:
    for _ in range(count):
        result.saved.append(_save(layout, release, stack.pop().commit, FAILED_TRIAL))
    tip = stack[-1].tip if stack else release.commit
    git_output(worktree, "reset", "--hard", "--quiet", tip)
    git_output(worktree, "clean", "-fdxq")


def _try_with_commits(
    layout: Layout, release: Release, lifecycle: Lifecycle, result: ReplayResult,
    worktree: Path, stack: list[_Picked], *, update_id: str, release_version: str,
) -> ReplayResult:
    retries = 0
    while stack:
        result.replayed = [entry.commit for entry in stack]
        result.attempts += 1
        try:
            built = lifecycle.build(layout, BuildRequest(
                update_id, stack[-1].tip, worktree=worktree, parent=release_version,
                built_by=f"official {release.release}",
            ))
        except NotEnoughSpace:
            raise  # No commit is to blame, so none is dropped.
        except BuildError as exc:
            verdict, version = Verdict(False, str(exc)), None
        else:
            version = built.version
            verdict = lifecycle.trial(version)
        if version is not None:
            settled = _settle(version, lifecycle, result, verdict)
            if settled is not None:
                return settled
        count = len(stack) if retries >= MAX_DROP_RETRIES else 1
        _drop_newest(layout, release, worktree, stack, result, count=count)
        retries += 1
    return _try_unmodified(release_version, lifecycle, result)
