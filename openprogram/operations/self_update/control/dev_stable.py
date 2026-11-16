"""Self-update on a machine that runs a stable and a development instance.

The development instance is the trial and promotion is the switch. The
controller runs detached, from a private copy of the runtime, because it
replaces both the development runtime and the App while it works and this
codebase imports lazily. See ``docs/reference/design/distribution/dev-instance.html``.

Phases, each resumable from the journal after a crash:

- ``building``: rebase the candidate onto ``dev``, fast-forward the primary
  checkout, deploy to the development instance, and observe it.
- ``ready``: wait for the stable instance to go idle.
- ``activating``: promote exactly that commit to ``stable``.
- ``verifying``: observe the stable instance on the new commit.

``stable`` only ever moves forward. A failed promotion leaves it, and the
App, where they were; the commit is then reverted on ``dev`` with a commit
that is deployed and promoted like any other, so ``dev`` stays promotable.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from typing import Callable

from openprogram.operations.self_update.store import SelfUpdateStore
from openprogram.operations.self_update.types import (
    ConcurrentUpdateError,
    UpdatePhase,
    UpdateRecord,
    is_terminal,
)
from openprogram.state.store.session.files.atomic_file import atomic_write_text

EVIDENCE = "backend:dev_stable"
TIMEOUT_SECONDS = 2 * 3600
JOURNAL = "dev-stable.json"
SCRIPT = "dev-stable-controller.sh"
LOG = "supervisor.log"
LAND_ATTEMPTS = 10
TURN_WAIT_SECONDS = 1800
IDLE_WAIT_SECONDS = 1800
PROMOTED_REFRESH_SECONDS = 1200
HEALTH_WAIT_SECONDS = 180
_REASON_LIMIT = 1000
_FILES_LIMIT = 100

# Environment the controller's build and install steps need. Everything else is
# left behind: OPENPROGRAM_SESSION_ID in particular would make
# scripts/local-instances/refresh-local-app.sh detach and report success before it finished.
_CONTROLLER_ENVIRONMENT = (
    "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TMPDIR", "SSH_AUTH_SOCK",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "no_proxy", "all_proxy",
    "OPENPROGRAM_UV_BIN",
)
_EXTRA_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")


class LandFailed(RuntimeError):
    """A commit could not reach ``dev``; ``files`` names the ones in the way."""

    def __init__(self, reason: str, files: tuple[str, ...] = ()) -> None:
        super().__init__(reason)
        self.files = files


class _Abort(Exception):
    """The update stops here with nothing further to undo."""

    def __init__(self, reason: str, *, files: tuple[str, ...] = ()) -> None:
        super().__init__(reason)
        self.files = files


def owns(record: UpdateRecord) -> bool:
    """Whether this update runs under this backend."""
    return EVIDENCE in record.request.pre_update_evidence


def result_summary(record: UpdateRecord) -> dict | None:
    """What this backend adds to the status of one of its updates."""
    if not owns(record):
        return None
    detail = record.state.detail
    return {key: detail[key] for key in ("landed_sha", "revert_sha", "reason", "files", "browser_probe") if key in detail}


# --- Git ---------------------------------------------------------------------

def _git(cwd: Path, *args: str, timeout: float = 300) -> subprocess.CompletedProcess:
    command = ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false", "-C", str(cwd), *args]
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "GIT_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0"})


def _rev(cwd: Path, ref: str) -> str:
    result = _git(cwd, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if result.returncode != 0:
        raise LandFailed(f"cannot read {ref}: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def _contains(cwd: Path, ancestor: str, descendant: str) -> bool:
    return _git(cwd, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0


def _unmerged(tree: Path) -> tuple[str, ...]:
    result = _git(tree, "diff", "--name-only", "--diff-filter=U")
    return tuple(line for line in result.stdout.splitlines() if line)


def _abort_rebase(tree: Path) -> None:
    _git(tree, "rebase", "--abort")


def _overlapping_files(output: str) -> tuple[str, ...]:
    """The files git names when local changes block a fast-forward."""
    if "would be overwritten" not in output:
        return ()
    return tuple(line.strip() for line in output.splitlines() if line.startswith("\t"))


def land(repo: Path, tree: Path, *, branch: str = "dev", note: Callable[[str, str], None] = lambda onto, head: None,
         attempts: int = LAND_ATTEMPTS, pause: Callable[[float], None] = time.sleep) -> str:
    """Rebase ``tree``'s HEAD onto ``branch`` and fast-forward the primary checkout.

    ``note(onto, head)`` runs just before each merge, so a crash after it knows
    which commits are this update's. Other sessions commit to ``dev`` all day:
    a moved branch or a held ``index.lock`` is retried, and only a real conflict
    or a dirty file in the way stops. Landing the same commit again is a no-op.
    """
    if _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip() != branch:
        raise LandFailed(f"the primary checkout is not on {branch}")
    _abort_rebase(tree)
    last = ""
    for attempt in range(attempts):
        if attempt:
            pause(min(30, 2 ** attempt))
        tip, head = _rev(repo, branch), _rev(tree, "HEAD")
        if _contains(repo, head, tip):
            return head
        if not _contains(tree, tip, head):
            rebased = _git(tree, "rebase", tip)
            if rebased.returncode != 0:
                files = _unmerged(tree)
                _abort_rebase(tree)
                detail = "" if files else ": " + (rebased.stderr or rebased.stdout).strip()[:300]
                raise LandFailed(f"the candidate does not rebase onto {branch}{detail}", files)
            head = _rev(tree, "HEAD")
            if _contains(repo, head, tip):
                return head
        note(tip, head)
        merged = _git(repo, "merge", "--ff-only", head)
        if merged.returncode == 0:
            return head
        last = (merged.stderr or merged.stdout).strip()
        files = _overlapping_files(merged.stderr + merged.stdout)
        if files:
            raise LandFailed("uncommitted changes in the primary checkout overlap the candidate", files)
        if "index.lock" not in last and "fast-forward" not in last:
            raise LandFailed(f"git merge failed: {last[:300]}")
    raise LandFailed(f"{branch} kept moving or stayed locked: {last[:300]}")


def commit_revert(repo: Path, tree: Path, onto: str, landed: str, *, message: str, branch: str = "dev") -> None:
    """Make one revert commit of ``onto..landed`` in a fresh detached worktree."""
    if tree.exists():
        _git(repo, "worktree", "remove", "--force", str(tree))
        shutil.rmtree(tree, ignore_errors=True)
    added = _git(repo, "worktree", "add", "--detach", str(tree), branch)
    if added.returncode != 0:
        raise LandFailed(f"cannot create the revert worktree: {added.stderr.strip()[:300]}")
    reverted = _git(tree, "revert", "--no-commit", f"{onto}..{landed}")
    if reverted.returncode != 0:
        files = _unmerged(tree)
        raise LandFailed("the candidate no longer reverts cleanly", files)
    committed = _git(tree, "commit", "-m", message)
    if committed.returncode != 0:
        raise LandFailed(f"cannot commit the revert: {(committed.stderr or committed.stdout).strip()[:300]}")


# --- Launching, in the worker ------------------------------------------------

def controller_environment(repo: str, source: dict | None = None) -> dict[str, str]:
    """What the detached controller runs with; never a session or a profile."""
    source = dict(os.environ if source is None else source)
    environment = {key: source[key] for key in _CONTROLLER_ENVIRONMENT if key in source}
    environment.update({key: value for key, value in source.items() if key.startswith("LC_")})
    path = [entry for entry in source.get("PATH", "").split(os.pathsep) if entry]
    environment["PATH"] = os.pathsep.join(dict.fromkeys([*path, *_EXTRA_PATH]))
    environment.setdefault("HOME", str(Path.home()))
    environment["OPENPROGRAM_SOURCE_REPO"] = repo
    return environment


def _script_body(update_id: str, state_root: Path, python: Path, environment: dict[str, str]) -> str:
    arguments = [
        "/usr/bin/env", "-i",
        *(f"{name}={value}" for name, value in sorted(environment.items())),
        str(python), "-I", "-B", "-m", "openprogram.operations.self_update.control.dev_stable",
        "--state-root", str(state_root), update_id,
    ]
    return "#!/bin/sh\nset -eu\nexec " + " ".join(map(shlex.quote, arguments)) + "\n"


def _label(update_id: str) -> str:
    return f"ai.openprogram.self-update.{update_id}"


def _start(update_dir: Path, update_id: str) -> None:
    script, log = update_dir / SCRIPT, update_dir / LOG
    if sys.platform == "darwin":
        from ..delivery.launcher import LaunchError, _launchctl

        label = _label(update_id)
        rc, _ = _launchctl("print", f"gui/{os.getuid()}/{label}")
        verb = ("kickstart", f"gui/{os.getuid()}/{label}") if rc == 0 else (
            "submit", "-l", label, "-o", str(log), "-e", str(log), "--", str(script))
        rc, message = _launchctl(*verb)
        if rc != 0:
            raise LaunchError(f"launchctl {verb[0]} failed ({rc}): {message}")
        return
    with log.open("ab") as stream:
        subprocess.Popen(["/bin/sh", str(script)], stdin=subprocess.DEVNULL, stdout=stream,
                         stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)


def submit(store: SelfUpdateStore, update_id: str, *, worktree: Path) -> None:
    """Freeze the controller's runtime, record the worktree, and start it."""
    from ..delivery.controller_bundle import clone_private_runtime, running_runtime

    record = store.load(update_id)
    update_dir = store.root / update_id
    python = clone_private_runtime(update_dir, running_runtime())
    store._write_json(update_dir / JOURNAL, {"schema": 1, "worktree": str(worktree), "python": str(python)})
    script = update_dir / SCRIPT
    atomic_write_text(script, _script_body(update_id, store.root, python, controller_environment(record.request.repo)))
    script.chmod(0o700)
    _start(update_dir, update_id)


