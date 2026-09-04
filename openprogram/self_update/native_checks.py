"""Run fixed installed-CLI checks with native isolation and identity receipts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import time

from .package_protocol import _file, _read_or_hash

CLI_ENTRIES = {"cli:version": ("--version",), "cli:help": ("--help",)}


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
    if any(check["entry"] in CLI_ENTRIES for check in plan["checks"]):
        from .supervisor import _sandbox_executable
        _sandbox_executable()
        runtime_identity(Path(request.app_path))


def observe_cli(store, record, check, deadline, revalidate) -> dict:
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
    argv = [identity["python"], "-I", "-B", "-m", "openprogram", *CLI_ENTRIES[check["entry"]]]
    before = _probe_system(record, record.request.candidate_sha, remaining())
    directory = store.root / record.request.update_id
    with tempfile.TemporaryDirectory(prefix="native-check-", dir=directory) as temporary:
        scratch = Path(temporary)
        cwd = scratch / "cwd"
        cwd.mkdir(mode=0o700)
        code, output = _test(argv, cwd, scratch, remaining(), remaining,
                             verification=True, output_limit=check["max_output_bytes"])
    # A receipt is emitted only after temporary resources and processes clean up.
    observed_at = time.time()
    if runtime_identity(Path(record.request.app_path), expected_revision=record.request.candidate_sha) != identity:
        raise ValueError("installed CLI changed during verification")
    after = _probe_system(record, record.request.candidate_sha, remaining())
    if before["worker_pid"] != after["worker_pid"]:
        raise ValueError("worker changed during native verification")
    remaining()
    return {"system_gate": after, "observation": {
        "entry": check["entry"], "status": code, "content_type": "text/plain",
        "body": output, "observed_at": observed_at,
        "execution": {"origin": "installed_app", "argv": argv, "cwd": "isolated_scratch",
                      "runtime": identity, "cleanup_complete": True},
    }}


def validate_execution(observation, check, request, *, passed) -> None:
    execution = observation.get("execution")
    if (not isinstance(execution, dict) or set(execution) != {"origin", "argv", "cwd", "runtime", "cleanup_complete"}
            or execution["origin"] != "installed_app" or execution["cwd"] != "isolated_scratch"
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
    if (not python.is_absolute() or ".." in python.parts or not python.is_relative_to(root)
            or str(python.parent.parent) != runtime["prefix"]
            or execution["argv"] != [str(python), "-I", "-B", "-m", "openprogram", *CLI_ENTRIES[check["entry"]]]):
        raise ValueError("native runtime receipt has an unexpected invocation")
