"""Stable and development instances of one source checkout.

The stable instance is the installed App (``/Applications/OpenProgram.app``,
port 18100, ``~/.openprogram``) and only ever runs the ``stable`` branch.
The development instance is a runtime (port 18200,
``~/.openprogram-dev``) that runs whatever ``dev`` commit was deployed to
it last, opened in ``/Applications/OpenProgram Dev.app`` on macOS or in a
browser. Commits reach stable only by promotion: a commit that the
development instance is serving, healthy, is installed into the App by
``scripts/local-instances/refresh-local-app.sh``, which fast-forwards ``stable`` onto it
once the new version is healthy.

Both build from managed detached worktrees of the source repository, so
neither depends on the state of a developer's checkout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from openprogram.host._platform.filesystem import replace_file

DEV_PROFILE = "dev"
DEV_PORT = 18200
# Browser debugging port of the development App (scripts/local-instances/install-dev-app.sh);
# the stable App uses 9223.
DEV_APP_DEBUG_PORT = 9224
STABLE_PORT = 18100
STABLE_BRANCH = "stable"
DEV_BRANCH = "dev"
APP_PATH = Path("/Applications/OpenProgram.app")
SOURCE_REPO_FILE = "source-repo"
# How long a promotion waits for the stable instance to go quiet.
STABLE_QUIET_WAIT_SECONDS = 600
# Inside the stable worker the calling turn cannot end while it waits, so
# waiting longer than a moment only delays the same answer.
STABLE_QUIET_WAIT_FROM_WORKER_SECONDS = 5
_NAMED_WORK_LIMIT = 10


class InstanceError(Exception):
    """A deploy or promotion was refused; the message says why."""


class NotServedError(InstanceError):
    """Only the commit the development instance serves can be promoted."""


class StableBusyError(InstanceError):
    """The stable instance stayed busy, so nothing was promoted."""


class RefreshFailedError(InstanceError):
    """Installing the commit into the App failed, so it was not promoted.

    The refresh puts the previous App and worker back, and ``stable`` moves
    only once the new version is healthy; the message says what runs now.
    """


def stable_state_dir() -> Path:
    return Path.home() / ".openprogram"


def dev_state_dir() -> Path:
    return Path.home() / f".openprogram-{DEV_PROFILE}"


def dev_runtime_dir() -> Path:
    return dev_state_dir() / "runtime"


def dev_artifacts_dir() -> Path:
    """The wheel and TUI bundle of the last dev deploy, for promotion."""
    return dev_state_dir() / "artifacts"


def source_repo() -> Path:
    """The source checkout both instances build from.

    ``OPENPROGRAM_SOURCE_REPO`` wins; otherwise the path the last refresh
    recorded in ``~/.openprogram/source-repo``.
    """
    value = os.environ.get("OPENPROGRAM_SOURCE_REPO", "").strip()
    if not value:
        try:
            value = (stable_state_dir() / SOURCE_REPO_FILE).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            value = ""
    if not value:
        raise InstanceError(
            "no source checkout is known; set OPENPROGRAM_SOURCE_REPO or "
            "run scripts/local-instances/refresh-dev-instance.sh from the checkout once")
    repo = Path(value).expanduser()
    if not (repo / ".git").exists():
        raise InstanceError(f"the recorded source checkout is not a Git repository: {repo}")
    return repo


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, text=True)
    if check and result.returncode != 0:
        raise InstanceError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _ref(repo: Path, ref: str) -> str | None:
    value = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    return value or None


def _read_revision(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return value or None


def dev_revision() -> str | None:
    """The commit the development instance was last deployed from."""
    return _read_revision(dev_runtime_dir() / "source-revision")


def dev_python() -> Path:
    """The development runtime's interpreter, from its manifest."""
    runtime = dev_runtime_dir()
    try:
        manifest = json.loads((runtime / "runtime-manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InstanceError("the development instance is not deployed; run `openprogram dev deploy`") from None
    return runtime / manifest["python"]


def auth_url() -> str:
    """A sign-in link for the development instance's current worker.

    The worker mints a new owner token every time it starts, so a browser
    signed in before a deploy has to sign in again with a fresh link.
    """
    result = subprocess.run(
        [str(dev_python()), "-I", "-B", "-m", "openprogram", "web", "auth-url",
         "--base-url", f"http://127.0.0.1:{DEV_PORT}"],
        capture_output=True, text=True, env={**os.environ, "OPENPROGRAM_PROFILE": DEV_PROFILE})
    url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if result.returncode != 0 or not url.startswith("http"):
        raise InstanceError(result.stderr.strip() or "the development instance gave no sign-in link; is it running?")
    return url


def stable_revision() -> str | None:
    """The commit the installed App was last refreshed from."""
    return _read_revision(APP_PATH / "Contents" / "Resources" / "openprogram-source-revision")


def healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def _subjects(repo: Path, base: str | None, head: str | None) -> list[dict]:
    if not head:
        return []
    span = f"{base}..{head}" if base else head
    out = _git(repo, "log", "--format=%H%x1f%s", "--max-count=200", span, check=False)
    return [dict(zip(("sha", "subject"), line.split("\x1f", 1)))
            for line in out.splitlines() if "\x1f" in line]


def status() -> dict:
    """Both instances, and what promoting the development one would bring."""
    repo = source_repo()
    stable_ref = _ref(repo, STABLE_BRANCH)
    dev = dev_revision()
    return {
        "source_repo": str(repo),
        "stable": {
            "branch": STABLE_BRANCH,
            "branch_revision": stable_ref,
            "installed_revision": stable_revision(),
            "port": STABLE_PORT,
            "healthy": healthy(STABLE_PORT),
        },
        "dev": {
            "branch": DEV_BRANCH,
            "branch_revision": _ref(repo, DEV_BRANCH),
            "deployed_revision": dev,
            "port": DEV_PORT,
            "url": f"http://127.0.0.1:{DEV_PORT}",
            "healthy": healthy(DEV_PORT),
        },
        # Commits stable would gain by promoting what dev serves now.
        "promotable": _subjects(repo, stable_ref, dev) if dev and not dev.endswith("-dirty") else [],
    }


# Credentials the development instance may share. An API key is static, and an
# imported login reads another tool's login by reference. OAuth logins are not
# copied: their refresh tokens rotate, so two instances refreshing one login
# would sign each other out.
SHAREABLE_KINDS = frozenset({"api_key", "import"})
MODEL_CONFIG_KEYS = ("api_keys", "providers", "last_used_model")


def copy_models() -> dict:
    """Give the development instance the stable instance's model setup.

    Copies provider settings and the shareable credentials the development
    instance does not hold yet; returns what was copied and which providers'
    sign-ins must be signed in again there. The stable instance's files are
    only read: its store is opened without the carry-over, which is the
    stable instance's own to run.
    """
    from openprogram.models.auth.store import AuthStore

    source = AuthStore(root=stable_state_dir(), carry_over=False)
    target = AuthStore(root=dev_state_dir())
    copied, skipped = [], []
    for provider in source.providers():
        stored = source.stored(provider).credentials
        shareable = [c for c in stored if c.kind in SHAREABLE_KINDS]
        if len(shareable) < len(stored):
            skipped.append(provider)
        held = target.stored(provider).credentials
        ids = {c.id for c in held}
        secrets = {c.payload.auth_value for c in held if c.payload.auth_value}
        added = 0
        for cred in shareable:
            if cred.id in ids or (cred.payload.auth_value and cred.payload.auth_value in secrets):
                continue
            target.add_credential(cred)
            added += 1
        if added:
            copied.append(provider)

    config_path = dev_state_dir() / "config.json"
    stable_config = json.loads((stable_state_dir() / "config.json").read_text(encoding="utf-8"))
    try:
        dev_config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        dev_config = {}
    for key in MODEL_CONFIG_KEYS:
        if key in stable_config:
            dev_config[key] = stable_config[key]
    staged = config_path.with_suffix(".json.incoming")
    staged.write_text(json.dumps(dev_config, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(staged, 0o600)
    replace_file(staged, config_path)
    return {"copied": copied, "oauth_not_copied": skipped}


def _managed_worktree(repo: Path, path: Path, revision: str) -> Path:
    """A clean detached worktree at ``revision``; ignored build caches stay."""
    if not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        _git(repo, "worktree", "add", "--detach", str(path), revision)
        return path
    _git(path, "checkout", "--force", "--detach", revision)
    _git(path, "clean", "-fd")
    return path


def _run(script: Path, cwd: Path, env: dict | None = None) -> None:
    """Run ``script``; a ``None`` value in ``env`` removes that variable."""
    merged = {**os.environ, **(env or {})}
    result = subprocess.run(["bash", str(script)], cwd=str(cwd),
                            env={key: value for key, value in merged.items() if value is not None})
    if result.returncode != 0:
        raise InstanceError(f"{script.name} failed (exit {result.returncode})")


def deploy_dev(ref: str = DEV_BRANCH) -> str:
    """Build ``ref`` into the development runtime and restart its worker."""
    repo = source_repo()
    revision = _ref(repo, ref)
    if not revision:
        raise InstanceError(f"unknown ref: {ref}")
    tree = _managed_worktree(repo, dev_state_dir() / "source", revision)
    _run(tree / "scripts" / "local-instances" / "refresh-dev-instance.sh", tree)
    return revision


@contextmanager
def _stable_profile() -> Iterator[None]:
    """Read the stable instance's stores, whichever profile called promotion."""
    from openprogram.state.paths import get_active_profile, set_active_profile

    caller = get_active_profile()
    set_active_profile(None)
    try:
        yield
    finally:
        set_active_profile(caller)


def _inside_worker(worker_pid: int | None) -> bool:
    """Whether this process descends from ``worker_pid``, as a chat's shell command does."""
    if worker_pid is None:
        return False
    try:
        table = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    parents = {int(pid): int(parent) for pid, parent in (line.split() for line in table.splitlines() if line.strip())}
    current, seen = os.getpid(), set()
    while current in parents and current not in seen:
        if current == worker_pid:
            return True
        seen.add(current)
        current = parents[current]
    return False


def _stable_worker_pid() -> int | None:
    """The live stable worker's pid; the lock file alone can outlive a crash."""
    from openprogram.operations.worker.lock import is_held_by, read_holder_pid

    pid = read_holder_pid()
    return pid if pid is not None and is_held_by(pid) else None


def _named(work: list[str]) -> str:
    extra = len(work) - _NAMED_WORK_LIMIT
    return "; ".join(work[:_NAMED_WORK_LIMIT]) + (f"; and {extra} more" if extra > 0 else "")


def _wait_until_stable_quiet(on_wait: Callable[[str], None] | None = None) -> None:
    """Return once nothing is running on stable; a restart would interrupt it."""
    import time

    from openprogram.operations.self_update.control.supervisor import _wait_for_quiescence, active_work

    try:
        with _stable_profile():
            worker = _stable_worker_pid()
            if worker is None:
                # No restart can interrupt anything, and rows a crashed
                # worker left "running" must not block the promotion that
                # repairs it: they only clear when a worker starts.
                return
            work = active_work()
            if not work:
                return
            from_worker = _inside_worker(worker)
            seconds = STABLE_QUIET_WAIT_FROM_WORKER_SECONDS if from_worker else STABLE_QUIET_WAIT_SECONDS
            if on_wait:
                on_wait(f"waiting up to {seconds} s for the stable instance to finish: {_named(work)}")
            if _wait_for_quiescence(time.time() + seconds):
                return
            work = active_work()
    except Exception as exc:  # noqa: BLE001 - unknown is not quiet; the owner gets a refusal, not a traceback.
        raise InstanceError(f"could not tell whether the stable instance is busy: {exc}") from exc
    if not work:
        return
    hint = ("this command runs inside the stable worker, so a conversation's own turn counts as running; "
            "run it from outside the stable App or use the self_update tool"
            if from_worker else "let that work finish or stop it, then promote again")
    raise StableBusyError(f"the stable instance is still busy after {seconds} s, so nothing was promoted: "
                          f"{_named(work)}; {hint}")


def promote(revision: str | None = None, *, wait_for_stable: bool = True,
            on_wait: Callable[[str], None] | None = None) -> str:
    """Fast-forward ``stable`` to what dev serves and install it into the App.

    Refused unless the commit is exactly the one the development instance
    is serving, that instance is healthy, and the move is a fast-forward.
    The refresh restarts the stable worker, so it then waits, bounded, for
    stable to go quiet, refuses by naming what still runs, and checks the
    conditions again since the wait can outlast them.
    ``wait_for_stable=False`` is for the self-update controller alone: it has
    already waited, and a repair after a failed promotion must not wait on
    stable's work. The stable profile is backed up next.

    ``scripts/local-instances/refresh-local-app.sh`` installs ``target`` and moves ``stable``
    itself, compare-and-swap from ``current``, only after the new worker is
    healthy. Everything that can fail runs before it stops the old worker,
    and a failure after that puts the previous App and worker back, so a
    failed promotion leaves stable running what it ran before. It is not
    retried: a second run would only repeat the same failure.
    """
    repo = source_repo()
    target, current = _promotion_target(repo, revision)
    if wait_for_stable:
        _wait_until_stable_quiet(on_wait)
        target, current = _promotion_target(repo, target)

    from openprogram.operations.backup import create_backup
    with _stable_profile():
        archive = create_backup(label="pre-promote")
    if archive:
        prune_promote_backups(Path(archive).parent)

    tree = _managed_worktree(repo, stable_state_dir() / "release-source", target)
    # The development instance already built this commit; the refresh reuses
    # that build when its recorded revision matches. Without a session id the
    # detached refresh is waited for, so its outcome is known here.
    env = {"OPENPROGRAM_PREBUILT_DIR": str(dev_artifacts_dir()),
           "OPENPROGRAM_PROMOTE_FROM": current or "",
           "OPENPROGRAM_SESSION_ID": None}
    try:
        _run(tree / "scripts" / "local-instances" / "refresh-local-app.sh", tree, env)
    except InstanceError as exc:
        # The refresh moves ``stable`` only once the new worker is healthy, so
        # a failure after that (the App window not reopening) is no failed
        # promotion: reporting one would have the self-update controller
        # revert a commit that stable is serving.
        if _ref(repo, STABLE_BRANCH) != target:
            raise RefreshFailedError(f"{target[:10]} was not promoted: {exc}; {_stable_now(repo)}") from exc
        sys.stderr.write(f"promoted {target[:10]}, but the refresh then failed: {exc}; {_stable_now(repo)}\n")
    return target


def _stable_now(repo: Path) -> str:
    """One line on what the stable instance runs, read from the facts."""
    import tempfile

    branch, installed = _ref(repo, STABLE_BRANCH), stable_revision()
    state = "healthy" if healthy(STABLE_PORT) else f"not answering on port {STABLE_PORT}"
    log = Path(tempfile.gettempdir()) / f"openprogram-refresh-{os.getuid()}.log"
    return (f"now stable is {(branch or 'absent')[:10]} and the App runs "
            f"{(installed or 'an unrecorded revision')[:10]}, {state}; log: {log}")


def _promotion_target(repo: Path, revision: str | None) -> tuple[str, str | None]:
    """The commit to promote and the current ``stable`` tip, or the reason not to."""
    served = dev_revision()
    if not served or served.endswith("-dirty"):
        raise InstanceError("the development instance serves no clean commit; deploy one first")
    target = _ref(repo, revision) if revision else served
    if not target:
        raise InstanceError(f"unknown revision: {revision}")
    if target != served:
        raise NotServedError(
            f"only the commit the development instance serves can be promoted ({served[:10]}); "
            f"deploy {target[:10]} to it first")
    if not healthy(DEV_PORT):
        raise InstanceError("the development instance is not healthy; promote after it serves the commit")
    current = _ref(repo, STABLE_BRANCH)
    if current and current != target and subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", current, target]).returncode != 0:
        raise InstanceError(f"{target[:10]} does not contain stable ({current[:10]}); promotion only fast-forwards")
    return target, current


PROMOTE_BACKUPS_KEPT = 5


def prune_promote_backups(directory: Path, keep: int = PROMOTE_BACKUPS_KEPT) -> list[Path]:
    """Delete all but the newest `keep` pre-promote backups (and their locks).

    Every promotion writes a full archive of the stable data; without a
    bound they pile up by the hundred. Other backups are left alone.
    """
    archives = sorted(directory.glob("*-pre-promote-*.tar.gz"), key=lambda path: path.name, reverse=True)
    removed = []
    for archive in archives[keep:]:
        for path in (archive, archive.with_name(archive.name + ".lock")):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            removed.append(path)
    return removed


def as_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
