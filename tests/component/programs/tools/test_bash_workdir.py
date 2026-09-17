"""One-shot workdir routing, authorization, and real subprocess regressions."""
from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from openprogram import sandbox
from openprogram.backend.base import RunResult
from openprogram.programs.tools.files.bash.workdir import prepare_command
from openprogram.worktree.context import current_worktree_path, reset_worktree, set_worktree


@pytest.fixture
def scope(tmp_path, monkeypatch):
    root = tmp_path / "workspace with spaces"
    child = root / "child with spaces"
    child.mkdir(parents=True)
    token = set_worktree(str(root))
    monkeypatch.setattr(sandbox, "resolve_policy", lambda: None)
    # Policy-routing tests use a recording backend; do not probe the host OS.
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: None)
    if os.name == "nt":
        from openprogram import _compat
        monkeypatch.setattr(_compat, "windows_path_to_wsl", lambda path: "/mapped/workdir")
    try:
        yield root, child
    finally:
        reset_worktree(token)


def invoke(**args):
    module = importlib.import_module("openprogram.programs.tools.files.bash.bash")
    return asyncio.run(module.bash.execute("workdir-test", args, None, None))


def text(result):
    return "\n".join(block.text for block in result.content if hasattr(block, "text"))


@pytest.fixture
def recorder(monkeypatch):
    module = importlib.import_module("openprogram.programs.tools.files.bash.bash")

    class RecordingBackend:
        backend_id = "local"

        def __init__(self):
            self.calls = []
            self.result = RunResult(0, "ok", "")

        def run(self, command, timeout, cwd=None):
            self.calls.append((command, cwd))
            return self.result

    backend = RecordingBackend()
    monkeypatch.setattr(module, "get_active_backend", lambda: backend)
    return backend


def test_workdir_is_optional_and_model_description_matches():
    module = importlib.import_module("openprogram.programs.tools.files.bash.bash")
    schema = module.bash.parameters
    assert "workdir" in schema["properties"]
    assert "workdir" not in schema.get("required", [])
    assert "do not persist between calls" in module.bash.description
    assert "does not change file-tool paths" in module.bash.description
    assert "120000" in schema["properties"]["timeout"]["description"]


def test_override_does_not_leak_to_next_call_or_file_tools(scope, recorder):
    from openprogram.worktree.path_resolve import resolve_path

    root, child = scope
    before = Path.cwd()
    first = invoke(command="echo one", workdir=child.name)
    second = invoke(command="echo two")
    assert not first.is_error and not second.is_error
    assert recorder.calls == [("echo one", str(child.resolve())), ("echo two", str(root))]
    assert 'cwd=' + json.dumps(str(child.resolve())) in text(first)
    assert current_worktree_path() == str(root)
    assert resolve_path("marker.txt")[0] == str(root.resolve() / "marker.txt")
    assert Path.cwd() == before


def test_independent_overrides_are_not_relative_to_previous_call(scope, recorder):
    root, child = scope
    sibling = root / "sibling"
    sibling.mkdir()
    for workdir in (child.name, sibling.name, str(child.resolve())):
        assert not invoke(command="echo ok", workdir=workdir).is_error
    assert [cwd for _, cwd in recorder.calls] == [str(child.resolve()), str(sibling.resolve()), str(child.resolve())]


def test_unbound_local_uses_host_default_without_mutating_it(scope, recorder, monkeypatch):
    root, child = scope
    monkeypatch.chdir(root)
    token = set_worktree(None)
    try:
        assert not invoke(command="one", workdir=child.name).is_error
        assert not invoke(command="two").is_error
        assert recorder.calls == [("one", str(child.resolve())), ("two", None)]
        assert Path.cwd() == root.resolve()
    finally:
        reset_worktree(token)


@pytest.mark.parametrize("value", ["", "   ", "bad\x00path", 1, False])
def test_invalid_workdir_never_executes(scope, recorder, value):
    result = invoke(command="must not run", workdir=value)
    assert result.is_error
    assert not recorder.calls


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_missing_or_non_directory_never_executes(scope, recorder, kind):
    root, _ = scope
    target = root / kind
    if kind == "file":
        target.write_text("not a directory")
    result = invoke(command="must not run", workdir=kind)
    assert result.is_error
    assert not recorder.calls
    assert not (root / "missing").exists()


def test_literal_directory_is_not_shell_expanded(scope, recorder):
    root, _ = scope
    literal = root / "$HOME;echo not-run"
    literal.mkdir()
    assert not invoke(command="echo ok", workdir=literal.name).is_error
    assert recorder.calls == [("echo ok", str(literal.resolve()))]


