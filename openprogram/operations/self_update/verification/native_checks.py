"""Run approved CLI and candidate checks with native isolation and identity receipts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from ..types import DEFAULT_APP_PATH
from ..delivery.package_protocol import _file
from ..delivery.package_protocol import _read_or_hash
from ..types import UpdatePhase

CLI_ENTRIES = {"cli:version": ("--version",), "cli:help": ("--help",)}
NATIVE_ENTRIES = CLI_ENTRIES.keys() | {"test:python"}


def runtime_identity(app: Path, *, expected_revision=None) -> dict:
    """Read the actual installed prefix, never search PATH or execute metadata."""
    try:
        resources = _file(app, "Contents/Resources")
        manifest_path = _file(resources, "runtime/runtime-manifest.json")
        manifest_bytes = _read_or_hash(manifest_path, limit=16384, read=True)
        manifest = json.loads(manifest_bytes)
        if (not isinstance(manifest, dict) or type(manifest.get("schema")) is not int
                or manifest["schema"] != 2 or not isinstance(manifest.get("python"), str)):
            raise ValueError
        python = _file(resources, "runtime/" + manifest["python"])
        match = re.fullmatch(r"python(\d+\.\d+)", python.name)
        if python.parent.name != "bin" or not match or not os.access(python, os.X_OK):
            raise ValueError
        prefix = python.parent.parent
        package = _file(prefix, f"lib/python{match[1]}/site-packages/openprogram")
        marker = _read_or_hash(_file(package, "_build_revision.txt"), limit=80, read=True).decode("ascii").strip()
        app_marker = _read_or_hash(_file(resources, "openprogram-source-revision"), limit=80, read=True).decode("ascii").strip()
        if (not re.fullmatch(r"[0-9a-f]{40}(?:-dirty)?", marker) or marker != app_marker
                or expected_revision is not None and marker != expected_revision):
            raise ValueError
        return dict(python=str(python), prefix=str(prefix), revision=marker,
                    python_sha256=_read_or_hash(python, limit=64 * 1024 * 1024),
                    manifest_sha256=_read_or_hash(manifest_path, limit=16384),
                    cli_sha256=_read_or_hash(_file(package, "__main__.py"), limit=1_048_576))
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError("installed CLI runtime identity is unavailable or changed") from None


def version_identity(version_dir: Path, *, expected_revision=None) -> dict:
    """The same receipt for a sealed version: identity comes from ``version.json``."""
    from ..versions import layout as paths

    try:
        manifest_path = version_dir / "version.json"
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        revision = manifest["commit"]
        if not re.fullmatch(r"[0-9a-f]{40}", revision) or expected_revision not in (None, revision):
            raise ValueError
        python = paths.venv_python(version_dir / "venv")
        package = paths.site_packages(version_dir, manifest["base"]) / "openprogram"
        return dict(python=str(python), prefix=str(python.parent.parent), revision=revision,
                    python_sha256=hashlib.sha256(python.resolve().read_bytes()).hexdigest(),
                    manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                    cli_sha256=hashlib.sha256((package / "__main__.py").read_bytes()).hexdigest())
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError("sealed version identity is unavailable or changed") from None


def trial_version_dir(record) -> Path | None:
    """The version being tried, when the update is in its trial."""
    if record.state.phase is not UpdatePhase.TRIAL:
        return None
    return Path(record.state.detail["trial"]["version_dir"])


def identity_for(record, *, expected_revision=None) -> dict:
    version_dir = trial_version_dir(record)
    if version_dir is not None:
        return version_identity(version_dir, expected_revision=expected_revision)
    return runtime_identity(Path(DEFAULT_APP_PATH), expected_revision=expected_revision)


def admit_plan(plan, request) -> None:
    # Under sealed versions the trial's own identity proves the runtime, at the time it runs.
    if not request.controller_version and any(check["entry"] in NATIVE_ENTRIES for check in plan["checks"]):
        from ..control.supervisor import _sandbox_executable
        _sandbox_executable()
        runtime_identity(Path(DEFAULT_APP_PATH))
    for check in plan["checks"]:
        if check["entry"] == "test:python":
            candidate_identity(request, check)


def candidate_identity(request, check, *, frozen=None) -> dict:
    from openprogram.programs.tools.system import self_update as tool
    from openprogram.state.worktree.types import WorktreeStatus

    worktree = tool.get_manager().get_worktree(request.worktree_id)
    if (worktree is None or worktree.status is not WorktreeStatus.ACTIVE
            or worktree.source_repo != request.repo):
        raise ValueError("candidate test worktree ownership changed")
    source = tool._recorded_path(request.repo, "source repo")
    candidate = tool._recorded_path(worktree.worktree_path, "candidate worktree")
    if frozen is not None and (str(candidate) != frozen["candidate_path"] or worktree.branch_name != frozen["branch_name"]):
        raise ValueError("candidate test no longer matches its frozen worktree")
    tool._validate_registered_worktree(source, candidate, request.candidate_sha, worktree.branch_name)
    tool._validate_candidate_snapshot(candidate, request.candidate_sha)
    script = _file(candidate, check["argv"][0])
    tool._git(candidate, "ls-files", "--error-unmatch", "--", check["argv"][0])
    return dict(path=str(candidate), branch=worktree.branch_name, revision=request.candidate_sha,
                script=check["argv"][0], script_sha256=_read_or_hash(script, limit=1_048_576))


def observe_native(store, record, check, deadline, revalidate) -> dict:
    from ..repair.repair_candidate import _test
    from .system_probe import _probe_system
    from .system_probe import trial_target

    end = time.monotonic() + min(check["timeout_seconds"], deadline - time.time())

    def remaining():
        revalidate()
        value = end - time.monotonic()
        if value <= 0:
            raise TimeoutError("native verification deadline expired")
        return value

    identity = identity_for(record, expected_revision=record.request.candidate_sha)
    target = trial_target(record)
    candidate = frozen = None
    if check["entry"] == "test:python":
        from ..repair.source_repair import _config
        frozen = _config(store, record)
        if frozen is None:
            raise ValueError("candidate test requires frozen source identity")
        candidate = candidate_identity(record.request, check, frozen=frozen)
        argv = [identity["python"], "-I", "-B", str(Path(candidate["path"]) / candidate["script"]), *check["argv"][1:]]
    else:
        argv = [identity["python"], "-I", "-B", "-m", "openprogram", *CLI_ENTRIES[check["entry"]]]
    before = _probe_system(record, record.request.candidate_sha, remaining(), target=target)
    directory = store.root / record.request.update_id
    with tempfile.TemporaryDirectory(prefix="native-check-", dir=directory) as temporary:
        scratch = Path(temporary)
        cwd = Path(candidate["path"]) if candidate else scratch / "cwd"
        if candidate is None:
            cwd.mkdir(mode=0o700)
        code, output = _test(argv, cwd, scratch, remaining(), remaining,
                             verification=True, output_limit=check["max_output_bytes"])
    # A receipt is emitted only after temporary resources and processes clean up.
    observed_at = time.time()
    if identity_for(record, expected_revision=record.request.candidate_sha) != identity:
        raise ValueError("installed runtime changed during verification")
    if candidate is not None and candidate_identity(record.request, check, frozen=frozen) != candidate:
        raise ValueError("candidate changed during verification")
    after = _probe_system(record, record.request.candidate_sha, remaining(), target=target)
    if before["worker_pid"] != after["worker_pid"]:
        raise ValueError("worker changed during native verification")
    remaining()
    execution = {"origin": "installed_app", "argv": argv, "cwd": "isolated_scratch",
                 "runtime": identity, "cleanup_complete": True}
    if candidate is not None:
        execution.update(origin="candidate_test", cwd=candidate["path"], candidate=candidate)
    return {"system_gate": after, "observation": {
        "entry": check["entry"], "status": code, "content_type": "text/plain",
        "body": output, "observed_at": observed_at,
        "execution": execution,
    }}


def validate_execution(observation, check, request, *, passed, candidate_config=None, runtime_root=None) -> None:
    execution = observation.get("execution")
    test = check["entry"] == "test:python"
    keys = {"origin", "argv", "cwd", "runtime", "cleanup_complete"} | ({"candidate"} if test else set())
    if (not isinstance(execution, dict) or set(execution) != keys
            or execution["origin"] != ("candidate_test" if test else "installed_app")
            or execution["cleanup_complete"] is not True or type(observation.get("status")) is not int
            or passed and observation["status"] != 0):
        raise ValueError("native verification did not complete successfully")
    runtime = execution["runtime"]
    if (not isinstance(runtime, dict) or set(runtime) != {
        "python", "prefix", "revision", "python_sha256", "manifest_sha256", "cli_sha256",
    } or runtime["revision"] != request.candidate_sha
            or any(not isinstance(runtime[key], str) or not re.fullmatch(r"[0-9a-f]{64}", runtime[key])
                   for key in ("python_sha256", "manifest_sha256", "cli_sha256"))):
        raise ValueError("native runtime receipt does not match the candidate")
    python = Path(runtime["python"])
    root = runtime_root if runtime_root is not None else Path(DEFAULT_APP_PATH) / "Contents/Resources/runtime"
    if test:
        candidate = execution["candidate"]
        if (candidate_config is None or not isinstance(candidate, dict)
                or set(candidate) != {"path", "branch", "revision", "script", "script_sha256"}
                or candidate["path"] != candidate_config["candidate_path"]
                or candidate["branch"] != candidate_config["branch_name"]
                or candidate["revision"] != request.candidate_sha or candidate["script"] != check["argv"][0]
                or execution["cwd"] != candidate["path"] or not isinstance(candidate["script_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", candidate["script_sha256"])):
            raise ValueError("candidate test receipt does not match frozen source")
        expected = [str(python), "-I", "-B", str(Path(candidate["path"]) / candidate["script"]), *check["argv"][1:]]
    else:
        if execution["cwd"] != "isolated_scratch":
            raise ValueError("unexpected CLI working directory")
        expected = [str(python), "-I", "-B", "-m", "openprogram", *CLI_ENTRIES[check["entry"]]]
    if (not python.is_absolute() or ".." in python.parts or not python.is_relative_to(root)
            or str(python.parent.parent) != runtime["prefix"]
            or execution["argv"] != expected):
        raise ValueError("native runtime receipt has an unexpected invocation")
