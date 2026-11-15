"""External controller for one durable conversational self-update."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import stat
import socket
import subprocess
import tempfile
import time
from typing import Iterator

from openprogram.host._platform import filesystem
from openprogram.host._platform import locking
from openprogram.operations.self_update.control.maintenance import enter_maintenance
from openprogram.operations.self_update.control.maintenance import leave_maintenance
from openprogram.operations.self_update.store import SelfUpdateStore
from openprogram.operations.self_update.trial.sandbox import _sandbox_profile
from openprogram.operations.self_update.delivery.activation import PLAN_FILE
from openprogram.operations.self_update.delivery.activation import load_plan
from openprogram.operations.self_update.delivery.package_protocol import validate_reopen_package
from openprogram.operations.self_update.types import (
    TERMINAL_PHASES,
    ConcurrentUpdateError,
    DEFAULT_APP_PATH,
    UpdatePhase,
    UpdateRecord,
    _validate_update_id,
)
from openprogram.state.store.session.atomic_file import atomic_write_text


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
    from openprogram.state.paths import get_state_dir

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
        filesystem.restrict_descriptor_to_user(descriptor)
        try:
            locking.flock(
                descriptor, locking.LOCK_EX | locking.LOCK_NB
            )
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        if acquired:
            locking.flock(descriptor, locking.LOCK_UN)
        os.close(descriptor)


def _wait_for_build(store: SelfUpdateStore, update_id: str) -> UpdateRecord | None:
    record = store.load(update_id)
    deadline = record.request.created_at + record.request.timeout_seconds
    while record.state.phase is UpdatePhase.PREPARING and time.time() < deadline:
        time.sleep(0.2)
        record = store.load(update_id)
    return record if record.state.phase is UpdatePhase.BUILDING else None


def _reserve_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    if port == 18100:
        return _reserve_loopback_port()
    return port


def _rebind_runtime_manifest(resources: Path, previous_sha256: str) -> None:
    manifest = resources / "runtime/runtime-manifest.json"
    current_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()
    paths = [resources / "update/reopen-protocol.json"]
    ui_protocol = resources / "update/ui-verification-protocol.json"
    if ui_protocol.exists() or ui_protocol.is_symlink():
        paths.append(ui_protocol)
    documents = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("candidate package protocol is unavailable")
        document = json.loads(path.read_text(encoding="utf-8"))
        binding = (document.get("bindings") or {}).get("runtime_manifest")
        if binding != {
            "path": "runtime/runtime-manifest.json",
            "sha256": previous_sha256,
        }:
            raise RuntimeError("candidate package protocol did not bind the prior manifest")
        documents.append((path, document))
    for path, document in documents:
        document["bindings"]["runtime_manifest"]["sha256"] = current_sha256
        atomic_write_text(path, json.dumps(document, sort_keys=True) + "\n")


def _complete_icon_probe(
    candidate: Path,
    update_dir: Path,
    *,
    deadline: float,
) -> None:
    assets = candidate / "apps/desktop/build/AppIcon.icon/Assets"
    names = (
        "01-cell.svg",
        "02-disc-indigo.svg",
        "03-disc-violet.svg",
        "04-disc-sky.svg",
    )
    sources = []
    for name in names:
        source = assets / name
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 1_048_576:
            raise RuntimeError("candidate icon source is unavailable")
        sources.append(source)
    with tempfile.TemporaryDirectory(prefix="icon-probe-", dir=update_dir) as temporary:
        probe_dir = Path(temporary)
        for source in sources:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise RuntimeError("candidate icon probe deadline expired")
            rendered = probe_dir / f"{source.name}.png"
            converted = subprocess.run(
                ["/usr/bin/sips", "-s", "format", "png", str(source), "--out", str(rendered)],
                env={"PATH": "/usr/bin:/bin", "HOME": str(probe_dir), "TMPDIR": str(probe_dir)},
                capture_output=True,
                text=True,
                timeout=min(20, remaining),
            )
            if converted.returncode != 0:
                raise RuntimeError("trusted icon render probe failed")
            inspected = subprocess.run(
                ["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", "-g", "hasAlpha", str(rendered)],
                env={"PATH": "/usr/bin:/bin", "HOME": str(probe_dir), "TMPDIR": str(probe_dir)},
                capture_output=True,
                text=True,
                timeout=min(10, max(0.1, deadline - time.time())),
            )
            if (
                inspected.returncode != 0
                or "pixelWidth: 1024" not in inspected.stdout
                or "pixelHeight: 1024" not in inspected.stdout
                or "hasAlpha: yes" not in inspected.stdout
            ):
                raise RuntimeError("trusted icon metadata probe failed")
    atomic_write_text(
        update_dir / "icon-probe.json",
        json.dumps(
            {
                "schema": 1,
                "sources": {
                    source.name: hashlib.sha256(source.read_bytes()).hexdigest()
                    for source in sources
                },
                "verified_at": time.time(),
            },
            sort_keys=True,
        )
        + "\n",
    )


def _complete_browser_probe(
    artifact: Path,
    update_dir: Path,
    *,
    deadline: float,
) -> None:
    from ..delivery.controller_bundle import _load_bundle
    from ..delivery.package_protocol import validate_reopen_package
    from ..delivery.package_protocol import validate_ui_package

    controller = update_dir / "controller"
    trusted = _load_bundle(controller)
    trusted_runtime = controller / "runtime"
    resources = artifact / "Contents/Resources"
    candidate_runtime = artifact / "Contents/Resources/runtime"
    candidate_browsers = candidate_runtime / "assets/playwright"
    trusted_browsers = trusted_runtime / "assets/playwright"
    trusted_browser_sha256 = _tree_digest(trusted_browsers)
    if _tree_digest(candidate_browsers) != trusted_browser_sha256:
        raise RuntimeError("candidate browser assets do not match the trusted runtime input")
    manifest_path = candidate_runtime / "runtime-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    browser_capability = (manifest.get("capabilities") or {}).get("browser.playwright")
    if (
        manifest.get("schema") != 2
        or manifest.get("browser_probe") != "deferred"
        or browser_capability != {"present": True, "verified": False}
    ):
        raise RuntimeError("candidate did not preserve the deferred browser probe contract")
    validate_reopen_package(artifact)
    ui_protocol = resources / "update/ui-verification-protocol.json"
    if ui_protocol.exists() or ui_protocol.is_symlink():
        validate_ui_package(artifact)
    previous_manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    remaining = min(60, deadline - time.time())
    if remaining <= 0:
        raise RuntimeError("candidate browser probe deadline expired")
    with tempfile.TemporaryDirectory(prefix="browser-probe-", dir=update_dir) as home:
        code = (
            "from playwright.sync_api import sync_playwright; "
            "p=sync_playwright().start(); b=p.chromium.launch(headless=True); "
            "page=b.new_page(); page.set_content('<title>OpenProgram runtime probe</title>'); "
            "assert page.title() == 'OpenProgram runtime probe'; b.close(); p.stop(); "
            "print('TRUSTED_BROWSER_PROBE_OK')"
        )
        argv = [str(trusted.python), "-I", "-B", "-c", code]
        proc = subprocess.Popen(
            argv,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": home,
                "TMPDIR": home,
                "PLAYWRIGHT_BROWSERS_PATH": str(candidate_browsers),
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=remaining)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        result = subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
    if result.returncode != 0 or result.stdout.strip() != "TRUSTED_BROWSER_PROBE_OK":
        raise RuntimeError("trusted browser probe failed")
    if (
        _tree_digest(trusted_browsers) != trusted_browser_sha256
        or _tree_digest(candidate_browsers) != trusted_browser_sha256
    ):
        raise RuntimeError("browser assets changed during the trusted probe")
    manifest["capabilities"]["browser.playwright"] = {"present": True, "verified": True}
    manifest["browser_probe"] = "complete"
    atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    _rebind_runtime_manifest(resources, previous_manifest_sha256)
    validate_reopen_package(artifact)
    if ui_protocol.exists():
        validate_ui_package(artifact)
    atomic_write_text(
        update_dir / "browser-probe.json",
        json.dumps(
            {
                "schema": 1,
                "browser_sha256": _tree_digest(candidate_browsers),
                "verified_at": time.time(),
            },
            sort_keys=True,
        )
        + "\n",
    )


def _build_candidate(_record: UpdateRecord, _update_dir: Path) -> Artifact:
    from openprogram.programs.tools.system.self_update import (
        _recorded_path,
        _validate_candidate_snapshot,
        _validate_registered_worktree,
    )
    from openprogram.state.store.session.atomic_file import atomic_write_text
    from openprogram.state.worktree.manager import get_manager

    record = _record
    update_dir = _update_dir
    worktree = get_manager().get_worktree(record.request.worktree_id)
    if worktree is None:
        raise RuntimeError("candidate worktree is no longer registered")
    source = _recorded_path(worktree.source_repo, "source repo")
    candidate = _recorded_path(worktree.worktree_path, "candidate worktree")
    _validate_registered_worktree(
        source, candidate, record.request.candidate_sha, worktree.branch_name
    )
    _validate_candidate_snapshot(candidate, record.request.candidate_sha)

    # Only the frozen trusted controller sees the owner's signing identity.
    # Candidate code builds an ad hoc artifact inside the existing sandbox.
    from ..delivery.local_signing import prepare_app
    from ..delivery.local_signing import sign_app
    prepare_app(Path("/Applications/OpenProgram.app"))

    plan = _activation_plan(record, update_dir, candidate)
    atomic_write_text(update_dir / PLAN_FILE, json.dumps(plan.to_dict(), sort_keys=True) + "\n")
    incremental = plan.scope == "incremental"
    sandbox = _sandbox_executable()
    script = candidate / ("apps/desktop/scripts/package-incremental-app.sh" if incremental
                          else "apps/desktop/scripts/package-and-install-app.sh")
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
    if incremental:
        # The sandbox cannot read the installed App; copy it out first.
        # APFS clones share unchanged blocks, so this stays cheap.
        cloned = subprocess.run(["/bin/cp", "-cR", DEFAULT_APP_PATH, str(artifact)],
                                capture_output=True, text=True, timeout=300)
        if cloned.returncode != 0 or _installed_revision(artifact) != plan.installed_revision:
            raise RuntimeError("could not copy the installed App for an incremental build")

    smoke_port = _reserve_loopback_port()
    profile_path = update_dir / "sandbox.sb"
    environment = {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(build_home),
        "TMPDIR": str(build_tmp),
        "CI": "1",
        "NPM_CONFIG_USERCONFIG": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "NEXT_TELEMETRY_DISABLED": "1",
        "NPM_CONFIG_AUDIT": "false",
        "OPENPROGRAM_SELF_UPDATE_DEFER_BROWSER": "1",
        "OPENPROGRAM_SELF_UPDATE_DEFER_ICON_RENDER": "1",
        "OPENPROGRAM_SMOKE_PORT": str(smoke_port),
    }
    from ..delivery.controller_bundle import build_inputs
    with build_inputs(update_dir, candidate, build_home,
                      deadline=record.request.created_at + record.request.timeout_seconds,
                      complete=not incremental) as inputs:
        profile = _sandbox_profile(
            candidate,
            artifact_root,
            build_home,
            build_tmp,
            loopback_port=smoke_port,
            trusted_input=(Path(inputs["OPENPROGRAM_SELF_UPDATE_ELECTRON_DIST"])
                           if "OPENPROGRAM_SELF_UPDATE_ELECTRON_DIST" in inputs else None),
        )
        atomic_write_text(profile_path, profile)
        environment.update(inputs)
        remaining = record.request.created_at + record.request.timeout_seconds - time.time()
        if remaining <= 0:
            raise RuntimeError("candidate build deadline expired")
        arguments = (["--app", str(artifact), "--components", ",".join(plan.components)] if incremental
                     else ["--output", str(artifact)])
        result = subprocess.run(
            [
                str(sandbox),
                "-f",
                str(profile_path),
                "/bin/bash",
                str(script),
                *arguments,
            ],
            cwd=str(candidate),
            env=environment,
            capture_output=True,
            text=True,
            timeout=remaining,
        )
        atomic_write_text(
            update_dir / "build.log",
            (result.stdout + result.stderr)[-200_000:],
        )
        if result.returncode == 0 and not incremental:
            _complete_icon_probe(
                candidate,
                update_dir,
                deadline=record.request.created_at + record.request.timeout_seconds,
            )
        if result.returncode == 0 and (not incremental or "python" in plan.components):
            # A rebuilt runtime defers the browser probe to this trusted check.
            _complete_browser_probe(
                artifact,
                update_dir,
                deadline=record.request.created_at + record.request.timeout_seconds,
            )
        elif result.returncode == 0:
            _validate_artifact_protocols(artifact)
    if result.returncode != 0:
        raise RuntimeError(f"candidate packaging failed with exit {result.returncode}")
    if not artifact.is_dir() or artifact.is_symlink():
        raise RuntimeError("candidate packaging did not produce one App artifact")
    _validate_registered_worktree(
        source, candidate, record.request.candidate_sha, worktree.branch_name
    )
    _validate_candidate_snapshot(candidate, record.request.candidate_sha)
    # Deferred browser verification rewrites resources. Sign after those writes,
    # and bind the final signature into the artifact digest used by installation.
    sign_app(artifact)
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


def _installed_revision(app: Path) -> str | None:
    from ..delivery.activation import installed_revision

    return installed_revision(app)


def _activation_plan(record: UpdateRecord, update_dir: Path, candidate: Path):
    """Choose incremental or complete packaging and the restart it needs."""
    from ..delivery.activation import full_plan
    from ..delivery.activation import plan_for

    installer = update_dir / "controller" / "install-app.sh"
    try:
        supports = b"--restart=" in installer.read_bytes()
    except OSError:
        supports = False
    if not supports:
        return full_plan("the installed App's installer predates partial restarts",
                         _installed_revision(Path(DEFAULT_APP_PATH)))
    return plan_for(Path(DEFAULT_APP_PATH), candidate, record.request.candidate_sha)


def _validate_artifact_protocols(artifact: Path) -> None:
    from ..delivery.package_protocol import validate_ui_package

    validate_reopen_package(artifact)
    ui_protocol = artifact / "Contents/Resources/update/ui-verification-protocol.json"
    if ui_protocol.exists() or ui_protocol.is_symlink():
        validate_ui_package(artifact)


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


def _running_work():
    """Yield a description of each piece of work that blocks a worker restart.

    Lazy, because polling only needs to know whether there is a first one.
    """
    from openprogram.runtime.jobs.store import list_jobs
    from openprogram.runtime.jobs.types import JobStatus
    from openprogram.state.store import default_store

    from openprogram.runtime.execution.store import default_store as execution_store
    from openprogram.runtime.jobs.resource_governance import ResourceGovernor
    from openprogram.models.usage.ledger import default_ledger

    # The supervisor is its own process, so each poll reads the Worker's
    # logs again. SessionStore's list index is process-local too.
    executions = execution_store().fresh_view()
    if ResourceGovernor(default_ledger).has_live_jobs():
        yield "background jobs holding a run slot"
    active = [item for item in executions.list_nonterminal() if item.current_attempt_id is not None or item.status.value in {
        "running", "pausing", "cancelling",
    }]
    for item in active:
        yield f"execution {item.execution_id} ({item.status.value}) in conversation {item.session_id}"
    sessions = default_store()
    # Refresh the legacy projection inventory as well. Canonical paused
    # foreground turns can legitimately retain a running session summary.
    from openprogram.state.store import SessionStore
    if isinstance(sessions, SessionStore):
        fresh = SessionStore(sessions.root_path)
        try:
            inventory = fresh.list_sessions(include_archived=True, limit=100_000)
        finally:
            fresh.close()
    else:
        inventory = sessions.list_sessions(include_archived=True, limit=100_000)
    for session in inventory:
        history = executions.list_for_session(session["id"])
        if session.get("status") == "running" and not history:
            yield f"a turn in conversation {session['id']}"
        try:
            jobs = list_jobs(session["id"], status_filter={JobStatus.RUNNING}, limit=100_000, lock_timeout=1.0)
        except TimeoutError:
            # A held lock must not stall the poll past its deadline, and
            # what cannot be read is not known to be quiet.
            yield f"conversation {session['id']} (locked by another process)"
            continue
        for job in jobs:
            canonical = executions.get_execution(job.id)
            if canonical is None or canonical.current_attempt_id is not None:
                yield f"job {job.id} in conversation {session['id']}"


def active_work() -> list[str]:
    """What is running right now, by the rule ``_wait_for_quiescence`` waits on."""
    return list(dict.fromkeys(_running_work()))


def _wait_for_quiescence(deadline: float) -> bool:
    while time.time() < deadline:
        if next(_running_work(), None) is None:
            return True
        time.sleep(0.2)
    return False


def _installer_command(
    argument: Path,
    update_dir: Path,
    installer_sha256: str,
    mode: str,
    *,
    timeout_seconds: float | None = None,
) -> str:
    installer = _installer_snapshot(update_dir, installer_sha256)
    prefix = [f"--reopen-update={_validate_update_id(update_dir.name)}"] if mode == "--prepare" else []
    plan = load_plan(update_dir) if mode == "--prepare" else None
    if plan is not None and plan.restart != "app":
        prefix.append(f"--restart={plan.restart}")
    result = subprocess.run(
        ["/bin/bash", str(installer), *prefix, mode, str(argument)],
        capture_output=True,
        text=True,
        timeout=timeout_seconds if timeout_seconds is not None else 30 if mode.startswith("--verify-terminal:") else 300,
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


def _validate_reopen_packages(artifact: Artifact, update_dir: Path, installer_sha256: str) -> None:
    if _tree_digest(artifact.path) != artifact.sha256:
        raise RuntimeError("candidate artifact changed after validation")
    _installer_snapshot(update_dir, installer_sha256)
    validate_reopen_package(artifact.path)
    previous = validate_reopen_package(Path(DEFAULT_APP_PATH))
    if previous["bindings"]["installer"]["sha256"] != installer_sha256:
        raise ValueError("frozen installer does not match the installed reopen protocol")
    if (update_dir / "request.json").exists():
        from ..verification.verifier_config import CONFIG_EVIDENCE_PREFIX
        from ..verification.verifier_config import load_verifier_config
        from ..delivery.package_protocol import validate_ui_package
        from ..verification.verification_plan import required_ui_protocol
        store = SelfUpdateStore(update_dir.parent)
        record = store.load(update_dir.name)
        config = (load_verifier_config(store, record)
                  if any(item.startswith(CONFIG_EVIDENCE_PREFIX) for item in record.request.pre_update_evidence) else {})
        if any(check["entry"] == "ui:main" for check in config.get("verification_plan", {}).get("checks", [])):
            for app in (artifact.path, Path(DEFAULT_APP_PATH)):
                package = validate_ui_package(app)
                if package["protocol"] < required_ui_protocol(config["verification_plan"]["checks"]):
                    raise ValueError("App does not support guarded UI interactions")


def _prepare_install(artifact: Artifact, update_dir: Path, installer_sha256: str) -> str:
    _validate_reopen_packages(artifact, update_dir, installer_sha256)
    return _installer_command(artifact.path, update_dir, installer_sha256, "--prepare")


def _prepared_reopen_transaction(transaction: Path, update_dir: Path) -> None:
    from .commit_intent import read_journal

    journal = read_journal(transaction)
    if journal["phase"] != "prepared" or journal.get("reopen_update_id") != _validate_update_id(update_dir.name):
        raise ValueError("prepared transaction does not match the reopen update")


def _prepare_reopen_activation(store: SelfUpdateStore, update_id: str, artifact: Artifact,
                               transaction: Path, installer_sha256: str) -> None:
    from .reopen import prepare_reopen

    update_dir = store.root / update_id
    # Recheck even on READY re-entry: installed/candidate bytes may have changed
    # while the controller was stopped or waiting for other sessions to finish.
    _validate_reopen_packages(artifact, update_dir, installer_sha256)
    _prepared_reopen_transaction(transaction, update_dir)
    prepare_reopen(store, update_id)


def _activate(transaction: Path, update_dir: Path, installer_sha256: str) -> str:
    transaction = _validate_transaction_path(transaction)
    _prepared_reopen_transaction(transaction, update_dir)
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
    if phase in {UpdatePhase.PREPARING, UpdatePhase.BUILDING, UpdatePhase.READY}:
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
    from openprogram.operations.self_update.control.rollback_intent import begin_rollback
    from openprogram.operations.self_update.verification.system_probe import probe_restored_system

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
    plan = load_plan(store.root / update_id)
    if target is UpdatePhase.ROLLED_BACK and plan is not None and plan.restart == "hot":
        # The kept worker learns of the rollback only from this request.
        from openprogram.operations.self_update.verification.system_probe import request_dispatch
        leave_maintenance(update_id)
        try:
            request_dispatch(store.load(update_id))
        except Exception:
            # The next worker startup dispatches the diagnosis instead.
            logging.getLogger(__name__).warning(
                "rollback diagnosis dispatch deferred to the next worker start", exc_info=True,
            )


def _finish_verification(store: SelfUpdateStore, update_id: str, installer_sha256: str, grant: dict) -> int:
    from openprogram.operations.self_update.control.commit_intent import begin_commit
    from openprogram.operations.self_update.verification.verification_channel import consume_result
    from openprogram.operations.self_update.verification.system_probe import probe_system

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
        begin_commit(store, update_id, transaction, installer_sha256, receipt, gate)
        if time.monotonic() >= deadline or time.time() >= grant["deadline"]:
            raise RuntimeError("verification deadline expired before commit")
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
            recovered = _resume_commit(store, update_id, installer_sha256)
            if recovered is not None:
                return recovered
            _rollback(store, update_id, installer_sha256, exc,
                      verdict=receipt["verdict"] if receipt is not None else "inconclusive")
        if store.load(update_id).state.phase is UpdatePhase.ROLLED_BACK:
            leave_maintenance(update_id)
        return 1


def _resume_commit(store: SelfUpdateStore, update_id: str, installer_sha256: str) -> int | None:
    from .commit_intent import commit_pending
    from .commit_intent import load_commit
    from .commit_intent import read_journal
    from .rollback_intent import load_rollback_intent
    from ..verification.system_probe import probe_committed_system

    record = store.load(update_id)
    pending = commit_pending(store, record)
    if not pending and (record.state.phase is not UpdatePhase.VERIFYING
                        or not record.state.detail.get("transaction_dir")):
        return None
    try:
        if load_rollback_intent(store, record) is not None:
            return None
        transaction = _validate_transaction_path(Path(record.state.detail["transaction_dir"]))
        if not pending:
            journal_path = transaction / "transaction.json"
            if journal_path.exists() or journal_path.is_symlink():
                if read_journal(transaction)["phase"] in {"committing", "committed"}:
                    raise ValueError("irreversible commit is missing its accepted decision")
            return None
        _, journal = load_commit(store, record, transaction, installer_sha256)
        if journal["phase"] == "activated":
            return None  # A first commit still requires the live original grant.
        reported = _installer_command(transaction, store.root / update_id, installer_sha256, "--commit")
        if reported != str(transaction):
            raise RuntimeError("installer committed a different transaction")
        gate = probe_committed_system(record)
        store.transition(update_id, UpdatePhase.SUCCEEDED, expected_phase=UpdatePhase.VERIFYING,
                         detail={**record.state.detail, "verifier_verdict": "pass", "rollback_available": False,
                                 "committed_system_gate": gate, "commit_recovered": True,
                                 "verifier_result": f"verifier-result-{record.state.attempt}.json"})
        leave_maintenance(update_id)
        return 0
    except Exception as exc:
        # The previous App may already be deleted; do not invent a rollback.
        if store.load(update_id).state.phase not in TERMINAL_PHASES:
            store.transition(update_id, UpdatePhase.NEEDS_MANUAL_RECOVERY, expected_phase=record.state.phase,
                             detail={**record.state.detail, "recovery_error": str(exc)[:2000]})
        return 1


def _resume_activated(store: SelfUpdateStore, update_id: str, installer_sha256: str) -> int:
    from ..verification.verification_channel import load_grant
    from ..verification.system_probe import probe_system

    try:
        recovered = _resume_commit(store, update_id, installer_sha256)
        if recovered is not None:
            return recovered
        with store._locked():
            record = store._load_unlocked(update_id)
            if record.state.phase is UpdatePhase.ACTIVATING:
                raise RuntimeError("controller interrupted during activation")
            grant = load_grant(store, record)
        gate = probe_system(record)
        if gate["worker_pid"] != grant["worker_pid"]:
            raise RuntimeError("candidate worker changed during controller interruption")
        return _finish_verification(store, update_id, installer_sha256, grant)
    except Exception as exc:
        _rollback(store, update_id, installer_sha256, exc)
        if store.load(update_id).state.phase is UpdatePhase.ROLLED_BACK:
            leave_maintenance(update_id)
        return 1


def _ready_artifact(record: UpdateRecord, update_dir: Path) -> Artifact:
    path = update_dir / "artifact" / "OpenProgram.app"
    if (record.state.detail.get("artifact_path") != str(path)
        or path.parent.is_symlink() or path.is_symlink() or not path.is_dir()
        or _tree_digest(path) != record.state.detail.get("artifact_sha256")):
        raise RuntimeError("prepared candidate artifact changed during interruption")
    return Artifact(path, record.state.detail["artifact_sha256"])


def _resume_terminal(store: SelfUpdateStore, update_id: str, installer_sha256: str) -> int:
    """Recheck a terminal outcome without changing it or repeating activation."""
    from .commit_intent import load_commit
    from .commit_intent import read_journal
    from .maintenance import load_maintenance
    from .maintenance import _leave_maintenance_unlocked
    from .rollback_intent import load_rollback_intent
    from ..verification.system_probe import probe_committed_system
    from ..verification.system_probe import probe_restored_system
    from ..verification.system_probe import probe_unchanged_system

    record = store.load(update_id)
    directory = store.root / update_id
    try:
        with store._locked():
            marker = load_maintenance(store)
            if marker is None:
                return 0
            if record.state.phase is UpdatePhase.NEEDS_MANUAL_RECOVERY:
                return 1
            if marker["update_id"] != update_id or store._load_active_unlocked() is not None:
                raise ValueError("terminal maintenance ownership changed")
        transaction = _validate_transaction_path(Path(record.state.detail["transaction_dir"]))
        def evidence():
            journal = read_journal(transaction)
            if record.state.phase is UpdatePhase.SUCCEEDED:
                decision, journal = load_commit(store, record, transaction, installer_sha256)
                if journal["phase"] != "committed" or load_rollback_intent(store, record) is not None:
                    raise ValueError("successful update is not committed")
                return journal, decision
            if record.state.phase is UpdatePhase.ROLLED_BACK:
                intent = load_rollback_intent(store, record)
                gate = record.state.detail.get("restored_system_gate", {})
                from .recovery import SYSTEM_CHECKS
                if (intent is None or journal["phase"] != "rolled_back"
                    or gate.get("candidate_sha") != intent["previous_revision"]
                    or gate.get("attempt") != record.state.attempt
                    or not intent["started_at"] <= gate.get("verified_at", 0) < intent["deadline"]
                    or gate.get("checks") != {key: True for key in SYSTEM_CHECKS}):
                    raise ValueError("rolled-back update lacks verified restoration")
                return journal, intent
            if record.state.phase is not UpdatePhase.ABORTED or journal["phase"] != "prepared":
                raise ValueError("aborted update is not an unchanged prepared transaction")
            return journal, None

        journal, proof = evidence()
        mode = "--verify-terminal:" + journal["phase"]
        if _installer_command(transaction, directory, installer_sha256, mode) != str(transaction):
            raise ValueError("terminal verification reported another transaction")
        if record.state.phase is UpdatePhase.SUCCEEDED:
            gate = probe_committed_system(record)
        elif record.state.phase is UpdatePhase.ROLLED_BACK:
            gate = probe_restored_system(record, proof["previous_revision"])
        else:
            gate = probe_unchanged_system(record)
        if _installer_command(transaction, directory, installer_sha256, mode) != str(transaction):
            raise ValueError("terminal verification reported another transaction")
        with store._locked():
            if (load_maintenance(store) != marker or store._load_active_unlocked() is not None
                or store._load_unlocked(update_id) != record or evidence() != (journal, proof)):
                raise ValueError("terminal recovery evidence changed during verification")
            store._write_json(directory / f"maintenance-cleanup-{record.state.attempt}.json", {
                "schema": 1, "update_id": update_id, "attempt": record.state.attempt,
                "phase": record.state.phase.value, "at": time.time(), "system_gate": gate,
            })
            _leave_maintenance_unlocked(store, update_id)
        return 0
    except Exception as exc:
        store._write_json(directory / f"maintenance-error-{record.state.attempt}.json", {
            "schema": 1, "update_id": update_id, "attempt": record.state.attempt,
            "at": time.time(), "error": str(exc)[:1000],
        })
        return 1


def run_supervisor(
    update_id: str,
    *,
    state_root: Path,
    installer_sha256: str,
) -> int:
    """Build and activate a candidate, then release system-gated verification."""
    from openprogram.operations.self_update.verification.system_probe import probe_system
    from openprogram.operations.self_update.verification.system_probe import probe_current_system
    from openprogram.operations.self_update.verification.system_probe import request_dispatch
    from openprogram.operations.self_update.verification.verification_channel import issue_grant
    from openprogram.operations.self_update.verification.verification_channel import _digest

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
        if record.state.phase in TERMINAL_PHASES:
            from ..repair.owner_repair import run_repair
            repaired = run_repair(store, record, installer_sha256)
            if repaired is not None:
                return repaired
            return _resume_terminal(store, update_id, installer_sha256)
        if record.state.phase in {UpdatePhase.ACTIVATING, UpdatePhase.VERIFYING}:
            return _resume_activated(store, update_id, installer_sha256)
        if record.state.phase is UpdatePhase.PREPARING:
            record = _wait_for_build(store, update_id)
            if record is None:
                current = store.load(update_id)
                _abort(store, update_id, current.state.phase, "turn release timed out")
                return 1
        if record.state.phase not in {UpdatePhase.BUILDING, UpdatePhase.READY}:
            return 1

        try:
            if record.state.phase is UpdatePhase.READY:
                artifact = _ready_artifact(record, update_dir)
                transaction = _validate_transaction_path(Path(record.state.detail["transaction_dir"]))
                state = record.state
            else:
                artifact = _build_candidate(record, update_dir)
                transaction = _validate_transaction_path(
                    Path(_prepare_install(artifact, update_dir, installer_sha256))
                )
                state = store.transition(
                    update_id,
                    UpdatePhase.READY,
                    expected_phase=UpdatePhase.BUILDING,
                    detail={
                        "artifact_path": str(artifact.path),
                        "artifact_sha256": artifact.sha256,
                        "transaction_dir": str(transaction),
                    },
                )
        except Exception as exc:
            _abort(store, update_id, record.state.phase, exc)
            if record.state.phase is UpdatePhase.READY:
                leave_maintenance(update_id)
            return 1

        maintenance_entered = False
        plan = load_plan(update_dir)
        hot = plan is not None and plan.restart == "hot"
        try:
            deadline = min(
                record.request.created_at + record.request.timeout_seconds,
                state.updated_at + 600,
            )
            # A hot swap stops nothing, so running turns need no safe point.
            if not hot:
                enter_maintenance(update_id)
                maintenance_entered = True
            if not hot and not _wait_for_quiescence(deadline):
                _abort(store, update_id, state.phase, "quiescence timed out")
                leave_maintenance(update_id)
                return 1
            previous_system_gate = probe_current_system(store.load(update_id))
            if time.time() >= deadline:
                raise RuntimeError("activation deadline expired during preflight")
            if previous_system_gate["candidate_sha"] == record.request.candidate_sha:
                raise RuntimeError("candidate is already the running revision")
            _prepare_reopen_activation(store, update_id, artifact, transaction, installer_sha256)
            if time.time() >= deadline:
                raise RuntimeError("activation deadline expired during reopen preflight")
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
            if hot:
                # No worker startup follows a hot swap to admit the verifier.
                request_dispatch(store.load(update_id))
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
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--installer-sha256")
    parser.add_argument("update_id")
    args = parser.parse_args(argv)
    if args.installer_sha256 is None:
        from .switch import run
        return run(args.update_id)
    try:
        return run_supervisor(
            args.update_id,
            state_root=args.state_root,
            installer_sha256=args.installer_sha256,
        )
    finally:
        from ..delivery.bootstrap import cleanup_bootstrap
        store = _canonical_store(args.state_root)
        cleanup_bootstrap(store, args.update_id)
        _retire_finished_job(store, args.update_id)


def _retire_finished_job(store: SelfUpdateStore, update_id: str) -> None:
    """Unload this supervisor's launchd job once its update needs nothing more.

    ``launchctl submit`` keeps the job alive, so a finished supervisor would
    otherwise be restarted every few seconds, forever, contending for the
    state lock with the next update. Removing the job stops this process too,
    so it is the last thing the supervisor does.
    """
    from ..delivery.bootstrap import _finished
    from ..delivery.launcher import _launchctl

    try:
        if not _finished(store, store.load(update_id)):
            return
    except Exception:  # noqa: BLE001 - an unreadable state keeps the job for recovery.
        return
    _launchctl("remove", f"ai.openprogram.self-update.{update_id}")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["Artifact", "run_supervisor", "main"]
