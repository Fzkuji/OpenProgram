"""Manual Workflow testing and publication through the package contract."""
from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile

from ..errors import InvalidWorkflow
from . import repository, validation


_TEST_TIMEOUT = 60
_RUNNER = '''import importlib.machinery
import importlib.util
import resource
import sys
from pathlib import Path

resource.setrlimit(resource.RLIMIT_FSIZE, (16777216, 16777216))
import pytest
sys.path.insert(0, sys.argv[1])
import openprogram.programs
snapshot = Path(sys.argv[2])
spec = importlib.machinery.ModuleSpec("workflows", loader=None, is_package=True)
spec.submodule_search_locations = [str(snapshot / "workflows")]
sys.modules["workflows"] = importlib.util.module_from_spec(spec)
raise SystemExit(pytest.main(["-q", "--tb=short", "-p", "no:cacheprovider", "-c", "/dev/null", "--rootdir", str(snapshot), "--confcutdir", str(snapshot), str(snapshot / "workflows" / sys.argv[3] / "tests")]))
'''


def _run_tests(instance: Path, entrypoint: str) -> dict:
    import openprogram
    from openprogram import sandbox

    if sys.platform not in {"darwin", "linux"}:
        raise InvalidWorkflow("Workflow behavior tests require a macOS or Linux OS sandbox")
    if importlib.util.find_spec("pytest") is None:
        raise InvalidWorkflow("Workflow behavior tests require pytest in the OpenProgram Python environment")
    reason = sandbox.unavailable_reason()
    if reason:
        raise InvalidWorkflow(f"Workflow behavior-test sandbox is unavailable: {reason}")
    snapshot = instance / "snapshot"
    runner = instance / "run_tests.py"
    runner.write_text(_RUNNER, encoding="utf-8")
    home = instance / "home"
    temporary = instance / "tmp"
    home.mkdir()
    temporary.mkdir()
    policy = sandbox._with_hard_floor(sandbox.SandboxPolicy(
        deny_write=(*sandbox.DEFAULT_DENY_WRITE, str(snapshot), str(snapshot / "**"), str(runner)),
        network=False,
    ))
    command = shlex.join([
        sys.executable, "-I", "-B", str(runner),
        str(Path(openprogram.__file__).resolve().parent.parent),
        str(snapshot), entrypoint,
    ])
    argv, shell = sandbox.wrap_command(
        command, str(instance), policy, private_tmp=True, allow_subprocesses=False,
        read_only_roots=(str(Path(openprogram.__file__).resolve().parent.parent), sys.prefix, sys.base_prefix),
    )
    env = sandbox.child_env(policy)
    env.update({"HOME": str(home), "TMPDIR": str(temporary), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
    output_path = instance / "test-output.txt"
    with output_path.open("wb") as output:
        process = subprocess.Popen(
            argv, shell=shell, cwd=instance, env=env,
            stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            try:
                code = process.wait(timeout=_TEST_TIMEOUT)
            except subprocess.TimeoutExpired as exc:
                raise InvalidWorkflow(f"Workflow tests exceeded {_TEST_TIMEOUT} seconds") from exc
        finally:
            # macOS denies forks; Linux uses a private PID namespace.
            # Also terminate the original process group on every exit path.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    with output_path.open("rb") as output:
        output.seek(max(0, output_path.stat().st_size - 16_384))
        detail = output.read().decode("utf-8", errors="replace")
    if code != 0:
        raise InvalidWorkflow(f"Workflow behavior tests failed (exit {code}):\n{detail}")
    return {"executed_tests": True, "sandboxed": True, "test_output": detail}


@contextmanager
def _tested_candidate(directory: str | Path):
    report = validation.validate_workflow_directory(directory)
    project_id = report["workflow_id"]
    candidate = repository._read_repository_candidate(Path(directory), expected_project_id=project_id)
    with tempfile.TemporaryDirectory(prefix="openprogram-workflow-test-") as raw:
        instance = Path(raw) / "candidate"
        instance.mkdir()
        dependencies = repository._replace_snapshot(instance, candidate)
        tests = _run_tests(instance, project_id)
        yield instance, candidate, dependencies, {**report, **tests}


def test_workflow_directory(directory: str | Path) -> dict:
    """Test a copied candidate; never publish or modify the author's directory."""
    with _tested_candidate(directory) as (_, _, _, report):
        return report


def publish_workflow_directory(directory: str | Path, *, replace: bool = False) -> dict:
    """Publish the exact tested snapshot, requiring explicit replacement."""
    with _tested_candidate(directory) as (instance, candidate, dependencies, report):
        project_id, revision = repository._publish_snapshot(
            instance,
            project_id=report["workflow_id"] if replace else "",
            action="revise" if replace else "create",
            metadata=candidate["project_metadata"],
            workflow_dependencies=dependencies,
        )
        return {**report, "workflow_id": project_id, "revision": revision}
