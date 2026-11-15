"""Take one change to a sealed version and switch to it, from the old version.

This module runs out of process, from the version that was current when the
update was prepared, so a changed updater applies only from the next switch.
Everything it does to the outside world goes through a :class:`LiveHost`.

The phase in ``state.json`` says how far an update got; ``switch.json`` in the
update directory records what happened inside ``activating`` and ``verifying``.
A controller that dies is restarted and carries on from those two files, and
each step is safe to repeat.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any, Iterator

from ..store import SelfUpdateStore, is_candidate_worker
from ..types import ConcurrentUpdateError, UpdatePhase, UpdateRecord, is_terminal
from ..versions import layout as paths
from ..versions import pointer as pointers
from ..versions import store as sealed
from ..versions.layout import Layout
from .held_turns import count as count_held
from .maintenance import enter_maintenance, leave_maintenance
from .rollback_intent import RECOVERY_SECONDS, begin_rollback
from openprogram.host._platform import filesystem

_log = logging.getLogger(__name__)

JOURNAL = "switch.json"
HEARTBEAT = "heartbeat"
# The running shell says where it is here, and is asked to relaunch there.
SHELL_RECORD = "shell.json"
SHELL_RELAUNCH = "shell-relaunch"
SHELL_HANDSHAKE_SECONDS = 30.0
CHECKPOINT_REQUESTED = "checkpoint-requested"
HEARTBEAT_SECONDS = 5.0
HEARTBEAT_STALE_SECONDS = 30.0
TURN_RELEASE_SECONDS = 1800
QUIET_SECONDS = 600
QUIESCE_LIMIT_SECONDS = 1800
MAINTENANCE_SECONDS = 2400
START_SECONDS = 120
MIGRATION_LIMIT_SECONDS = 1800
POINTER_PROBATION_SECONDS = 600
VERIFY_LIMIT_SECONDS = RECOVERY_SECONDS  # 390 s
PROBATION_SECONDS = 120
PROBATION_POLL_SECONDS = 5.0
ROLLBACK_WRITE_SECONDS = 30
MAX_REBASES = 3
_ERROR_LIMIT = 2000


# The stores that are not databases and that a version's migration can rewrite.
SNAPSHOT_FOLDERS = ("projects", "sessions", "functions", "memory", "active")


def _copy_tree(source: Path, target: Path) -> None:
    """Copy a folder, as a copy-on-write clone where the file system can make one."""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(target, ignore_errors=True)  # A resumed switch repeats this, and cp would nest inside an existing copy.
    clone = {"darwin": ["cp", "-c", "-R"]}.get(sys.platform) or (
        ["cp", "--reflink=auto", "-R"] if sys.platform.startswith("linux") else None)
    if clone is not None and subprocess.run([*clone, str(source), str(target)], capture_output=True).returncode == 0:
        return
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target, symlinks=True)


class Failed(RuntimeError):
    """A step failed; the phase it failed in decides what happens next."""


class RebaseConflict(Failed):
    def __init__(self, reason: str, files: tuple[str, ...] = ()) -> None:
        super().__init__(reason)
        self.files = files


WINDOWS_NOTE = ("Windows is unverified for this update path, and has no OS sandbox for a trial: the temporary "
                "data directory, the separate port and the sanitized copy are the only isolation.")


def owns(record: UpdateRecord) -> bool:
    """Whether this update runs under the ``versions`` backend."""
    return bool(record.request.controller_version)


def candidate_version(record: UpdateRecord) -> str:
    return record.request.candidate_version or str(record.state.detail.get("candidate_version") or "")


def candidate_commit(record: UpdateRecord) -> str:
    """The commit actually built: rebasing lands the request under another SHA."""
    return str(record.state.detail.get("landed_sha") or record.request.candidate_sha)


def result_summary(record: UpdateRecord) -> dict | None:
    """What this backend adds to the status of one of its updates."""
    if not owns(record):
        return None
    detail = record.state.detail
    summary = {key: detail[key] for key in (
        "candidate_version", "landed_sha", "rebases", "reason", "turn_check", "held_turns", "backup", "trial",
    ) if key in detail}
    if os.name == "nt":
        summary["windows_note"] = WINDOWS_NOTE
    return summary


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class CommitIncomplete(RuntimeError):
    """A commit step failed. The controller exits abnormally and resumes it."""


class LiveHost:
    """The controller's effects on this machine."""

    def __init__(self, store: SelfUpdateStore, layout: Layout) -> None:
        self.store, self.layout = store, layout
        self.state_dir = store.root.parent

    # Git ----------------------------------------------------------------

    def _git(self, cwd: Path, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false", "-C", str(cwd), *args],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "GIT_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0"})

    def local_tip(self) -> str:
        result = self._git(self.layout.repo, "rev-parse", "--verify", "refs/heads/local^{commit}")
        if result.returncode != 0:
            raise Failed("the local line cannot be read")
        return result.stdout.strip()

    def worktree(self, record: UpdateRecord) -> Path | None:
        from openprogram.state.worktree.manager import get_manager

        if not record.request.task:
            return None
        entry = get_manager().get_worktree(record.request.worktree_id)
        if entry is None:
            raise Failed("the task's worktree is no longer registered")
        return Path(entry.worktree_path)

    def is_based_on(self, record: UpdateRecord, commit: str, base: str) -> bool:
        tree = self.worktree(record)
        return tree is None or self._git(tree, "merge-base", "--is-ancestor", base, commit).returncode == 0

    def rebase(self, record: UpdateRecord, onto: str) -> str:
        tree = self.worktree(record)
        if tree is None:
            raise Failed("this update has no worktree to rebase")
        result = self._git(tree, "rebase", onto, timeout=300)
        if result.returncode != 0:
            files = tuple(line for line in self._git(tree, "diff", "--name-only", "--diff-filter=U").stdout.splitlines() if line)
            self._git(tree, "rebase", "--abort")
            raise RebaseConflict(f"the change does not rebase cleanly onto {onto[:10]}", files)
        return self._git(tree, "rev-parse", "HEAD").stdout.strip()

    def move_local(self, commit: str, expected: str, version: str) -> None:
        """Commit the switch to history; repeating it is harmless."""
        if self.local_tip() != commit:
            result = self._git(self.layout.repo, "update-ref", "refs/heads/local", commit, expected)
            if result.returncode != 0:
                raise Failed(f"the local line did not move: {result.stderr.strip()[:300]}")
        self._pin_version(commit, version)

    def go_back_local(self, update_id: str, commit: str, expected: str, version: str) -> None:
        """``local`` moves off its line (an undo, or a release): the old tip is saved first."""
        from . import undo

        undo.commit_going_back(self.layout, update_id, commit, expected_tip=expected)
        self._pin_version(commit, version)

    def _pin_version(self, commit: str, version: str) -> None:
        result = self._git(self.layout.repo, "update-ref", f"refs/openprogram/version/{version}", commit)
        if result.returncode != 0:
            raise Failed(f"the version ref was not written: {result.stderr.strip()[:300]}")

    def backup_repo(self) -> None:
        """A failed bundle costs a backup, never the switch."""
        directory = self.layout.root / "repo-backups"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.bundle"
        result = self._git(self.layout.repo, "bundle", "create", str(target), "refs/heads/local", timeout=600)
        if result.returncode != 0:
            _log.warning("repository backup failed: %s", result.stderr.strip()[:300])
            target.unlink(missing_ok=True)
        for old in sorted(directory.glob("*.bundle"))[:-3]:
            old.unlink(missing_ok=True)

    # Build and trial ------------------------------------------------------

    def build(self, record: UpdateRecord, commit: str, parent: str) -> str:
        from ..versions.build import BuildRequest, build_version

        built = build_version(self.layout, BuildRequest(
            update_id=record.request.update_id, commit=commit, worktree=self.worktree(record),
            parent=parent or None, built_by=record.request.controller_version,
        ))
        return built.version

    def trial(self, record: UpdateRecord, version: str) -> dict:
        from ..trial.engine import TrialRequest, plan_tests, run_trial
        from ..trial.verifier import run_verifier

        live = self._target()

        def verify(instance, baseline) -> dict:
            current = self.store.load(record.request.update_id)
            return run_verifier(self.store, current, instance, live=live, host_pid=instance.process.pid,
                                doctor_baseline=baseline)

        # The verifier is a Job in the conversation that asked for the change;
        # an official release has no such conversation, so it skips this check.
        conversational = (self.store.root / record.request.update_id / "verifier-config.json").is_file()
        shell = self._running_shell()
        result = run_trial(TrialRequest(
            layout=self.layout, record=record, store=self.store, candidate=version,
            previous=pointers.read_or_recover(self.layout).version, live_state=self.state_dir,
            worktree=self.worktree(record), tests=plan_tests(self.store, record) if conversational else (),
            verifier=verify if conversational else None,
            shell_executable=None if shell is None else Path(str(shell.get("executable"))),
        ))
        if not result.passed:
            raise Failed(f"{result.failed_check}: {result.reason}")
        return {"isolation": result.isolation, "checks": list(result.checks), "notes": list(result.notes),
                "evidence": str(result.evidence) if result.evidence else None}

    # Tasks ----------------------------------------------------------------

    def wait_idle(self, until: float) -> bool:
        from .supervisor import _wait_for_quiescence

        return _wait_for_quiescence(until)

    def request_checkpoint(self, record: UpdateRecord) -> None:
        """Ask the worker to pause what is running; it does so at its next pass."""
        path = self.store.root / record.request.update_id / CHECKPOINT_REQUESTED
        self.store._write_json(path, {"schema": 1, "at": time.time()})

    # Probes ---------------------------------------------------------------

    def _target(self, baseline: dict[str, bool] | None = None):
        from openprogram.host.security.backend_endpoint import read_active_web_access
        from ..verification.system_probe import ProbeTarget

        return ProbeTarget(port=read_active_web_access(self.state_dir).port, state_dir=self.state_dir,
                           doctor_baseline={} if baseline is None else baseline)

    def _subsystems(self, observation: dict) -> dict:
        body = json.loads(observation["body"])
        value = body.get("subsystems")
        return value if isinstance(value, dict) else {}

    def probe_old(self, record: UpdateRecord) -> dict:
        """The old version's reference. Only health or identity failing stops the update."""
        from ..verification.system_probe import SystemProbeError, _probe_system, doctor_baseline

        target = self._target()
        try:
            probe = _probe_system(record, None, 60, "/api/diagnostics", 1_048_576, target=target)
            doctor = doctor_baseline(record, target)
        except SystemProbeError as exc:
            if str(exc).rsplit(": ", 1)[-1] in {"owner_auth", "identity", "health"}:
                raise Failed(f"the running version does not answer, so a rollback would have nothing to return to: {exc}") from exc
            probe = _probe_system(record, None, 60, "/api/diagnostics", 1_048_576, target=target, through_health=True)
            doctor = {}
        return {"gate": probe["system_gate"], "doctor": doctor, "subsystems": self._subsystems(probe["observation"])}

    def stop_worker(self) -> None:
        from openprogram.operations.worker.lifecycle import stop_worker

        if stop_worker(prefer_service=True) != 0:
            raise Failed("the worker did not stop")

    def start_worker(self) -> None:
        from ..delivery.controller import worker_environment
        from openprogram.operations.worker.lifecycle import spawn_detached

        if spawn_detached(prefer_service=True, environment=worker_environment(store=self.store)) != 0:
            raise Failed("the worker did not start")

    def snapshot_databases(self, update_id: str) -> str:
        """Copy every store a migration can rewrite, before a destructive migration runs.

        Conversations and run state are log files, not databases, so the
        folders and the run logs beside them are copied too; each database is
        then replaced by a consistent backup.
        """
        from openprogram.runtime.execution.store.run_tables import RunLogFiles

        target = self.state_dir / "backups" / f"pre-switch-{update_id}"
        target.mkdir(parents=True, exist_ok=True)
        for name in SNAPSHOT_FOLDERS:
            if (self.state_dir / name).is_dir():
                _copy_tree(self.state_dir / name, target / name)
        run_log = RunLogFiles(self.state_dir)
        for source in (run_log.global_path, run_log.index_path):
            if source.is_file():
                shutil.copy2(source, target / source.name)
        backups = self.state_dir / "backups"
        for source in self.state_dir.rglob("*.db"):
            if backups in source.parents or self.layout.root in source.parents:
                continue
            relative = source.relative_to(self.state_dir)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as origin, sqlite3.connect(destination) as copy:
                origin.backup(copy)
            for suffix in ("-wal", "-shm"):
                Path(f"{destination}{suffix}").unlink(missing_ok=True)
        config = self.state_dir / "config.json"
        if config.is_file():
            shutil.copy2(config, target / "config.json")
        return str(target)

    def is_up(self, record: UpdateRecord, commit: str) -> int | None:
        """The candidate worker's pid once it answers with this identity."""
        from openprogram.host.security.backend_endpoint import OwnerAuthError
        from ..verification.system_probe import SystemProbeError, _probe_system

        try:
            gate = _probe_system(record, commit, 20, require_loaded=True, target=self._target(), through_health=True)
        except (SystemProbeError, OwnerAuthError, OSError, ValueError):
            return None  # A worker still starting has not written its access file yet.
        return gate["worker_pid"]

    def migrating(self, version: str) -> bool:
        """Whether a store is still migrating: its marker moved in the last 15 s."""
        try:
            return time.time() - (self.layout.boot / f"{version}.migrating").stat().st_mtime < 15
        except OSError:
            return False

    def probe_candidate(self, record: UpdateRecord, version: str, commit: str, reference: dict) -> dict:
        """Observe the candidate under the regression rule."""
        from ..verification.system_probe import _probe_system

        probe = _probe_system(record, commit, 60, "/api/diagnostics", 1_048_576, require_loaded=True,
                              target=self._target(reference.get("doctor") or {}))
        body = json.loads(probe["observation"]["body"])
        if body.get("runtime_version") not in (None, version):
            raise Failed(f"the running version is {body.get('runtime_version')}, not {version}")
        self.compare_subsystems(reference, self._subsystems(probe["observation"]))
        return probe["system_gate"]

    @staticmethod
    def compare_subsystems(reference: dict, current: dict) -> None:
        for name, value in (reference.get("subsystems") or {}).items():
            if isinstance(value, dict) and value.get("status") == "running":
                state = current.get(name)
                if not isinstance(state, dict) or state.get("status") != "running":
                    raise Failed(f"{name} stopped running after the switch")

    def probe_restored(self, record: UpdateRecord, revision: str, reference: dict) -> dict:
        from ..verification.system_probe import probe_instance

        return probe_instance(record, self._target(reference.get("doctor") or {}), revision=revision, timeout=60)

    def turn_check(self, record: UpdateRecord) -> str:
        """A real Agent turn on the candidate, run by its worker."""
        from ..verification.system_probe import post_worker

        body = post_worker(f"/api/self-updates/{record.request.update_id}/turn-check", self._target(), timeout=180)
        outcome = body.get("outcome")
        if outcome == "failed":
            raise Failed(f"the agent loop failed on the new version: {str(body.get('error'))[:500]}")
        return "ok" if outcome == "ok" else "inconclusive"

    def probation(self, record: UpdateRecord, version: str, commit: str, pid: int, reference: dict, until: float) -> None:
        """Hold the candidate under watch until ``until``."""
        from ..verification.system_probe import _probe_system

        while time.time() < until:
            if pointers.read(self.layout).version != version:
                raise Failed("the pointer left the candidate during probation")
            probe = _probe_system(record, commit, 30, "/api/diagnostics", 1_048_576, require_loaded=True,
                                  target=self._target(), through_health=True)
            if probe["system_gate"]["worker_pid"] != pid:
                raise Failed("the worker restarted during probation")
            self.compare_subsystems(reference, self._subsystems(probe["observation"]))
            time.sleep(min(PROBATION_POLL_SECONDS, max(0.0, until - time.time())))

    def _running_shell(self) -> dict | None:
        """What the open desktop shell recorded about itself; ``None`` when none is open."""
        from openprogram.host._platform.processes import process_alive

        try:
            shell = _read_json(self.layout.root / SHELL_RECORD)
            return shell if isinstance(shell.get("pid"), int) and process_alive(shell["pid"]) else None
        except (OSError, ValueError, AttributeError):
            return None

    def shell_handshake(self, record: UpdateRecord, version: str) -> None:
        """Relaunch the open shell on the new desktop code and wait for it to load."""
        failed = self.layout.boot / f"{version}.desktop-failed"
        if failed.exists():
            raise Failed("the desktop shell could not load the new version")
        if self._running_shell() is None:
            return  # A CLI install, or the App is closed: nothing to relaunch.
        ready = self.layout.boot / f"{version}.desktop-ready"
        ready.unlink(missing_ok=True)
        request = self.layout.root / SHELL_RELAUNCH
        request.write_text(f"{version}\n", encoding="ascii")
        # The App shows its window again when it has loaded, which activates
        # it. A user who is in another application keeps the front.
        from openprogram.host._platform import front
        front.watch_for(front.bundles(*front.APP_BUNDLES), SHELL_HANDSHAKE_SECONDS)
        deadline = time.monotonic() + SHELL_HANDSHAKE_SECONDS
        try:
            while time.monotonic() < deadline:
                if failed.exists():
                    raise Failed("the desktop shell could not load the new version")
                try:
                    handshake = _read_json(ready)
                except (OSError, ValueError):
                    handshake = {}
                if handshake.get("main_ready") and handshake.get("window_loaded"):
                    return
                time.sleep(0.5)
        finally:
            request.unlink(missing_ok=True)
        raise Failed("the desktop shell did not load the new version within 30 seconds")

    def retention(self) -> None:
        from ..versions import retention

        retention.run(self.layout, self.state_dir)

    def note_recovery(self, line: str) -> None:
        with open(self.layout.recovery_log, "a", encoding="utf-8") as stream:
            stream.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {line}\n")


class SwitchController:
    """Drive one update from its durable phase to a terminal phase."""

    def __init__(self, store: SelfUpdateStore, update_id: str, host: Any, layout: Layout) -> None:
        self.store, self.update_id, self.host, self.layout = store, update_id, host, layout
        self.directory = store.root / update_id
        self.journal_path = self.directory / JOURNAL

    # Durable state ---------------------------------------------------------

    def _record(self) -> UpdateRecord:
        return self.store.load(self.update_id)

    def _journal(self) -> dict:
        try:
            return _read_json(self.journal_path)
        except (OSError, ValueError):
            return {}

    def _note(self, **changes: Any) -> dict:
        journal = {**self._journal(), **changes}
        self.store._write_json(self.journal_path, journal)
        return journal

    def _move(self, target: UpdatePhase, **changes: Any) -> None:
        record = self._record()
        self.store.transition(self.update_id, target, expected_phase=record.state.phase,
                              detail={**record.state.detail, **changes})

    def _end(self, target: UpdatePhase, reason: str, **changes: Any) -> None:
        self._move(target, reason=reason[:_ERROR_LIMIT], error=reason[:_ERROR_LIMIT], **changes)

    def _reopen_admission(self) -> None:
        leave_maintenance(self.update_id)
        (self.layout.root / "switching").unlink(missing_ok=True)

    def _close_admission(self, *, deadline: float) -> None:
        self.layout.root.mkdir(parents=True, exist_ok=True)
        (self.layout.root / "switching").write_text(
            json.dumps({"pid": os.getpid(), "deadline": deadline, "update_id": self.update_id}) + "\n", encoding="utf-8")
        enter_maintenance(self.update_id, deadline=deadline)

    # The phases --------------------------------------------------------------

    def _preparing(self) -> None:
        record = self._record()
        deadline = record.request.created_at + TURN_RELEASE_SECONDS
        while record.state.phase is UpdatePhase.PREPARING:
            if time.time() >= deadline:
                self._end(UpdatePhase.ABORTED, "the turn that requested this update never finished")
                return
            time.sleep(0.2)
            record = self._record()

    def _building(self) -> None:
        record = self._record()
        commit = candidate_commit(record)
        base = str(record.state.detail.get("base_sha") or self.host.local_tip())
        changes: dict[str, Any] = {"base_sha": base}
        if commit != base and not self.host.is_based_on(record, commit, base):
            commit = self.host.rebase(record, base)
            self.store.rebind_candidate(self.update_id, commit)
        running = paths.current_runtime()
        version = self.host.build(record, commit, running.version if running else record.request.controller_version)
        self._move(UpdatePhase.TRIAL, landed_sha=commit, candidate_version=version, **changes)

    def _trial(self) -> None:
        record = self._record()
        try:
            result = self.host.trial(record, candidate_version(record))
        except Exception as exc:  # noqa: BLE001 - any failure leaves the live instance untouched.
            raise Failed(f"the trial failed: {exc}") from exc
        self._move(UpdatePhase.READY, trial=result if isinstance(result, dict) else {})

    def _ready(self) -> None:
        record = self._record()
        base = str(record.state.detail.get("base_sha") or record.request.base_sha)
        local = self.host.local_tip()
        if local == base:
            self._move(UpdatePhase.QUIESCING)
            return
        rebases = int(record.state.detail.get("rebases") or 0) + 1
        if rebases > MAX_REBASES:
            raise Failed("the local line kept moving; the change was rebased three times")
        commit = self.host.rebase(record, local)
        self.store.rebind_candidate(self.update_id, commit)
        self._retire_trial_evidence(rebases)
        self._move(UpdatePhase.BUILDING, landed_sha=commit, base_sha=local, rebases=rebases)

    def _retire_trial_evidence(self, rebases: int) -> None:
        """A new trial issues a new grant, so the previous one moves aside."""
        attempt = self._record().state.attempt
        names = [f"verifier-grant-{attempt}.json", f"verifier-result-{attempt}.json", "observations"]
        keep = self.directory / f"rebase-{rebases}"
        for name in names:
            source = self.directory / name
            if source.exists():
                keep.mkdir(exist_ok=True)
                filesystem.replace_file(source, keep / name)

    def _quiescing(self) -> None:
        record = self._record()
        entered = record.state.updated_at
        if not self._idle(entered + QUIET_SECONDS):
            self.host.request_checkpoint(record)
            if not self._idle(entered + QUIESCE_LIMIT_SECONDS):
                raise Failed("tasks were still running after 30 minutes")
        record = self._record()
        reference = self.host.probe_old(record)
        present = pointers.read_or_recover(self.layout)
        self._note(update=self.update_id, reference=reference, from_version=present.version,
                   from_base=present.base, generation=present.generation)
        self._move(UpdatePhase.ACTIVATING, previous_system_gate=reference["gate"])

    def _idle(self, until: float) -> bool:
        """Wait for quiet in slices, so a cancelled update stops waiting."""
        while True:
            if self._record().state.phase is not UpdatePhase.QUIESCING:
                raise ConcurrentUpdateError("the update left quiescing")
            if self.host.wait_idle(min(time.time() + 20, until)):
                return True
            if time.time() >= until:
                return False

    def _activating(self) -> None:
        record = self._record()
        version = candidate_version(record)
        journal = self._journal()
        present = pointers.read_or_recover(self.layout)
        if present.version != version and present.generation > int(journal["generation"]):
            # The pointer moved and came back while this controller was away.
            raise Failed("the launcher returned the pointer to the previous version")
        if present.version != version:
            self._switch_pointer(record, version, journal)
            if self._record().state.phase is not UpdatePhase.ACTIVATING:
                return
        elif self.host.is_up(record, candidate_commit(record)) is None:
            # Died between moving the pointer and starting the worker.
            self.host.start_worker()
        self._await_candidate(record, version)

    def _switch_pointer(self, record: UpdateRecord, version: str, journal: dict) -> None:
        if journal.get("stopped"):
            # Died after stopping the worker and before moving the pointer:
            # the old worker must be back to checkpoint what is left.
            self.host.start_worker()
        deadline = time.time() + MAINTENANCE_SECONDS
        self._close_admission(deadline=deadline)
        self.host.request_checkpoint(record)
        if not self.host.wait_idle(min(time.time() + 60, deadline)):
            _log.warning("tasks still running at the switch; they resume after the restart")
        self._note(stopped=True)
        self.host.stop_worker()
        changes: dict[str, Any] = {}
        manifest = sealed.read_manifest(self.layout, version)
        if manifest.get("destructive_migration"):
            changes["backup"] = self.host.snapshot_databases(self.update_id)
        try:
            pointers.activate(self.layout, int(journal["generation"]), version=version, base=str(manifest["base"]),
                              update_id=self._probation_id(), deadline=int(time.time() + POINTER_PROBATION_SECONDS))
        except (pointers.PointerConflict, pointers.PointerError, OSError) as exc:
            self.host.start_worker()
            self._reopen_admission()
            self._end(UpdatePhase.ABORTED, f"the pointer could not be moved: {exc}", diagnose=True, **changes)
            self._start_diagnosis()
            return
        if changes:
            self._note(**changes)
        self.host.start_worker()

    def _probation_id(self) -> str:
        return paths.require_id(self.update_id, "update id")

    def _await_candidate(self, record: UpdateRecord, version: str) -> None:
        commit = candidate_commit(record)
        started = time.monotonic()
        limit = started + START_SECONDS
        hard = started + MIGRATION_LIMIT_SECONDS
        while True:
            if pointers.read(self.layout).version != version:
                raise Failed("the candidate crashed and the launcher returned to the previous version")
            pid = self.host.is_up(record, commit)
            if pid is not None:
                break
            now = time.monotonic()
            if self.host.migrating(version):
                limit = min(hard, now + START_SECONDS)
            if now >= limit:
                raise Failed("the candidate did not start")
            time.sleep(1.0)
        self._note(pid=pid, verify_started=time.time())
        self._move(UpdatePhase.VERIFYING, pid=pid, **self._backup_detail())

    def _backup_detail(self) -> dict:
        backup = self._journal().get("backup")
        return {"backup": backup} if backup else {}

    def _verifying(self) -> None:
        record = self._record()
        journal = self._journal()
        version, commit = candidate_version(record), candidate_commit(record)
        if journal.get("committing"):
            self._commit(record, version, commit, journal)
            return
        reference = journal["reference"]
        started = float(journal.get("verify_started") or record.state.updated_at)
        deadline = started + VERIFY_LIMIT_SECONDS
        if not journal.get("probed"):
            self._within(deadline)
            self.host.probe_candidate(record, version, commit, reference)
            journal = self._note(probed=True)
        if "turn_check" not in journal:
            self._within(deadline)
            journal = self._note(turn_check=self.host.turn_check(record))
        if not journal.get("admission_open"):
            held = count_held(self.store)
            self._reopen_admission()
            journal = self._note(admission_open=True, held_turns=held, probation_started=time.time())
        self.host.probation(record, version, commit, int(journal["pid"]), reference,
                            float(journal["probation_started"]) + PROBATION_SECONDS)
        if "desktop" in (sealed.read_manifest(self.layout, version).get("components") or ()):
            self.host.shell_handshake(record, version)
        self._commit(record, version, commit, journal)

    @staticmethod
    def _within(deadline: float) -> None:
        if time.time() >= deadline:
            raise Failed("verification ran past its time limit")

    def _commit(self, record: UpdateRecord, version: str, commit: str, journal: dict) -> None:
        """Every check has passed, so from here the only way is forward."""
        present = pointers.read(self.layout)
        if present.version != version:
            raise Failed("the pointer left the candidate before commit")
        journal = self._note(committing=True)
        base = str(record.state.detail.get("base_sha") or record.request.base_sha)
        try:
            if present.probation:
                pointers.commit(self.layout, present.generation)
            if record.request.kind in ("undo_to", "official"):
                self.host.go_back_local(self.update_id, commit, base, version)
            else:
                self.host.move_local(commit, base, version)
            self.host.backup_repo()
        except Exception as exc:  # noqa: BLE001 - each step repeats harmlessly on resume.
            raise CommitIncomplete(str(exc)) from exc
        self._end_success(journal)

    def _end_success(self, journal: dict) -> None:
        record = self._record()
        self.store.transition(self.update_id, UpdatePhase.SUCCEEDED, expected_phase=record.state.phase, detail={
            **record.state.detail, "turn_check": journal.get("turn_check"), "held_turns": journal.get("held_turns", 0),
        })

    # Failure -----------------------------------------------------------------

    def _abort(self, error: Exception, *, diagnose: bool) -> None:
        changes: dict[str, Any] = {"diagnose": True} if diagnose else {}
        if isinstance(error, RebaseConflict):
            changes["files"] = list(error.files)
        self._end(UpdatePhase.ABORTED, str(error), **changes)
        if diagnose:
            self._start_diagnosis()

    def _start_diagnosis(self) -> None:
        """A failure in ``trial`` or later gets the same read-only diagnosis as a rollback."""
        from ..repair.diagnosis import prepare_after_rollback

        try:
            with self.store._locked():
                prepare_after_rollback(self.store, self.store._load_unlocked(self.update_id))
        except Exception:  # noqa: BLE001 - the failure itself is already recorded.
            _log.exception("diagnosis could not be prepared")

    def _rollback(self, reason: str) -> None:
        record = self._record()
        version = candidate_version(record)
        journal = self._journal()
        try:
            intent = begin_rollback(self.store, self.update_id, reason)
        except ValueError:
            intent = None
        outcome = pointers.rollback(self.layout, version, deadline=time.time() + ROLLBACK_WRITE_SECONDS)
        changes: dict[str, Any] = {"rollback": outcome}
        try:
            self.host.stop_worker()
        except Exception:  # noqa: BLE001 - a candidate that will not stop is replaced at start.
            _log.warning("the candidate worker did not stop for rollback", exc_info=True)
        target = UpdatePhase.ROLLED_BACK
        try:
            self.host.start_worker()
            revision = intent["previous_revision"] if intent else None
            gate = self.host.probe_restored(record, revision, journal.get("reference") or {})
            changes["restored_system_gate"] = gate
        except Exception as exc:  # noqa: BLE001 - the old version failing is what this branch is for.
            target = UpdatePhase.NEEDS_MANUAL_RECOVERY
            changes["recovery_error"] = str(exc)[:_ERROR_LIMIT]
            self.host.note_recovery(
                f"{self.update_id}: the previous version did not come back after rollback: {exc}")
        backup = journal.get("backup")
        if backup:
            changes["backup"] = backup
        self._end(target, reason, **changes)
        self._reopen_admission()

    def advance(self, until: UpdatePhase | None = None) -> None:
        """Run until the update is terminal, or has reached ``until``."""
        steps = {
            UpdatePhase.PREPARING: self._preparing,
            UpdatePhase.BUILDING: self._building,
            UpdatePhase.TRIAL: self._trial,
            UpdatePhase.READY: self._ready,
            UpdatePhase.QUIESCING: self._quiescing,
            UpdatePhase.ACTIVATING: self._activating,
            UpdatePhase.VERIFYING: self._verifying,
        }
        while not is_terminal(phase := self._record().state.phase) and phase is not until:
            try:
                steps[phase]()
            except ConcurrentUpdateError:
                if is_terminal(self._record().state.phase):
                    return
                raise
            except CommitIncomplete:
                raise
            except Exception as exc:  # noqa: BLE001 - every failure has to end the update.
                _log.exception("self-update %s failed in %s", self.update_id, phase.value)
                current = self._record().state.phase
                if current in (UpdatePhase.ACTIVATING, UpdatePhase.VERIFYING):
                    if self._pointer_moved():
                        self._rollback(str(exc))
                    else:
                        # Nothing live changed: the old worker comes back.
                        self._fail_before_pointer(exc)
                elif not is_terminal(current):
                    self._abort(exc, diagnose=current not in (UpdatePhase.PREPARING, UpdatePhase.BUILDING))

    def _pointer_moved(self) -> bool:
        record = self._record()
        try:
            return pointers.read(self.layout).version == candidate_version(record) or (
                pointers.read(self.layout).generation > int(self._journal().get("generation", 1 << 62)))
        except pointers.PointerError:
            return True

    def _fail_before_pointer(self, error: Exception) -> None:
        if self._journal().get("stopped"):
            try:
                self.host.start_worker()
            except Exception:  # noqa: BLE001 - the shell and launcher still recover a stopped worker.
                _log.warning("the old worker did not restart", exc_info=True)
        self._reopen_admission()
        self._abort(error, diagnose=True)


# Worker side ------------------------------------------------------------------


def launch(update_id: str, *, store: SelfUpdateStore | None = None) -> None:
    from ..delivery.controller import launch_controller

    if os.environ.get("OPENPROGRAM_TRIAL", "").strip() == "1":
        return  # A trial's sweep works on its copy and starts nothing outside it.
    launch_controller(update_id, store=store)


def _durable(request) -> bool:
    from openprogram.runtime.context.session_db import default_db

    try:
        return default_db().message_exists(request.session_id, request.origin_assistant_id)
    except Exception:  # noqa: BLE001 - an unreadable conversation means the turn is not proven.
        return False


def _local_tip(store: SelfUpdateStore) -> str:
    """What ``local`` is now; a queued request was prepared against an older tip."""
    layout = paths.for_state(store.root.parent)
    result = subprocess.run(["git", "-C", str(layout.repo), "rev-parse", "--verify", "refs/heads/local^{commit}"],
                            capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError("the local line cannot be read")
    return result.stdout.strip()


def clear_finished_admission(store: SelfUpdateStore, layout: Layout) -> bool:
    """Finish reopening admission for a controller that died just after ending its update."""
    from .maintenance import load_maintenance

    try:
        with store._locked():
            marker = load_maintenance(store)
            if marker is None or "deadline" not in marker or not is_terminal(store._load_unlocked(marker["update_id"]).state.phase):
                return False
    except (RuntimeError, ValueError, OSError):
        return False  # Not this function's to judge; the marker's deadline still applies.
    leave_maintenance(marker["update_id"])
    (layout.root / "switching").unlink(missing_ok=True)
    return True


def take_next(store: SelfUpdateStore) -> str | None:
    """Start the head of the queue when the slot is free."""
    state = store.take_queued(durable=_durable, local_tip=lambda: _local_tip(store))
    if state is None:
        return None
    launch(state.update_id, store=store)
    return state.update_id


def record_guard_rollback(store: SelfUpdateStore, record: UpdateRecord, layout: Layout) -> bool:
    """Record a rollback the launcher's guard made with no controller alive."""
    journal_path = store.root / record.request.update_id / JOURNAL
    try:
        journal = _read_json(journal_path)
        present = pointers.read(layout)
    except (OSError, ValueError, pointers.PointerError):
        return False
    if (record.state.phase not in (UpdatePhase.ACTIVATING, UpdatePhase.VERIFYING)
            or present.version == candidate_version(record)
            or present.generation <= int(journal.get("generation", 1 << 62))
            or is_candidate_worker(record)):
        return False
    reason = "the new version kept crashing and the launcher returned to the previous one"
    store.transition(record.request.update_id, UpdatePhase.ROLLED_BACK, expected_phase=record.state.phase, detail={
        **record.state.detail, "reason": reason, "error": reason, "rollback": "launcher",
    })
    leave_maintenance(record.request.update_id)
    (layout.root / "switching").unlink(missing_ok=True)
    return True


def reconcile_at_start(store: SelfUpdateStore, record: UpdateRecord | None) -> None:
    """Worker start: finish what a dead controller left, then start the next."""
    clear_finished_admission(store, paths.for_state(store.root.parent))
    if record is not None and owns(record):
        layout = paths.for_state(store.root.parent)
        if record_guard_rollback(store, record, layout):
            take_next(store)
            return
        launch(record.request.update_id, store=store)
    elif record is None:
        take_next(store)


def controller_stale(store: SelfUpdateStore, record: UpdateRecord, *, now: float | None = None) -> bool:
    """Whether a live update's controller has stopped writing its heartbeat."""
    try:
        beat = (store.root / record.request.update_id / HEARTBEAT).stat().st_mtime
    except OSError:
        return True
    return (time.time() if now is None else now) - beat > HEARTBEAT_STALE_SECONDS


# Controller entry ----------------------------------------------------------------


def _heartbeat(path: Path, stop: threading.Event) -> None:
    while True:
        try:
            path.write_text(f"{os.getpid()}\n", encoding="ascii")
        except OSError:
            _log.warning("heartbeat not written", exc_info=True)
        if stop.wait(HEARTBEAT_SECONDS):
            return


@contextmanager
def controlling(directory: Path) -> Iterator[bool]:
    """Hold an update's controller lock and write its heartbeat; yields whether it was free."""
    from .supervisor import _controller_lock

    with _controller_lock(directory) as acquired:
        if not acquired:
            yield False
            return
        stop = threading.Event()
        beat = threading.Thread(target=_heartbeat, args=(directory / HEARTBEAT, stop), daemon=True, name="heartbeat")
        beat.start()
        try:
            yield True
        finally:
            stop.set()
            beat.join(timeout=2)


def run(update_id: str, *, store: SelfUpdateStore | None = None, host: Any = None) -> int:
    """The controller process. An exit other than 0 is restarted by the service manager."""
    from ..delivery.controller import retire_controller

    target = store or SelfUpdateStore()
    layout = paths.for_state(target.root.parent)
    directory = target.root / update_id
    with controlling(directory) as acquired:
        if not acquired:
            return 0
        SwitchController(target, update_id, host or LiveHost(target, layout), layout).advance()
    if is_terminal(target.load(update_id).state.phase):
        clear_finished_admission(target, layout)
        try:
            (host or LiveHost(target, layout)).retention()
        except Exception:  # noqa: BLE001 - retention runs again at the next worker start.
            _log.warning("retention failed", exc_info=True)
        take_next(target)
        retire_controller(update_id)
    return 0


__all__ = [
    "CommitIncomplete", "Failed", "JOURNAL", "LiveHost", "RebaseConflict", "SNAPSHOT_FOLDERS", "SwitchController", "candidate_commit",
    "candidate_version", "clear_finished_admission",
    "controller_stale", "controlling", "launch", "owns", "reconcile_at_start", "record_guard_rollback", "result_summary", "run",
    "take_next",
]