def test_outside_workdir_does_not_authorize_itself(scope, recorder, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(sandbox, "resolve_policy", lambda: sandbox.SandboxPolicy())
    result = invoke(command="must not run", workdir=str(outside))
    assert result.is_error
    assert "outside writable roots" in text(result)
    assert not recorder.calls


def test_already_authorized_extra_root_is_allowed(scope, recorder, tmp_path, monkeypatch):
    outside = tmp_path / "allowed"
    outside.mkdir()
    policy = sandbox.SandboxPolicy(writable_roots=(str(outside),))
    monkeypatch.setattr(sandbox, "resolve_policy", lambda: policy)
    assert not invoke(command="echo ok", workdir=str(outside)).is_error
    assert recorder.calls[0][1] == str(scope[0].resolve())
    assert recorder.calls[0][0].startswith("cd ")
    assert recorder.calls[0][0].endswith(" || exit\necho ok")


@pytest.mark.parametrize("denied", ["deny_read", "deny_write"])
def test_denied_workdir_fails_before_execution(scope, recorder, monkeypatch, denied):
    _, child = scope
    policy = sandbox.SandboxPolicy(**{denied: (str(child) + "/**",)})
    monkeypatch.setattr(sandbox, "resolve_policy", lambda: policy)
    assert invoke(command="must not run", workdir=str(child)).is_error
    assert not recorder.calls


def test_symlink_escape_is_checked_against_original_root(scope, recorder, tmp_path, monkeypatch):
    root, _ = scope
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(sandbox, "resolve_policy", lambda: sandbox.SandboxPolicy())
    assert invoke(command="must not run", workdir="link").is_error
    assert not recorder.calls


def test_launch_race_is_a_typed_error_not_a_silent_fallback(scope, recorder, monkeypatch):
    _, child = scope

    def gone(*args, **kwargs):
        raise FileNotFoundError("directory removed before spawn")

    monkeypatch.setattr(recorder, "run", gone)
    result = invoke(command="echo ok", workdir=str(child))
    assert result.is_error
    assert result.details["error"] == "FileNotFoundError"
    assert result.details["cwd"] == str(child.resolve())


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_failed_commands_report_the_selected_directory(scope, recorder, failure):
    _, child = scope
    recorder.result = RunResult(2, "partial", "failed", timed_out=failure == "timeout")
    result = invoke(command="bad", workdir=child.name)
    assert result.is_error
    assert result.details["cwd"] == str(child.resolve())
    assert "cwd=" in text(result)
    assert "partial" in text(result)


@pytest.mark.parametrize("backend_id", ["ssh", "docker"])
def test_remote_paths_are_not_resolved_on_the_host(scope, monkeypatch, backend_id):
    command, cwd, display = prepare_command(
        "first; second", "link/../child", backend_id=backend_id, worktree="/remote/root",
    )
    assert cwd is None  # Do not ask Docker -w to create a missing directory.
    assert display == "/remote/root/link/../child"
    assert command == "cd /remote/root/link/../child || exit\nfirst; second"


@pytest.mark.parametrize("backend_id", ["ssh", "docker"])
def test_remote_override_with_host_sandbox_fails_closed(scope, monkeypatch, backend_id):
    monkeypatch.setattr(sandbox, "resolve_policy", lambda: sandbox.SandboxPolicy())
    with pytest.raises(PermissionError, match="backend-native"):
        prepare_command("echo ok", "/remote", backend_id=backend_id, worktree=None)


@pytest.mark.parametrize("workdir,root", [("child", None), ("child", "C:\\repo"), ("C:\\repo", None)])
def test_ambiguous_remote_location_is_rejected(scope, workdir, root):
    with pytest.raises(ValueError):
        prepare_command("echo ok", workdir, backend_id="ssh", worktree=root)


def test_unknown_backend_does_not_assume_host_path_semantics(scope):
    with pytest.raises(ValueError, match="does not support"):
        prepare_command("echo ok", "/foo", backend_id="custom", worktree=None)
    assert prepare_command("echo ok", None, backend_id="custom", worktree=None) == ("echo ok", None, None)


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell regression")
def test_remote_guard_quotes_paths_and_stops_all_commands_on_cd_failure(scope, tmp_path):
    weird = tmp_path / "quote' and $var; chars"
    weird.mkdir()
    script, cwd, _ = prepare_command("pwd", str(weird), backend_id="docker", worktree=None)
    result = subprocess.run(script, shell=True, cwd=cwd, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert result.stdout.strip() == str(weird.resolve())
    script, cwd, _ = prepare_command("printf first; printf second", str(tmp_path / "missing"), backend_id="docker", worktree=None)
    result = subprocess.run(script, shell=True, cwd=cwd, capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert result.stdout == ""
    assert not (tmp_path / "missing").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX host-shell syntax")
def test_real_local_shell_cd_and_export_remain_one_shot(scope, monkeypatch):
    from openprogram.backend.local import LocalBackend

    root, child = scope
    (root / "marker.txt").write_text("root", encoding="utf-8")
    (child / "marker.txt").write_text("child", encoding="utf-8")
    module = importlib.import_module("openprogram.programs.tools.files.bash.bash")
    monkeypatch.setattr(module, "get_active_backend", lambda: LocalBackend())
    monkeypatch.setenv("OPENPROGRAM_WORKDIR_TEST", "original")
    first = invoke(command="cat marker.txt; export OPENPROGRAM_WORKDIR_TEST=changed; cd ..", workdir=child.name)
    assert not first.is_error, text(first)
    assert text(first).endswith("child")
    second = invoke(command='cat marker.txt; printf ":%s" "$OPENPROGRAM_WORKDIR_TEST"')
    assert not second.is_error, text(second)
    assert text(second).endswith("root:original")
    assert current_worktree_path() == str(root)


def test_sandboxed_override_keeps_original_policy_root(scope, recorder, monkeypatch):
    root, child = scope
    monkeypatch.setattr(sandbox, "resolve_policy", lambda: sandbox.SandboxPolicy())
    result = invoke(command="echo one; echo two", workdir=child.name)
    assert not result.is_error, text(result)
    assert recorder.calls[0][1] == str(root.resolve())
    assert recorder.calls[0][0].endswith(" || exit\necho one; echo two")
    assert json.dumps(str(child.resolve())) in text(result)


def test_explicit_workdir_never_falls_back_from_an_unavailable_sandbox(scope, recorder, monkeypatch):
    _, child = scope
    monkeypatch.setattr(sandbox, "resolve_policy", lambda: sandbox.SandboxPolicy())
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: "test sandbox unavailable")
    result = invoke(command="must not run", workdir=child.name)
    assert result.is_error
    assert not recorder.calls