def _controller_running(update_dir: Path) -> bool:
    from .supervisor import _controller_lock

    with _controller_lock(update_dir) as acquired:
        return not acquired


def ensure_running(store: SelfUpdateStore, record: UpdateRecord) -> None:
    """Called at worker start: a worker restart must not lose a live update.

    Never raises, because turns and the scheduler do not wait for this update.
    """
    import logging

    update_id, update_dir = record.request.update_id, store.root / record.request.update_id
    try:
        if is_terminal(record.state.phase) or _controller_running(update_dir):
            return
        if not (update_dir / SCRIPT).exists():
            if record.state.phase in {UpdatePhase.PREPARING, UpdatePhase.BUILDING}:
                store.transition(update_id, UpdatePhase.ABORTED, expected_phase=record.state.phase,
                                 detail={**record.state.detail, "reason": "the controller was never started"})
            return
        _start(update_dir, update_id)
    except Exception:
        logging.getLogger(__name__).exception("Could not restart the self-update controller")


# --- The controller ----------------------------------------------------------

def _log(message: str) -> None:
    print(f"[self-update {time.strftime('%H:%M:%S')}] {message}", flush=True)


def _targets():
    from openprogram.operations.worker import dev_instance
    from ..verification.system_probe import ProbeTarget

    return (ProbeTarget(port=dev_instance.DEV_PORT, state_dir=dev_instance.dev_state_dir()),
            ProbeTarget(port=dev_instance.STABLE_PORT, state_dir=dev_instance.stable_state_dir()))


