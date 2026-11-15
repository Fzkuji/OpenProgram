"""``openprogram programs check``: validate, test and commit a Program.

A Python package is imported in a separate process, its
``PROGRAM_ENTRIES`` are checked against the entry contract, and its
``tests/`` run under pytest, inside the OS sandbox when one is available
(no network, no writes to the package). When everything passes, the change
is committed to the package's own Git repository and the commit is recorded
in ``refs/openprogram/checked``. That ref is what makes it callable; a plain
``git commit`` is not loaded.

An application directory (``application.json``) is validated as the loader
would mount it: the manifest, the package tree and, for a Python backend,
an import of every declared operation in the application's environment.
A directory may be both; each part is checked.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import openprogram

_TEST_TIMEOUT = 600
_SOURCE_ROOT = str(Path(openprogram.__file__).resolve().parent.parent)

_PROBE = r'''
import json, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from openprogram.runtime.agent.workflow_site import add_workflow_root
add_workflow_root(sys.argv[1])
report = {"entries": [], "problems": []}
try:
    import importlib
    module = importlib.import_module(sys.argv[3])
    from openprogram.programs._host.packages import entry_problems
    report["problems"] = entry_problems(module)
    import inspect
    report["entries"] = [inspect.unwrap(entry).__name__ for entry in getattr(module, "PROGRAM_ENTRIES", []) or []
                         if callable(entry)]
except BaseException as exc:
    import traceback
    report["problems"].append("Import failed: " + "".join(traceback.format_exception_only(type(exc), exc)).strip())
    report["traceback"] = traceback.format_exc()[-4000:]
print("\n@@CHECK@@" + json.dumps(report))
'''

_RUNNER = r'''
import importlib, os, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from openprogram.runtime.agent.workflow_site import add_workflow_root
add_workflow_root(sys.argv[1])
# Register the entries as tools, as the worker does, so tests can call them
# through the tool registry.
from openprogram.programs._host.packages import register_entries
_name = os.path.basename(sys.argv[3].rstrip(os.sep))
register_entries(importlib.import_module(_name), owner=_name)
import pytest
raise SystemExit(pytest.main(["-q", "--tb=short", "-p", "no:cacheprovider",
                              "--rootdir", sys.argv[3], sys.argv[4]]))
'''


class CheckFailed(Exception):
    def __init__(self, message: str, report: dict):
        super().__init__(message)
        self.report = report


def _probe(directory: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="openprogram-program-probe-") as raw:
        script = Path(raw) / "probe.py"
        script.write_text(_PROBE, encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "-I", "-B", str(script), str(directory.parent), _SOURCE_ROOT, directory.name],
            capture_output=True, text=True, timeout=300, cwd=raw,
        )
    marker = completed.stdout.rfind("@@CHECK@@")
    if marker < 0:
        return {"entries": [], "problems": ["Import process failed:\n" + (completed.stderr or completed.stdout)[-4000:]]}
    return json.loads(completed.stdout[marker + len("@@CHECK@@"):])


def _test_files(directory: Path) -> list[Path]:
    tests = directory / "tests"
    if not tests.is_dir():
        return []
    return sorted(path for path in tests.rglob("test_*.py") if "__pycache__" not in path.parts)


def _run_tests(directory: Path) -> dict:
    from openprogram.host import sandbox

    with tempfile.TemporaryDirectory(prefix="openprogram-program-check-") as raw:
        instance = Path(raw)
        runner = instance / "run_tests.py"
        runner.write_text(_RUNNER, encoding="utf-8")
        (instance / "home").mkdir()
        (instance / "tmp").mkdir()
        command = shlex.join([
            sys.executable, "-I", "-B", str(runner),
            str(directory.parent), _SOURCE_ROOT, str(directory), str(directory / "tests"),
        ])
        env = dict(os.environ)
        sandboxed = sys.platform in {"darwin", "linux"} and not sandbox.unavailable_reason()
        if sandboxed:
            policy = sandbox.SandboxPolicy(
                deny_write=(*sandbox.DEFAULT_DENY_WRITE, str(directory), str(directory / "**")),
                network=False,
            )
            argv, shell = sandbox.wrap_command(
                command, str(instance), policy, private_tmp=True, allow_subprocesses=True,
                read_only_roots=(_SOURCE_ROOT, str(directory.parent), sys.prefix, sys.base_prefix),
            )
            env = sandbox.child_env(policy)
        else:
            argv, shell = command, True
        env.update({"HOME": str(instance / "home"), "TMPDIR": str(instance / "tmp"),
                    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
        try:
            completed = subprocess.run(argv, shell=shell, cwd=instance, env=env, capture_output=True,
                                       text=True, timeout=_TEST_TIMEOUT)
        except subprocess.TimeoutExpired:
            return {"ran": True, "passed": False, "sandboxed": sandboxed,
                    "output": f"Tests exceeded {_TEST_TIMEOUT} seconds."}
    output = (completed.stdout + completed.stderr)[-8000:]
    return {"ran": True, "passed": completed.returncode == 0, "sandboxed": sandboxed, "output": output}


def _git(directory: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True, timeout=60)


def _commit(directory: Path, message: str) -> tuple[str, bool]:
    if not (directory / ".git").exists():
        _git(directory, "init", "-q")
    ignore = directory / ".gitignore"
    if not ignore.exists():
        ignore.write_text("__pycache__/\n.pytest_cache/\n*.pyc\n", encoding="utf-8")
    _git(directory, "add", "-A")
    staged = _git(directory, "diff", "--cached", "--quiet").returncode != 0
    has_head = _git(directory, "rev-parse", "--verify", "-q", "HEAD").returncode == 0
    committed = False
    if staged or not has_head:
        identity = []
        if not _git(directory, "config", "user.email").stdout.strip():
            identity = ["-c", "user.name=OpenProgram", "-c", "user.email=programs@openprogram.local"]
        result = subprocess.run(["git", "-C", str(directory), *identity, "commit", "-q",
                                 "--allow-empty", "-m", message],
                                capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError("git commit failed: " + (result.stderr or result.stdout).strip())
        committed = True
    head = _git(directory, "rev-parse", "HEAD").stdout.strip()
    return head, committed


def _check_application(path: Path) -> dict:
    """Validate an application directory as the loader would mount it."""
    from openprogram.programs._host.ui import catalog

    definition = catalog.manifest(path)
    catalog.check_tree(path)
    if definition.get("backend"):
        catalog.check_backend(path, definition)
    operations = [name for name, spec in definition.get("operations", {}).items() if spec.get("agent")]
    return {"id": definition["id"], "title": definition["title"], "version": definition["version"],
            "digest": catalog.package_digest(path), "needs_trust": catalog.needs_trust(definition),
            "agent_operations": operations}


def check_program(directory: str | os.PathLike, *, message: str = "", commit: bool = True) -> dict:
    """Validate a Program directory; test and (on success) commit its package.

    The report names the ``kinds`` found (``package``, ``application``).
    A package is probed, tested, committed and recorded as checked; an
    application is validated. Raises :class:`CheckFailed` with the report on any problem.
    """
    from openprogram.state.program_directory import program_kinds
    from openprogram.programs._host.packages import GIT_MISSING, git_available, name_problem, record_checked

    path = Path(directory).expanduser().resolve()
    kinds = program_kinds(path)
    report: dict = {"ok": False, "program": path.name, "path": str(path), "kinds": kinds}
    if not kinds:
        report["problems"] = [f"{path} is neither a Python package (__init__.py) nor an application (application.json)."]
        raise CheckFailed(report["problems"][0], report)
    if "application" in kinds:
        try:
            report["application"] = _check_application(path)
        except (OSError, ValueError) as exc:
            report["problems"] = [f"application.json: {exc}"]
            raise CheckFailed(report["problems"][0], report) from None
    if "package" not in kinds:
        report["ok"] = True
        return report
    problem = name_problem(path.name, path)
    if problem:
        report["problems"] = [problem]
        raise CheckFailed(problem, report)
    probe = _probe(path)
    report["entries"] = probe.get("entries", [])
    report["problems"] = probe.get("problems", [])
    if report["problems"]:
        if probe.get("traceback"):
            report["traceback"] = probe["traceback"]
        raise CheckFailed("; ".join(report["problems"]), report)
    if _test_files(path):
        report["tests"] = _run_tests(path)
        if not report["tests"]["passed"]:
            raise CheckFailed("Tests failed.", report)
    else:
        report["tests"] = {"ran": False, "passed": True, "output": "No tests in tests/."}
    if commit:
        if not git_available():
            report["problems"] = [GIT_MISSING]
            raise CheckFailed(GIT_MISSING, report)
        report["revision"], report["committed"] = _commit(path, message or "Update program")
        try:
            record_checked(path, report["revision"])
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            report["problems"] = [f"Cannot record the checked commit: {exc}"]
            raise CheckFailed(report["problems"][0], report) from None
    report["ok"] = True
    return report


__all__ = ["CheckFailed", "check_program"]
