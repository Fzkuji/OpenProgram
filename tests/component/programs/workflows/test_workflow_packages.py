"""Workflow form: what a role call sends, loading by Git state, and check."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import textwrap
import uuid

import pytest

from openprogram.runtime.context import Context
from openprogram.programs._authoring.testing import script_roles

WORKFLOW = '''
"""Probe workflow."""
from openprogram import Agent, Context
from openprogram.runtime.context import call_path

writer = Agent(instructions="You write reports.", tools=[])
reviewer = Agent(instructions="You check facts.", tools=[])


def report(notes: str) -> str:
    """Turn notes into a report."""
    ctx = Context({"notes": notes})
    draft = write_draft(ctx)
    return check(draft, ctx)


def write_draft(ctx):
    """Write three sections at most."""
    return writer("Write the report", context=ctx)


def check(draft, ctx):
    """Only check facts. Keep the style."""
    return reviewer("Return the final text", context=ctx.derive({"draft": draft}))


def two_turns(notes: str) -> str:
    ctx = Context({"notes": notes}, history="thread")
    writer("First pass", context=ctx)
    return writer("Second pass", context=ctx)


def located(notes: str) -> str:
    return writer("Go", context=Context({"notes": notes}, providers={"call path": call_path}))


PROGRAM_ENTRIES = [report]
'''


def _write_package(root, name, *, test_body=None):
    package = root / name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        f"from .workflow import PROGRAM_ENTRIES, report\n", encoding="utf-8")
    (package / "workflow.py").write_text(WORKFLOW, encoding="utf-8")
    if test_body is not None:
        (package / "tests").mkdir()
        (package / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (package / "tests" / "test_probe.py").write_text(textwrap.dedent(test_body), encoding="utf-8")
    return package


def _git(directory, *args):
    subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"})


def _mark_checked(directory):
    """What a passing ``openprogram programs check`` records."""
    _git(directory, "update-ref", "refs/openprogram/checked", "HEAD")


@pytest.fixture
def workflow_module(tmp_path):
    from openprogram.runtime.agent.workflow_site import add_workflow_root

    name = f"wf_probe_{uuid.uuid4().hex[:8]}"
    package = _write_package(tmp_path, name)
    add_workflow_root(tmp_path)
    spec = importlib.util.spec_from_file_location(f"{name}.workflow", package / "workflow.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def test_each_call_carries_only_its_own_function_docstring(workflow_module):
    wf = workflow_module
    with script_roles({wf.writer: ["Draft"], wf.reviewer: ["Final"]}) as calls:
        assert wf.report("Shipped the parser.") == "Final"

    written, checked = calls[wf.writer][0], calls[wf.reviewer][0]
    assert "Write three sections at most." in written.text
    assert "Only check facts" not in written.text
    assert "Only check facts. Keep the style." in checked.text
    assert "Write three sections at most." not in checked.text
    assert "Shipped the parser." in checked.text and "Draft" in checked.text
    assert "Write the report" not in checked.text
    assert checked.system.startswith("You check facts.")
    assert written.history_text == "" and checked.history_text == ""


def test_thread_history_carries_earlier_calls_on_the_same_context(workflow_module):
    wf = workflow_module
    with script_roles({wf.writer: ["One", "Two"]}) as calls:
        assert wf.two_turns("notes") == "Two"

    first, second = calls[wf.writer]
    assert first.history_text == ""
    assert "First pass" in second.history_text and "One" in second.history_text
    assert second.prompt == "Second pass" or "Second pass" in second.text


def test_call_path_provider_lists_workflow_functions(workflow_module):
    wf = workflow_module
    with script_roles({wf.writer: ["ok"]}) as calls:
        wf.located("n")
    assert "located" in calls[wf.writer][0].text


def test_context_rejects_unknown_history_values():
    with pytest.raises(TypeError):
        Context(history="dag")
    assert Context(history=None).history is None
    assert Context().history == "chat"


def test_only_a_checked_commit_loads(tmp_path):
    from openprogram.programs._host import packages

    name = f"wf_load_{uuid.uuid4().hex[:8]}"
    package = _write_package(tmp_path, name)
    # A unique tool name: other tests may have registered a ``report`` tool.
    with open(package / "__init__.py", "a", encoding="utf-8") as handle:
        handle.write(f"ENTRY_OPTIONS = {{'report': {{'name': '{name}_report'}}}}\n")
    _git(package, "init", "-q")
    try:
        status = packages.load_user_packages(tmp_path)[name]
        assert status.state == "pending" and status.entries == []

        _git(package, "add", "-A")
        _git(package, "commit", "-qm", "first")
        status = packages.load_user_packages(tmp_path)[name]
        assert status.state == "pending" and "openprogram programs check" in status.note

        _mark_checked(package)
        status = packages.load_user_packages(tmp_path)[name]
        assert status.state == "loaded", status.error
        assert status.entries == [f"{name}_report"]

        with open(package / "workflow.py", "a", encoding="utf-8") as handle:
            handle.write("\nBROKEN = (\n")
        status = packages.load_user_packages(tmp_path)[name]
        assert status.state == "loaded"
        assert status.note == "Uncommitted changes. The last checked version is still loaded."
    finally:
        packages._unregister(name)
        packages._evict(name)
        packages._status.pop(name, None)
        packages._fingerprints.pop(name, None)
        packages._finder.packages.pop(name, None)


def test_reload_and_removal_dispose_the_workflow_context(tmp_path):
    from openprogram.programs._host import registry as _registry
    from openprogram.programs._host import runtime as _runtime
    from openprogram.programs._host import packages

    name = f"wf_ctx_{uuid.uuid4().hex[:8]}"
    package = _write_package(tmp_path, name)
    with open(package / "__init__.py", "a", encoding="utf-8") as handle:
        handle.write(f"ENTRY_OPTIONS = {{'report': {{'name': '{name}_a'}}}}\n")
    _git(package, "init", "-q")
    _git(package, "add", "-A")
    _git(package, "commit", "-qm", "first")
    try:
        packages.load_user_packages(tmp_path)
        assert _registry.program_tools(name) == [f"{name}_a"]
        assert _runtime.get(f"{name}_a")._workflow_owner == name

        # A committed rename reloads: the old tool goes, only the new one stays.
        init = (package / "__init__.py").read_text(encoding="utf-8")
        (package / "__init__.py").write_text(init.replace(f"{name}_a", f"{name}_b"), encoding="utf-8")
        _git(package, "commit", "-qam", "rename")
        _mark_checked(package)
        packages.load_user_packages(tmp_path)
        assert _registry.program_tools(name) == [f"{name}_b"]
        assert _runtime.get(f"{name}_a") is None and _runtime.get(f"{name}_b") is not None

        # Deleting the package disposes its context.
        shutil.rmtree(package)
        packages.load_user_packages(tmp_path)
        assert _registry.program_tools(name) == []
        assert _runtime.get(f"{name}_b") is None
    finally:
        packages._unregister(name)
        packages._evict(name)
        packages._status.pop(name, None)
        packages._fingerprints.pop(name, None)
        packages._finder.packages.pop(name, None)


def test_a_same_second_same_size_edit_reloads_from_source(tmp_path, monkeypatch):
    """Python trusts a .pyc whose source mtime (seconds) and size match; each commit has its own copy."""
    import os

    from openprogram.programs._host import registry as _registry
    from openprogram.programs._host import packages

    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    name = f"wf_pyc_{uuid.uuid4().hex[:8]}"
    package = _write_package(tmp_path, name)
    init = package / "__init__.py"
    with open(init, "a", encoding="utf-8") as handle:
        handle.write(f"ENTRY_OPTIONS = {{'report': {{'name': '{name}_a'}}}}\n")
    _git(package, "init", "-q")
    _git(package, "add", "-A")
    _git(package, "commit", "-qm", "first")
    try:
        packages.load_user_packages(tmp_path)
        assert _registry.program_tools(name) == [f"{name}_a"]
        assert not list(package.glob("__pycache__/*.pyc")), "the working copy is not what runs"

        stat = init.stat()
        init.write_text(init.read_text(encoding="utf-8").replace(f"{name}_a", f"{name}_b"), encoding="utf-8")
        os.utime(init, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        _git(package, "commit", "-qam", "rename")
        _mark_checked(package)
        packages.load_user_packages(tmp_path)
        assert _registry.program_tools(name) == [f"{name}_b"]
    finally:
        packages._unregister(name)
        packages._evict(name)
        packages._status.pop(name, None)
        packages._fingerprints.pop(name, None)
        packages._finder.packages.pop(name, None)


def test_bytecode_caches_do_not_make_a_package_dirty(tmp_path):
    from openprogram.programs._host import packages

    package = _write_package(tmp_path, f"wf_cache_{uuid.uuid4().hex[:8]}")
    _git(package, "init", "-q")
    _git(package, "add", "-A")
    _git(package, "commit", "-qm", "first")
    # What importing the package leaves behind when bytecode is written.
    (package / "__pycache__").mkdir()
    (package / "__pycache__" / "workflow.cpython-312.pyc").write_bytes(b"\0")
    head, dirty = packages.git_state(package)
    assert head and not dirty
    # Committed by a bare ``git add -A``, then rewritten by the next import.
    _git(package, "add", "-A")
    _git(package, "commit", "-qm", "with caches")
    (package / "__pycache__" / "workflow.cpython-312.pyc").write_bytes(b"\1")
    assert not packages.git_state(package)[1]

    (package / "notes.md").write_text("draft", encoding="utf-8")
    assert packages.git_state(package)[1]


def test_check_commits_only_when_tests_pass(tmp_path):
    from openprogram.programs._host.check import CheckFailed, check_program

    name = f"wf_check_{uuid.uuid4().hex[:8]}"
    package = _write_package(tmp_path, name, test_body="""
        def test_fails():
            assert False, "scripted failure"
    """)
    _git(package, "init", "-q")
    with pytest.raises(CheckFailed) as failed:
        check_program(package)
    assert failed.value.report["tests"]["passed"] is False
    assert subprocess.run(["git", "-C", str(package), "rev-parse", "-q", "--verify", "HEAD"],
                          capture_output=True).returncode != 0

    (package / "tests" / "test_probe.py").write_text("def test_passes():\n    assert True\n", encoding="utf-8")
    report = check_program(package, message="First version")
    assert report["ok"] and report["entries"] == ["report"]
    log = subprocess.run(["git", "-C", str(package), "log", "--format=%s"],
                         capture_output=True, text=True).stdout
    assert log.strip() == "First version"
    checked = subprocess.run(["git", "-C", str(package), "rev-parse", "refs/openprogram/checked"],
                             capture_output=True, text=True).stdout.strip()
    assert checked == report["revision"]


def test_resumable_entries_are_synchronous_subprocess_tools():
    import types

    from openprogram.programs._host.packages import entry_problems

    async def later(x: str) -> str:
        """Async entry."""
        return x

    def here(x: str) -> str:
        """Worker entry."""
        return x

    module = types.SimpleNamespace(
        PROGRAM_ENTRIES=[later, here],
        ENTRY_OPTIONS={"later": {"resumable": True},
                       "here": {"resumable": True, "run_in_worker": True}},
    )
    problems = entry_problems(module)
    assert any("later: a resumable entry must be synchronous" in p for p in problems)
    assert any("here: a resumable entry cannot run in the worker" in p for p in problems)
