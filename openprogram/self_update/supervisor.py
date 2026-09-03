"""External controller for one durable conversational self-update."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time
from typing import Iterator

from openprogram import _compat as file_lock
from openprogram.self_update.maintenance import enter_maintenance, leave_maintenance
from openprogram.self_update.store import SelfUpdateStore
from openprogram.self_update.types import (
    TERMINAL_PHASES,
    ConcurrentUpdateError,
    UpdatePhase,
    UpdateRecord,
    _validate_update_id,
)
from openprogram.store.session.git_session import atomic_write_text


@dataclass(frozen=True)
class Artifact:
    path: Path
    sha256: str


def _sandbox_executable() -> Path:
    path = Path("/usr/bin/sandbox-exec")
    if not path.is_file() or path.is_symlink() or not os.access(path, os.X_OK):
        raise RuntimeError("sandbox-exec is required for candidate packaging")
    return path


def _canonical_store(state_root: Path) -> SelfUpdateStore:
    from openprogram.paths import get_state_dir

    expected = (get_state_dir() / "self-updates").resolve()
    actual = Path(state_root).resolve()
    if actual != expected:
        raise RuntimeError("state root is not the canonical self-update directory")
    return SelfUpdateStore(actual)


@contextmanager
def _controller_lock(update_dir: Path) -> Iterator[bool]:
    path = update_dir / "supervisor.lock"
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    acquired = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError("supervisor lock is not a regular file")
        os.fchmod(descriptor, 0o600)
        try:
            file_lock.flock(
                descriptor, file_lock.LOCK_EX | file_lock.LOCK_NB
            )
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        if acquired:
            file_lock.flock(descriptor, file_lock.LOCK_UN)
        os.close(descriptor)


def _wait_for_staging(store: SelfUpdateStore, update_id: str) -> UpdateRecord | None:
    record = store.load(update_id)
    deadline = record.request.created_at + record.request.timeout_seconds
    while record.state.phase is UpdatePhase.PREPARING and time.time() < deadline:
        time.sleep(0.2)
        record = store.load(update_id)
    return record if record.state.phase is UpdatePhase.STAGING else None


def _sandbox_profile(
    candidate: Path,
    artifact_root: Path,
    build_home: Path,
    build_tmp: Path,
) -> str:
    def quoted(path: Path) -> str:
        return json.dumps(str(path))

    return "\n".join((
        "(version 1)",
        "(deny default)",
        "(allow file-read*)",
        "(allow process-exec process-fork)",
        "(allow process-info* (target same-sandbox))",
        "(allow signal (target same-sandbox))",
        "(allow ipc-posix-sem ipc-posix-shm)",
        "(allow sysctl-read (sysctl-name-prefix \"hw.\"))",
        "(deny network*)",
        f"(deny file-read* (subpath {quoted(Path.home() / '.openprogram')}))",
        f"(deny file-read* (subpath {quoted(Path.home() / '.ssh')}))",
        f"(deny file-read* (subpath {quoted(Path.home() / '.aws')}))",
        f"(deny file-read* (subpath {quoted(Path.home() / '.gnupg')}))",
        f"(deny file-read* (subpath {quoted(Path.home() / '.claude')}))",
        f"(deny file-read* (subpath {quoted(Path.home() / '.config')}))",
        f"(deny file-read* (literal {quoted(Path.home() / '.claude.json')}))",
        f"(deny file-read* (literal {quoted(Path.home() / '.netrc')}))",
        f"(deny file-read* (subpath {quoted(Path.home() / 'Library/Keychains')}))",
        '(allow file-ioctl file-read-data file-write-data (literal "/dev/null"))',
        '(allow file-read-data (literal "/dev/zero"))',
        '(allow file-read-data (literal "/dev/random"))',
        '(allow file-read-data (literal "/dev/urandom"))',
        f"(allow file-write* (subpath {quoted(candidate)}))",
        f"(allow file-write* (subpath {quoted(artifact_root)}))",
        f"(allow file-write* (subpath {quoted(build_home)}))",
        f"(allow file-write* (subpath {quoted(build_tmp)}))",
        f"(allow file-read* (subpath {quoted(artifact_root)}))",
        f"(allow file-read* (subpath {quoted(build_home)}))",
        f"(allow file-read* (subpath {quoted(build_tmp)}))",
        '(deny file-read* (subpath "/Applications/OpenProgram.app"))',
        '(deny file-read* (regex #".*/\\.env($|/).*$"))',
    )) + "\n"


def _build_candidate(_record: UpdateRecord, _update_dir: Path) -> Artifact:
    from openprogram.programs.tools.system.self_update import (
        _recorded_path,
        _validate_candidate_snapshot,
        _validate_registered_worktree,
    )
    from openprogram.store.session.git_session import atomic_write_text
    from openprogram.worktree.manager import get_manager

    record = _record
    update_dir = _update_dir
    worktree = get_manager().get_worktree(record.request.worktree_id)
    if worktree is None or worktree.parent_session != record.request.session_id:
        raise RuntimeError("candidate worktree ownership changed")
    source = _recorded_path(worktree.source_repo, "source repo")
    candidate = _recorded_path(worktree.worktree_path, "candidate worktree")
    _validate_registered_worktree(
        source, candidate, record.request.candidate_sha, worktree.branch_name
    )
    _validate_candidate_snapshot(candidate, record.request.candidate_sha)

    sandbox = _sandbox_executable()
    script = candidate / "apps/desktop/scripts/package-and-install-app.sh"
    if not script.is_file() or script.is_symlink():
        raise RuntimeError("candidate packaging entry is unavailable")
    artifact_root = update_dir / "artifact"
    artifact = artifact_root / "OpenProgram.app"
    build_home = update_dir / "build-home"
    build_tmp = update_dir / "build-tmp"
    for directory in (artifact_root, build_home, build_tmp):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise RuntimeError("candidate staging path is not a private directory")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
    if artifact.exists() or artifact.is_symlink():
        raise RuntimeError("candidate artifact path already exists")

    profile = _sandbox_profile(candidate, artifact_root, build_home, build_tmp)
    profile_path = update_dir / "sandbox.sb"
    atomic_write_text(profile_path, profile)
    environment = {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(build_home),
        "TMPDIR": str(build_tmp),
        "CI": "1",
        "NPM_CONFIG_USERCONFIG": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }
    result = subprocess.run(
        [
            str(sandbox),
            "-f",
            str(profile_path),
            "/bin/bash",
            str(script),
            "--output",
            str(artifact),
        ],
        cwd=str(candidate),
        env=environment,
        capture_output=True,
        text=True,
        timeout=record.request.timeout_seconds,
    )
    atomic_write_text(
        update_dir / "build.log",
        (result.stdout + result.stderr)[-200_000:],
    )
    if result.returncode != 0:
        raise RuntimeError(f"candidate packaging failed with exit {result.returncode}")
    if not artifact.is_dir() or artifact.is_symlink():
        raise RuntimeError("candidate packaging did not produce one App artifact")
    _validate_registered_worktree(
        source, candidate, record.request.candidate_sha, worktree.branch_name
    )
    _validate_candidate_snapshot(candidate, record.request.candidate_sha)
    digest = _tree_digest(artifact)
    atomic_write_text(
        update_dir / "artifact.json",
        json.dumps(
            {
                "schema": 1,
                "candidate_sha": record.request.candidate_sha,
                "path": str(artifact),
                "sha256": digest,
            },
            sort_keys=True,
        ) + "\n",
    )
    return Artifact(artifact, digest)


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix().encode()
        stat = path.lstat()
        digest.update(relative + b"\0" + str(stat.st_mode).encode() + b"\0")
        if path.is_symlink():
            target = path.resolve(strict=True)
            try:
                target.relative_to(root.resolve())
            except ValueError as exc:
                raise RuntimeError(f"artifact symlink escapes App bundle: {relative!r}") from exc
            digest.update(os.readlink(path).encode() + b"\0")
        elif path.is_file():
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def _wait_for_quiescence(deadline: float) -> bool:
    from openprogram.agent.job.store import list_jobs
    from openprogram.agent.job.types import JobStatus
    from openprogram.store import default_store

    active_jobs = {JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING}
    while time.time() < deadline:
        sessions = default_store().list_sessions(
            status="running", include_archived=True, limit=100_000
        )
        jobs = []
        for session in default_store().list_sessions(
            include_archived=True, limit=100_000
        ):
            jobs.extend(
                list_jobs(
                    session["id"], status_filter=active_jobs, limit=1
                )
            )
            if jobs:
                break
        if not sessions and not jobs:
            return True
        time.sleep(0.2)
    return False


def _installer_command(
    argument: Path,
    update_dir: Path,
    installer_sha256: str,
    mode: str,
) -> str:
    installer = _installer_snapshot(update_dir, installer_sha256)
    result = subprocess.run(
        ["/bin/bash", str(installer), mode, str(argument)],
        capture_output=True,
        text=True,
        timeout=300,
        env={
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(Path.home()),
        },
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"installer {mode} failed: {(result.stderr or result.stdout)[-1000:]}"
        )
    values = [
        line.partition("=")[2]
        for line in result.stdout.splitlines()
        if line.startswith("OPENPROGRAM_TRANSACTION_DIR=")
    ]
    if len(values) != 1:
        raise RuntimeError("installer did not report one transaction")
    return values[0]


def _prepare_install(artifact: Artifact, update_dir: Path, installer_sha256: str) -> str:
    if _tree_digest(artifact.path) != artifact.sha256:
        raise RuntimeError("candidate artifact changed after validation")
    return _installer_command(artifact.path, update_dir, installer_sha256, "--prepare")


def _activate(transaction: Path, update_dir: Path, installer_sha256: str) -> str:
    transaction = _validate_transaction_path(transaction)
    reported = _installer_command(transaction, update_dir, installer_sha256, "--activate")
    if reported != str(transaction):
        raise RuntimeError("installer activated a different transaction")
    return reported


def _installer_snapshot(update_dir: Path, installer_sha256: str) -> Path:
    installer = update_dir / "controller" / "install-app.sh"
    if not installer.is_file() or installer.is_symlink():
        raise RuntimeError("trusted self-update installer snapshot is unavailable")
    if hashlib.sha256(installer.read_bytes()).hexdigest() != installer_sha256:
        raise RuntimeError("trusted self-update installer snapshot changed")
    return installer


def _abort(
    store: SelfUpdateStore,
    update_id: str,
    phase: UpdatePhase,
    error: Exception | str,
) -> None:
    if phase in {UpdatePhase.PREPARING, UpdatePhase.STAGING, UpdatePhase.READY}:
        try:
            store.transition(
                update_id,
                UpdatePhase.ABORTED,
                expected_phase=phase,
                detail={**store.load(update_id).state.detail, "error": str(error)[:2000]},
            )
        except ConcurrentUpdateError:
            if store.load(update_id).state.phase not in TERMINAL_PHASES:
                raise


def _validate_transaction_path(path: Path) -> Path:
    if (
        not path.is_absolute()
        or not path.is_dir()
        or path.is_symlink()
        or path.parent != Path("/Applications")
        or not path.name.startswith(".openprogram-app-install.")
        or path.stat().st_uid != os.getuid()
    ):
        raise RuntimeError("installer returned an invalid transaction directory")
    return path


def _rollback(store: SelfUpdateStore, update_id: str, installer_sha256: str, error: Exception, *, verdict: str | None = None) -> None:
    from openprogram.self_update.rollback_intent import begin_rollback
    from openprogram.self_update.system_probe import probe_restored_system

    record = store.load(update_id)
    detail = {**record.state.detail, "error": str(error)[:2000]}
    if verdict is not None:
        detail["verifier_verdict"] = verdict
    target = UpdatePhase.NEEDS_MANUAL_RECOVERY
    try:
        intent = begin_rollback(store, update_id, str(error))
        if time.time() >= intent["deadline"]:
            raise RuntimeError("rollback deadline expired")
        transaction = _validate_transaction_path(Path(detail["transaction_dir"]))
        reported = _installer_command(transaction, store.root / update_id, installer_sha256, "--rollback")
        if reported != str(transaction):
            raise RuntimeError("installer rolled back a different transaction")
        restored = probe_restored_system(store.load(update_id), intent["previous_revision"])
        if time.time() >= intent["deadline"]:
            raise RuntimeError("rollback verification deadline expired")
        detail.update(restored_system_gate=restored, rollback_available=False)
        target = UpdatePhase.ROLLED_BACK
    except Exception as exc:
        detail["recovery_error"] = str(exc)[:2000]
    store.transition(update_id, target, expected_phase=record.state.phase, detail=detail)


def _finish_verification(store: SelfUpdateStore, update_id: str, installer_sha256: str, grant: dict) -> int:
    from openprogram.self_update.verification_channel import consume_result
    from openprogram.self_update.system_probe import probe_system

    receipt = None
    deadline = time.monotonic() + max(0, min(600, grant["deadline"] - time.time()))
    try:
        while time.monotonic() < deadline and time.time() < grant["deadline"]:
            receipt = consume_result(store, update_id, grant["token"])
            if receipt is not None:
                break
            time.sleep(0.2)
        if receipt is None:
            raise RuntimeError("verifier timed out")
        if receipt["verdict"] != "pass":
            raise RuntimeError(f"verifier result: {receipt['verdict']}")
        record = store.load(update_id)
        gate = probe_system(record)
        if gate["worker_pid"] != grant["worker_pid"] or time.monotonic() >= deadline or time.time() >= grant["deadline"]:
            raise RuntimeError("candidate changed or verification deadline expired before commit")
        if consume_result(store, update_id, grant["token"]) != receipt:
            raise RuntimeError("accepted verifier result changed before commit")
        transaction = _validate_transaction_path(Path(record.state.detail["transaction_dir"]))
        reported = _installer_command(transaction, store.root / update_id, installer_sha256, "--commit")
        if reported != str(transaction):
            raise RuntimeError("installer committed a different transaction")
        store.transition(update_id, UpdatePhase.SUCCEEDED, expected_phase=UpdatePhase.VERIFYING,
                         detail={**record.state.detail, "verifier_verdict": "pass", "rollback_available": False,
                                 "committed_system_gate": gate, "verifier_result": f"verifier-result-{record.state.attempt}.json"})
        leave_maintenance(update_id)
        return 0
    except Exception as exc:
        record = store.load(update_id)
        if record.state.phase is UpdatePhase.VERIFYING:
            _rollback(store, update_id, installer_sha256, exc,
                      verdict=receipt["verdict"] if receipt is not None else "inconclusive")
        if store.load(update_id).state.phase is UpdatePhase.ROLLED_BACK:
            leave_maintenance(update_id)
        return 1


def run_supervisor(
    update_id: str,
    *,
    state_root: Path,
    installer_sha256: str,
) -> int:
    """Build and activate a candidate, then release system-gated verification."""
    from openprogram.self_update.system_probe import probe_system, probe_current_system
    from openprogram.self_update.verification_channel import issue_grant, _digest

    if (
        len(installer_sha256) != 64
        or any(character not in "0123456789abcdef" for character in installer_sha256)
    ):
        raise ValueError("installer_sha256 must be a lowercase SHA-256 digest")
    store = _canonical_store(state_root)
    update_dir = store.root / _validate_update_id(update_id)
    if update_dir.is_symlink() or not update_dir.is_dir():
        raise ValueError("update directory must be a real private directory")
    record = store.load(update_id)
    with _controller_lock(update_dir) as acquired:
        if not acquired:
            return 0
        _installer_snapshot(update_dir, installer_sha256)
        atomic_write_text(
            update_dir / "supervisor.ready",
            json.dumps(
                {
                    "schema": 1,
                    "pid": os.getpid(),
                    "update_id": update_id,
                    "installer_sha256": installer_sha256,
                },
                sort_keys=True,
            ) + "\n",
        )
        record = store.load(update_id)
        if record.state.phase in TERMINAL_PHASES or record.state.phase is UpdatePhase.VERIFYING:
            return 0
        if record.state.phase is UpdatePhase.PREPARING:
            record = _wait_for_staging(store, update_id)
            if record is None:
                current = store.load(update_id)
                _abort(store, update_id, current.state.phase, "turn release timed out")
                return 1
        if record.state.phase is not UpdatePhase.STAGING:
            return 1

        try:
            artifact = _build_candidate(record, update_dir)
            transaction = _validate_transaction_path(
                Path(_prepare_install(artifact, update_dir, installer_sha256))
            )
            state = store.transition(
                update_id,
                UpdatePhase.READY,
                expected_phase=UpdatePhase.STAGING,
                detail={
                    "artifact_path": str(artifact.path),
                    "artifact_sha256": artifact.sha256,
                    "transaction_dir": str(transaction),
                },
            )
        except Exception as exc:
            _abort(store, update_id, UpdatePhase.STAGING, exc)
            return 1

        maintenance_entered = False
        try:
            enter_maintenance(update_id)
            maintenance_entered = True
            deadline = min(
                record.request.created_at + record.request.timeout_seconds,
                time.time() + 600,
            )
            if not _wait_for_quiescence(deadline):
                _abort(store, update_id, state.phase, "quiescence timed out")
                leave_maintenance(update_id)
                return 1
            previous_system_gate = probe_current_system(store.load(update_id))
            if previous_system_gate["candidate_sha"] == record.request.candidate_sha:
                raise RuntimeError("candidate is already the running revision")
            store.transition(
                update_id,
                UpdatePhase.ACTIVATING,
                expected_phase=UpdatePhase.READY,
                detail={
                    "artifact_path": str(artifact.path),
                    "artifact_sha256": artifact.sha256,
                    "transaction_dir": str(transaction),
                    "previous_system_gate": previous_system_gate,
                },
            )
            _activate(transaction, update_dir, installer_sha256)
            # Startup recovery waits in ACTIVATING. Publish the receipt and
            # VERIFYING together, never an observable ungated verifying state.
            system_gate = probe_system(store.load(update_id))
            grant = issue_grant(store, update_id, system_gate)
            store.transition(
                update_id,
                UpdatePhase.VERIFYING,
                expected_phase=UpdatePhase.ACTIVATING,
                detail={
                    "artifact_sha256": artifact.sha256,
                    "transaction_dir": str(transaction),
                    "rollback_available": True,
                    "system_gate": system_gate,
                    "previous_system_gate": previous_system_gate,
                    "verifier_grant_sha256": _digest(grant),
                },
            )
            return _finish_verification(store, update_id, installer_sha256, grant)
        except Exception as exc:
            current = store.load(update_id).state.phase
            if current is UpdatePhase.READY:
                _abort(store, update_id, current, exc)
            elif current in {UpdatePhase.ACTIVATING, UpdatePhase.VERIFYING}:
                _rollback(store, update_id, installer_sha256, exc)
            if maintenance_entered and store.load(update_id).state.phase is not UpdatePhase.NEEDS_MANUAL_RECOVERY:
                leave_maintenance(update_id)
            return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one OpenProgram self-update")
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--installer-sha256", required=True)
    parser.add_argument("update_id")
    args = parser.parse_args(argv)
    return run_supervisor(
        args.update_id,
        state_root=args.state_root,
        installer_sha256=args.installer_sha256,
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["Artifact", "run_supervisor", "main"]
