"""Run approved CLI and candidate checks with native isolation and identity receipts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import time

from .package_protocol import _file, _read_or_hash

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


def admit_plan(plan, request) -> None:
    if any(check["entry"] in NATIVE_ENTRIES for check in plan["checks"]):
        from .supervisor import _sandbox_executable
        _sandbox_executable()
        runtime_identity(Path(request.app_path))
    for check in plan["checks"]:
        if check["entry"] == "test:python":
            candidate_identity(request, check)


def candidate_identity(request, check, *, frozen=None) -> dict:
    from openprogram.programs.tools.system import self_update as tool
    from openprogram.worktree.types import WorktreeStatus

    worktree = tool.get_manager().get_worktree(request.worktree_id)
    if (worktree is None or worktree.status is not WorktreeStatus.ACTIVE
            or worktree.parent_session != request.session_id or worktree.source_repo != request.repo):
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
    from .repair_candidate import _test
    from .system_probe import _probe_system

    end = time.monotonic() + min(check["timeout_seconds"], deadline - time.time())

    def remaining():
        revalidate()
        value = end - time.monotonic()
        if value <= 0:
            raise TimeoutError("native verification deadline expired")
        return value

    identity = runtime_identity(Path(record.request.app_path), expected_revision=record.request.candidate_sha)
    candidate = frozen = None
    if check["entry"] == "test:python":
        from .source_repair import _config
        frozen = _config(store, record)
        if frozen is None:
            raise ValueError("candidate test requires frozen source identity")
        candidate = candidate_identity(record.request, check, frozen=frozen)
        argv = [identity["python"], "-I", "-B", str(Path(candidate["path"]) / candidate["script"]), *check["argv"][1:]]
    else:
        argv = [identity["python"], "-I", "-B", "-m", "openprogram", *CLI_ENTRIES[check["entry"]]]
    before = _probe_system(record, record.request.candidate_sha, remaining())
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
    if runtime_identity(Path(record.request.app_path), expected_revision=record.request.candidate_sha) != identity:
        raise ValueError("installed runtime changed during verification")
    if candidate is not None and candidate_identity(record.request, check, frozen=frozen) != candidate:
        raise ValueError("candidate changed during verification")
    after = _probe_system(record, record.request.candidate_sha, remaining())
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


def validate_execution(observation, check, request, *, passed, candidate_config=None) -> None:
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
    root = Path(request.app_path) / "Contents/Resources/runtime"
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