class _Controller:
    def __init__(self, store: SelfUpdateStore, update_id: str) -> None:
        from openprogram.operations.worker import dev_instance

        self.store, self.update_id = store, update_id
        self.update_dir = store.root / update_id
        self.instances = dev_instance
        self.journal_path = self.update_dir / JOURNAL
        self.journal = store._read_json(self.journal_path)
        self.worktree = Path(self.journal["worktree"])
        record = store.load(update_id)
        self.repo = Path(record.request.repo)
        self.dev_target, self.stable_target = _targets()

    # Durable state

    def _record(self) -> UpdateRecord:
        return self.store.load(self.update_id)

    def _note(self, **values) -> None:
        self.journal.update(values)
        self.store._write_json(self.journal_path, self.journal)

    def _move(self, target: UpdatePhase, **extra) -> None:
        record = self._record()
        self.store.transition(self.update_id, target, expected_phase=record.state.phase,
                              detail={**record.state.detail, **extra})
        _log(f"{record.state.phase.value} -> {target.value}")

    def _end(self, target: UpdatePhase, reason: str, **extra) -> None:
        files = extra.pop("files", ())
        values = {"reason": reason[:_REASON_LIMIT], **extra}
        if files:
            values["files"] = list(files)[:_FILES_LIMIT]
        self._move(target, **values)

    # Observation

    def _probe_until(self, target, *, revision: str | None):
        from ..verification import system_probe

        deadline = time.monotonic() + HEALTH_WAIT_SECONDS
        record = self._record()
        while True:
            try:
                return system_probe.probe_instance(record, target, revision=revision)
            except system_probe.SystemProbeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(3)

    def _dev_probe(self, sha: str):
        from ..verification import system_probe

        target = replace(self.dev_target, doctor_baseline=self.journal.get("dev_doctor_baseline", {}))
        gate = self._probe_until(target, revision=sha)
        runtime = self.update_dir / "controller" / "runtime"
        browsers = runtime / "assets" / "playwright"
        outcome = system_probe.probe_browser(target, python=sys.executable,
                                             browsers=browsers if browsers.is_dir() else None)
        self._note(browser_probe=outcome)
        return gate

    # Phases

    def _wait_for_turn(self) -> None:
        deadline = time.time() + TURN_WAIT_SECONDS
        while self._record().state.phase is UpdatePhase.PREPARING:
            if time.time() >= deadline:
                raise _Abort("the turn that requested this update never finished")
            time.sleep(0.2)

    def _land(self) -> str:
        try:
            landed = land(self.repo, self.worktree, note=lambda onto, head: self._note(onto=onto, landed=head))
        except LandFailed as exc:
            raise _Abort(str(exc), files=exc.files) from exc
        self._note(landed=landed)
        return landed

    def _deploy(self, sha: str) -> None:
        if self.instances.dev_revision() == sha and self.instances.healthy(self.instances.DEV_PORT):
            return
        self.instances.deploy_dev(sha)

    def _staging(self) -> None:
        from ..verification import system_probe

        landed = self._land()
        self._require_phase(UpdatePhase.BUILDING)
        if "dev_doctor_baseline" not in self.journal:
            self._note(dev_doctor_baseline=system_probe.doctor_baseline(self._record(), self.dev_target))
        try:
            self._deploy(landed)
            gate = self._dev_probe(landed)
        except (self.instances.InstanceError, system_probe.SystemProbeError) as exc:
            raise _Abort(f"{landed[:10]} is on dev, but the development instance did not take it: {exc}") from exc
        self._move(UpdatePhase.READY, landed_sha=landed, development_system_gate=gate,
                   browser_probe=self.journal.get("browser_probe"))

    def _require_phase(self, phase: UpdatePhase) -> None:
        if self._record().state.phase is not phase:
            raise ConcurrentUpdateError(f"the update left {phase.value}")

    def _wait_for_idle(self) -> None:
        from .supervisor import _wait_for_quiescence

        deadline = time.time() + IDLE_WAIT_SECONDS
        while not _wait_for_quiescence(min(time.time() + 20, deadline)):
            self._require_phase(UpdatePhase.READY)
            if time.time() >= deadline:
                raise _Abort("the stable instance stayed busy for 30 minutes; the commit is on dev, not on stable")

    def _ready(self) -> None:
        from ..verification import system_probe

        self._wait_for_idle()
        self._require_phase(UpdatePhase.READY)
        self._note(stable_doctor_baseline=system_probe.doctor_baseline(self._record(), self.stable_target))
        self._move(UpdatePhase.ACTIVATING)

    def _already_live(self, sha: str) -> bool:
        """Whether nothing is left to promote: the App serves it, or a later promotion is carrying it."""
        stable = _rev(self.repo, self.instances.STABLE_BRANCH)
        if not _contains(self.repo, sha, stable):
            return False
        installed = self.instances.stable_revision()
        return self._promoted_past(sha, stable) or (
            installed is not None and _contains(self.repo, sha, installed.removesuffix("-dirty")))

    def _promoted_past(self, sha: str, stable: str | None = None) -> bool:
        """Whether another session's promotion moved ``stable`` beyond this commit."""
        tip = stable or _rev(self.repo, self.instances.STABLE_BRANCH)
        return tip != sha and _contains(self.repo, sha, tip)

    def _promote(self, sha: str) -> None:
        """Promote exactly ``sha``, redeploying once if another deploy took its place.

        The ``ready`` phase already waited for stable, and a repair must not wait on it.
        """
        if self._already_live(sha):
            return
        try:
            self.instances.promote(sha, wait_for_stable=False)
        except self.instances.NotServedError:
            self._note(redeployed=sha)
            self.instances.deploy_dev(sha)
            self._dev_probe(sha)
            self.instances.promote(sha, wait_for_stable=False)

    def _activating(self, sha: str) -> None:
        try:
            self._promote(sha)
        except self.instances.RefreshFailedError as exc:
            self._repair_forward(sha, str(exc))
            return
        except self.instances.InstanceError as exc:
            # A promotion that overtook this one refuses it, and has carried the commit anyway.
            if not self._promoted_past(sha):
                raise _Abort(f"{sha[:10]} is on dev but was not promoted: {exc}") from exc
        self._move(UpdatePhase.VERIFYING)
        self._verifying(sha)

    def _verifying(self, sha: str) -> None:
        from ..verification import system_probe

        target = replace(self.stable_target, doctor_baseline=self.journal.get("stable_doctor_baseline", {}))
        record = self._record()
        started = time.monotonic()
        while True:
            try:
                gate = system_probe.probe_instance(record, target, revision=None)
            except system_probe.SystemProbeError as exc:
                problem = str(exc)
            else:
                if _contains(self.repo, sha, gate["candidate_sha"].removesuffix("-dirty")):
                    self._move(UpdatePhase.SUCCEEDED, landed_sha=sha, committed_system_gate=gate)
                    return
                problem = "the stable instance is not serving the promoted commit"
            # Another session's promotion moved ``stable`` once its worker
            # was healthy, but that worker may still be settling, so neither
            # the old worker answering nor no worker answering says this
            # commit is bad yet.
            limit = PROMOTED_REFRESH_SECONDS if self._promoted_past(sha) else HEALTH_WAIT_SECONDS
            if time.monotonic() - started >= limit:
                break
            time.sleep(3)
        self._repair_forward(sha, f"the stable instance did not come up healthy: {problem}")

    def _repair_forward(self, sha: str, reason: str) -> None:
        """Undo a failed promotion with a revert, because ``stable`` never moves back."""
        _log(f"repairing forward: {reason}")
        try:
            if "revert_sha" not in self.journal and self._promoted_past(sha):
                # Reverting would take a commit back out of a promotion that already carries it.
                self._end(UpdatePhase.NEEDS_MANUAL_RECOVERY, f"{sha[:10]} is on stable through a later promotion, "
                          f"so it was not reverted: {reason}", landed_sha=sha)
                return
            revert = self._make_revert(sha, reason)
            self._note(revert_sha=revert)
            self._deploy(revert)
            self._promote(revert)
            target = replace(self.stable_target, doctor_baseline=self.journal.get("stable_doctor_baseline", {}))
            self._probe_until(target, revision=None)
        except Exception as exc:  # noqa: BLE001 - whatever stopped the repair goes to the owner.
            files = getattr(exc, "files", ())
            self._end(UpdatePhase.NEEDS_MANUAL_RECOVERY, f"{reason}; the repair also failed: {exc}",
                      landed_sha=sha, files=files, **({"revert_sha": self.journal["revert_sha"]} if "revert_sha" in self.journal else {}))
            return
        self._end(UpdatePhase.ROLLED_BACK, f"{reason}; reverted on dev by {revert[:10]} and promoted",
                  landed_sha=sha, revert_sha=revert)

    def _make_revert(self, sha: str, reason: str) -> str:
        if "revert_sha" in self.journal:
            return self.journal["revert_sha"]
        tree = self.update_dir / "revert-tree"
        if not (tree / ".git").exists() or "revert_tree_ready" not in self.journal:
            if "onto" not in self.journal:
                raise LandFailed("this update's commits on dev are not recorded, so they cannot be reverted")
            subject = self._record().request.goal.splitlines()[0][:120]
            commit_revert(self.repo, tree, self.journal["onto"], sha,
                          message=f"Reverted a self-update that did not reach stable: {subject}\n\n{reason[:400]}\n")
            self._note(revert_tree_ready=True)
        revert = land(self.repo, tree)
        _git(self.repo, "worktree", "remove", "--force", str(tree))
        return revert

    def advance(self) -> None:
        record = self._record()
        try:
            if record.state.phase is UpdatePhase.PREPARING:
                self._wait_for_turn()
            if self._record().state.phase is UpdatePhase.BUILDING:
                self._staging()
            if self._record().state.phase is UpdatePhase.READY:
                self._ready()
            phase = self._record().state.phase
            sha = self.journal.get("landed", "")
            if phase is UpdatePhase.ACTIVATING:
                self._activating(sha)
            elif phase is UpdatePhase.VERIFYING:
                self._verifying(sha)
        except _Abort as stop:
            self._end(UpdatePhase.ABORTED, str(stop), files=stop.files)
        except ConcurrentUpdateError:
            _log("the update was cancelled or finished elsewhere")
        except Exception as exc:  # noqa: BLE001 - every failure must reach the conversation.
            self._unexpected(exc)

    def _unexpected(self, error: Exception) -> None:
        _log(f"unexpected failure: {type(error).__name__}: {error}")
        phase = self._record().state.phase
        reason = f"{type(error).__name__}: {error}"
        sha = self.journal.get("landed", "")
        if phase in {UpdatePhase.PREPARING, UpdatePhase.BUILDING, UpdatePhase.READY}:
            self._end(UpdatePhase.ABORTED, reason)
        elif phase is UpdatePhase.ACTIVATING and (not sha or not _contains(self.repo, sha, _rev(self.repo, "stable"))):
            self._end(UpdatePhase.ABORTED, reason)
        elif phase in {UpdatePhase.ACTIVATING, UpdatePhase.VERIFYING}:
            self._repair_forward(sha, reason)


def _retire(update_id: str) -> None:
    """Remove this update's launchd job; ``launchctl submit`` keeps it alive otherwise."""
    if sys.platform != "darwin":
        return
    from ..delivery.launcher import _launchctl
    _launchctl("remove", _label(update_id))


def run(update_id: str, state_root: Path) -> int:
    from .supervisor import _controller_lock

    store = SelfUpdateStore(Path(state_root))
    with _controller_lock(store.root / update_id) as acquired:
        if not acquired:
            return 0
        if not is_terminal(store.load(update_id).state.phase):
            _Controller(store, update_id).advance()
    if is_terminal(store.load(update_id).state.phase):
        _retire(update_id)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Land, deploy and promote one OpenProgram self-update")
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("update_id")
    args = parser.parse_args(argv)
    return run(args.update_id, args.state_root)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["EVIDENCE", "LandFailed", "TIMEOUT_SECONDS", "commit_revert", "controller_environment",
           "ensure_running", "land", "main", "owns", "result_summary", "run", "submit"]
