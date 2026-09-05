from __future__ import annotations

import errno
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import zipfile
from pathlib import Path

import pytest

from openprogram.agent.run_control import (
    begin_turn,
    end_turn,
    reset_current_session_id,
    set_current_session_id,
)


@pytest.fixture
def agent_run(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr("openprogram.paths._migration_checked", True)
    monkeypatch.setattr("openprogram.setup._read_config", lambda: {"sandbox": {"mode": "danger-full-access"}})
    token = begin_turn("session/one", "turn:two")
    sid_token = set_current_session_id("session/one")
    try:
        yield tmp_path
    finally:
        reset_current_session_id(sid_token)
        end_turn("session/one", token)


def _manifest(trash_root: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (trash_root / "manifest.jsonl").read_text(
            encoding="utf-8",
        ).splitlines()
    ]


def _symlink_to_or_skip(
    link: Path,
    target: Path,
    *,
    target_is_directory: bool = False,
) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account cannot create symbolic links")
        raise


def test_move_records_original_path_and_restore_refuses_to_overwrite(tmp_path, monkeypatch):
    from openprogram.sandbox.recoverable_delete import move_to_trash, restore_deleted

    trash = tmp_path / "trash"
    monkeypatch.setenv("OPENPROGRAM_RECOVERABLE_TRASH", str(trash))
    target = tmp_path / "work" / "same.txt"
    target.parent.mkdir()
    target.write_text("first")

    entry = move_to_trash(target)

    assert not target.exists()
    assert Path(entry["trash_path"]).read_text() == "first"
    assert _manifest(trash)[0]["original_path"] == str(target)

    target.write_text("replacement")
    with pytest.raises(FileExistsError):
        restore_deleted(entry["id"], trash_root=trash)
    assert target.read_text() == "replacement"

    target.unlink()
    assert restore_deleted(entry["id"], trash_root=trash) == target
    assert target.read_text() == "first"


def test_move_preserves_symlink_and_uses_copy_then_delete_on_exdev(tmp_path, monkeypatch):
    import openprogram.sandbox.recoverable_delete as rd

    trash = tmp_path / "trash"
    monkeypatch.setenv("OPENPROGRAM_RECOVERABLE_TRASH", str(trash))
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    link = tmp_path / "link"
    _symlink_to_or_skip(link, outside)

    link_entry = rd.move_to_trash(link)

    moved_link = Path(link_entry["trash_path"])
    assert moved_link.is_symlink()
    assert moved_link.readlink() == outside
    assert outside.read_text() == "outside"

    directory = tmp_path / "tree"
    directory.mkdir()
    (directory / "child.txt").write_text("child")
    real_rename = rd._rename
    calls = 0

    def exdev_once(source, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(errno.EXDEV, "cross-device link")
        return real_rename(source, destination)

    monkeypatch.setattr(rd, "_rename", exdev_once)
    entry = rd.move_to_trash(directory)

    assert not directory.exists()
    assert (Path(entry["trash_path"]) / "child.txt").read_text() == "child"


def test_directory_cleanup_does_not_follow_a_swapped_symlink(tmp_path, monkeypatch):
    import openprogram.sandbox.recoverable_delete as rd

    if not rd._safe_rmtree_supported():
        pytest.skip("platform has no fd-relative symlink-safe directory removal")

    source = tmp_path / "source"
    child = source / "child"
    child.mkdir(parents=True)
    (child / "original.txt").write_text("original")
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("keep")
    parked = tmp_path / "parked"
    real_scandir = rd.os.scandir
    swapped = False

    class SwappingEntry:
        def __init__(self, entry):
            self._entry = entry
            self.path = entry.path
            self.name = entry.name

        def is_dir(self, *, follow_symlinks=True):
            nonlocal swapped
            result = self._entry.is_dir(follow_symlinks=follow_symlinks)
            if not swapped and self.path == str(child):
                swapped = True
                child.rename(parked)
                _symlink_to_or_skip(
                    child,
                    outside,
                    target_is_directory=True,
                )
            return result

    def scandir(path):
        entries = real_scandir(path)
        if path == str(source):
            with entries:
                return iter([SwappingEntry(entry) for entry in entries])
        return entries

    monkeypatch.setattr(rd.os, "scandir", scandir)

    rd._physical_rmtree(str(source))

    assert victim.read_text() == "keep"
    assert not source.exists()


def test_exdev_cleanup_failure_keeps_a_manifest_for_the_complete_copy(tmp_path, monkeypatch):
    import openprogram.sandbox.recoverable_delete as rd

    trash = tmp_path / "trash"
    source = tmp_path / "source"
    source.mkdir()
    (source / "child.txt").write_text("complete copy")
    monkeypatch.setenv("OPENPROGRAM_RECOVERABLE_TRASH", str(trash))
    real_rename = rd._rename
    calls = 0

    def exdev_once(source_path, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(errno.EXDEV, "cross-device link")
        return real_rename(source_path, destination)

    monkeypatch.setattr(rd, "_rename", exdev_once)
    monkeypatch.setattr(rd, "_physical_rmtree", lambda _path: (_ for _ in ()).throw(PermissionError("cleanup refused")))

    with pytest.raises(PermissionError, match="cleanup refused"):
        rd.move_to_trash(source)

    records = _manifest(trash)
    assert len(records) == 1
    assert records[-1]["source_cleanup_error"] == "PermissionError: cleanup refused"
    assert records[-1]["source_cleanup_status"] == "error"
    assert (Path(records[-1]["trash_path"]) / "child.txt").read_text() == "complete copy"
    # The sidecar is superseded by the error record, not left behind.
    assert not list(trash.glob("pending/*.json"))


def test_exdev_deletion_records_one_manifest_line_and_drops_its_sidecar(
    tmp_path, monkeypatch,
):
    """Every --bind under bubblewrap is its own mount, so the workspace ->
    trash rename always raises EXDEV there. The record format must not
    depend on which path ran."""
    import openprogram.sandbox.recoverable_delete as rd

    trash = tmp_path / "trash"
    target = tmp_path / "work" / "gone.txt"
    target.parent.mkdir()
    target.write_text("payload")
    monkeypatch.setenv("OPENPROGRAM_RECOVERABLE_TRASH", str(trash))
    real_rename = rd._rename
    calls = 0

    def exdev_once(source_path, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(errno.EXDEV, "cross-device link")
        return real_rename(source_path, destination)

    monkeypatch.setattr(rd, "_rename", exdev_once)
    entry = rd.move_to_trash(target)

    assert not target.exists()
    assert len(_manifest(trash)) == 1
    assert not list(trash.glob("pending/*.json"))
    assert rd.restore_deleted(entry["id"], trash_root=trash) == target
    assert target.read_text() == "payload"


def test_a_deletion_interrupted_after_the_copy_is_still_listed(tmp_path, monkeypatch):
    """The sidecar is the crash-window record the manifest line replaced."""
    import openprogram.sandbox.recoverable_delete as rd

    base = tmp_path / "trash"
    trash = base / "session" / "run"
    target = tmp_path / "work" / "interrupted.txt"
    target.parent.mkdir()
    target.write_text("payload")
    monkeypatch.setenv("OPENPROGRAM_RECOVERABLE_TRASH", str(trash))
    real_rename = rd._rename
    calls = 0

    def exdev_once(source_path, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(errno.EXDEV, "cross-device link")
        return real_rename(source_path, destination)

    monkeypatch.setattr(rd, "_rename", exdev_once)
    monkeypatch.setattr(
        rd, "_append_manifest",
        lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        rd.move_to_trash(target)

    assert not (trash / "manifest.jsonl").exists()
    listed = rd.list_deleted(trash_base=base)
    assert [record["original_path"] for record in listed] == [str(target)]
    assert listed[0]["source_cleanup_status"] == "pending"


def test_manifest_short_writes_are_serialized_within_the_process(tmp_path, monkeypatch):
    import openprogram.sandbox.recoverable_delete as rd

    root = tmp_path / "trash"
    root.mkdir()
    real_write = os.write

    def short_write(fd, data):
        time.sleep(0.0005)
        return real_write(fd, data[:3])

    monkeypatch.setattr(rd.os, "write", short_write)
    errors = []

    def append(index):
        try:
            rd._append_manifest(root, {"id": str(index), "value": "x" * 20})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=append, args=(i,)) for i in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    records = _manifest(root)
    assert errors == []
    assert {record["id"] for record in records} == {str(i) for i in range(12)}


def test_failed_manifest_append_truncates_its_partial_record(tmp_path, monkeypatch):
    import openprogram.sandbox.recoverable_delete as rd

    root = tmp_path / "trash"
    root.mkdir()
    rd._append_manifest(root, {"id": "before"})
    real_write = os.write
    calls = 0

    def partial_then_fail(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(fd, data[:3])
        raise OSError("disk write failed")

    monkeypatch.setattr(rd.os, "write", partial_then_fail)
    with pytest.raises(OSError, match="disk write failed"):
        rd._append_manifest(root, {"id": "broken", "value": "long"})
    monkeypatch.setattr(rd.os, "write", real_write)
    rd._append_manifest(root, {"id": "after"})

    assert [record["id"] for record in _manifest(root)] == ["before", "after"]


def test_manifest_uses_a_cross_process_lock_on_windows(tmp_path, monkeypatch):
    import openprogram.sandbox.recoverable_delete as rd

    class FakeMsvcrt:
        LK_LOCK = 1
        LK_UNLCK = 2

        def __init__(self):
            self.calls = []

        def locking(self, fd, mode, size):
            self.calls.append((mode, size))

    fake_msvcrt = FakeMsvcrt()
    root = tmp_path / "trash"
    root.mkdir()
    monkeypatch.setattr(rd.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)

    rd._append_manifest(root, {"id": "windows-lock"})

    assert fake_msvcrt.calls == [(fake_msvcrt.LK_LOCK, 1), (fake_msvcrt.LK_UNLCK, 1)]


def test_restore_skips_a_damaged_manifest_line(tmp_path, monkeypatch):
    from openprogram.sandbox.recoverable_delete import move_to_trash, restore_deleted

    trash = tmp_path / "trash"
    source = tmp_path / "source.txt"
    source.write_text("recover")
    monkeypatch.setenv("OPENPROGRAM_RECOVERABLE_TRASH", str(trash))
    entry = move_to_trash(source)
    manifest = trash / "manifest.jsonl"
    manifest.write_text("{damaged\n[]\n" + manifest.read_text())

    restore_deleted(entry["id"], trash_root=trash)

    assert source.read_text() == "recover"


def test_list_deleted_reads_run_manifests_and_reports_availability(tmp_path):
    from openprogram.sandbox.recoverable_delete import list_deleted, move_to_trash

    trash_base = tmp_path / "state" / "trash"
    trash_root = trash_base / "session-one" / "turn-two"
    source = tmp_path / "source.txt"
    source.write_text("recover")

    entry = move_to_trash(source, trash_root=trash_root)

    records = list_deleted(trash_base=trash_base)
    assert len(records) == 1
    assert records[0]["id"] == entry["id"]
    assert records[0]["status"] == "available"
    assert records[0]["session"] == "session-one"
    assert records[0]["turn"] == "turn-two"


def test_restore_deleted_anywhere_records_completion(tmp_path):
    from openprogram.sandbox.recoverable_delete import (
        list_deleted,
        move_to_trash,
        restore_deleted_anywhere,
    )

    trash_base = tmp_path / "state" / "trash"
    trash_root = trash_base / "session-one" / "turn-two"
    source = tmp_path / "source.txt"
    source.write_text("recover")
    entry = move_to_trash(source, trash_root=trash_root)

    restored = restore_deleted_anywhere(entry["id"], trash_base=trash_base)

    assert restored == source
    assert source.read_text() == "recover"
    assert _manifest(trash_root)[-1]["restore_status"] == "complete"
    assert list_deleted(trash_base=trash_base)[0]["status"] == "restored"


def test_missing_targets_are_only_ignored_when_requested(tmp_path, monkeypatch):
    from openprogram.sandbox.recoverable_delete import move_to_trash

    monkeypatch.setenv("OPENPROGRAM_RECOVERABLE_TRASH", str(tmp_path / "trash"))
    missing = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        move_to_trash(missing)
    assert move_to_trash(missing, missing_ok=True) is None


def test_python_bytes_paths_are_recorded_as_text(tmp_path, monkeypatch):
    from openprogram.sandbox.recoverable_delete import move_to_trash

    trash = tmp_path / "trash"
    monkeypatch.setenv("OPENPROGRAM_RECOVERABLE_TRASH", str(trash))
    target = tmp_path / "bytes.txt"
    target.write_text("bytes")

    entry = move_to_trash(os.fsencode(target))

    assert entry["original_path"] == str(target)
    assert not target.exists()


def test_trash_contents_cannot_be_deleted_into_the_same_trash(tmp_path, monkeypatch):
    from openprogram.sandbox.recoverable_delete import move_to_trash

    trash = tmp_path / "trash"
    item = trash / "existing"
    item.parent.mkdir()
    item.write_text("keep")
    monkeypatch.setenv("OPENPROGRAM_RECOVERABLE_TRASH", str(trash))

    with pytest.raises(OSError):
        move_to_trash(item)
    assert item.read_text() == "keep"

    alias = tmp_path / "trash-alias"
    _symlink_to_or_skip(alias, trash, target_is_directory=True)
    with pytest.raises(OSError):
        move_to_trash(alias / "existing")
    assert item.read_text() == "keep"


def test_sandbox_trash_exists_before_the_command_is_wrapped(agent_run, monkeypatch):
    from openprogram import sandbox
    from openprogram.backend.local import _invocation

    policy = sandbox.SandboxPolicy()
    seen = []

    def wrap(command, cwd, actual_policy):
        root = Path(actual_policy.writable_roots[-1])
        seen.append(root.is_dir())
        return (["/bin/sh", "-c", command], False)

    monkeypatch.setattr(sandbox, "resolve_policy", lambda: policy)
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: None)
    monkeypatch.setattr(sandbox, "wrap_command", wrap)

    _invocation("true", str(agent_run))

    assert seen == [True]


def test_agent_child_launch_fails_if_trash_cannot_be_resolved(agent_run, monkeypatch):
    from openprogram.sandbox.recoverable_delete import prepare_child_env

    def fail():
        raise OSError("state unavailable")

    monkeypatch.setattr("openprogram.paths.get_state_dir", fail)

    with pytest.raises(OSError, match="state unavailable"):
        prepare_child_env()


def test_session_context_without_active_turn_does_not_inject():
    from openprogram.sandbox.recoverable_delete import (
        TRASH_ENV,
        current_trash_root,
        prepare_child_env,
    )

    sid_token = set_current_session_id("session-without-turn")
    try:
        base = {"PATH": "/usr/bin"}
        assert current_trash_root() is None
        assert prepare_child_env(base) is base
        assert TRASH_ENV not in base
    finally:
        reset_current_session_id(sid_token)


def test_execute_code_honors_sandbox_unavailable_refuse(agent_run, monkeypatch):
    from openprogram import sandbox
    from openprogram.programs.tools.code.execute_code import execute

    marker = agent_run / "must-not-exist"
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {"sandbox": {"mode": sandbox.MODE_WORKSPACE_WRITE, "unavailable_policy": "refuse"}},
    )
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: "sandbox binary missing")

    result = execute(code=f"from pathlib import Path; Path({str(marker)!r}).write_text('unsafe')")

    assert not marker.exists()
    assert "sandbox binary missing" in result


def test_execute_code_routes_interpreter_script_and_cwd_through_local_backend(
    tmp_path, monkeypatch,
):
    from openprogram.backend.local import LocalBackend
    from openprogram.backend.base import RunResult
    from openprogram.programs.tools.code.execute_code import execute

    seen = {}

    def fake_run(self, command, timeout, cwd=None):
        seen.update(command=command, timeout=timeout, cwd=cwd)
        return RunResult(0, "ok\n", "")

    monkeypatch.setattr(LocalBackend, "run", fake_run)
    result = execute(
        code="print('ok')", cwd=str(tmp_path), python="/opt/custom python",
        timeout=7,
    )
    assert seen["cwd"] == str(tmp_path)
    assert seen["timeout"] == 7
    assert seen["command"].startswith("'/opt/custom python' ")
    assert seen["command"].rstrip("'").endswith(".py")
    assert "exit=0" in result


def test_shell_shim_update_does_not_replace_the_final_file_on_failure(tmp_path, monkeypatch):
    import openprogram.sandbox.recoverable_delete as rd

    root = tmp_path / "trash"
    rm = root / "shims" / "bin" / "rm"
    rm.parent.mkdir(parents=True)
    rm.write_text("old complete shim")

    def fail_replace(_source, _destination):
        raise OSError("replace refused")

    monkeypatch.setattr(rd.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace refused"):
        rd._write_shell_shims(root)

    assert rm.read_text() == "old complete shim"


def test_shell_shim_write_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    import openprogram.sandbox.recoverable_delete as rd

    root = tmp_path / "trash"
    rm = root / "shims" / "bin" / "rm"
    rm.parent.mkdir(parents=True)
    rm.write_text("old complete shim")

    def fail_write(_fd, _data):
        raise OSError("write refused")

    monkeypatch.setattr(rd.os, "write", fail_write)

    with pytest.raises(OSError, match="write refused"):
        rd._write_shell_shims(root)

    assert rm.read_text() == "old complete shim"
    assert list(rm.parent.glob(".*.tmp-*")) == []


def test_dir_fd_deletion_fails_safe_instead_of_re_resolving_the_path(tmp_path):
    from openprogram.sandbox.recoverable_delete import _dir_fd_path

    with pytest.raises(NotImplementedError, match="dir_fd"):
        _dir_fd_path("target", 123)


def test_agent_shell_rm_rmdir_and_unlink_are_recoverable(agent_run):
    from openprogram.backend.local import LocalBackend

    work = agent_run / "work"
    work.mkdir()
    (work / "a.txt").write_text("a")
    (work / "b.txt").write_text("b")
    (work / "empty").mkdir()

    result = LocalBackend().run("rm a.txt && unlink b.txt && rmdir empty", 10, str(work))

    assert result.exit_code == 0, result.stderr
    assert not any((work / name).exists() for name in ("a.txt", "b.txt", "empty"))
    trash = agent_run / "home" / ".openprogram" / "trash" / "session_one" / "turn_two"
    assert {Path(item["original_path"]).name for item in _manifest(trash)} == {
        "a.txt", "b.txt", "empty",
    }


def test_agent_python_and_execute_code_deletions_are_recoverable(agent_run):
    from openprogram.backend.local import LocalBackend
    from openprogram.programs.tools.code.execute_code import execute

    work = agent_run / "python-work"
    work.mkdir()
    for name in ("os.txt", "pathlib.txt", "execute.txt"):
        (work / name).write_text(name)
    (work / "tree").mkdir()
    (work / "tree" / "child").write_text("child")
    code = (
        "import os, pathlib, shutil; "
        "os.remove('os.txt'); pathlib.Path('pathlib.txt').unlink(); shutil.rmtree('tree')"
    )

    result = LocalBackend().run(
        f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}", 10, str(work),
    )
    execute_result = execute(
        code="import os; os.unlink('execute.txt')", cwd=str(work), timeout=10,
    )

    assert result.exit_code == 0, result.stderr
    assert "exit=0" in execute_result
    assert not any((work / name).exists() for name in ("os.txt", "pathlib.txt", "tree", "execute.txt"))
    trash = agent_run / "home" / ".openprogram" / "trash" / "session_one" / "turn_two"
    assert len(_manifest(trash)) == 4


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_agent_node_sync_callback_and_promises_deletions_are_recoverable(agent_run):
    from openprogram.backend.local import LocalBackend

    work = agent_run / "node-work"
    work.mkdir()
    for name in ("sync.txt", "callback.txt", "promise.txt"):
        (work / name).write_text(name)
    script = work / "delete.cjs"
    script.write_text(
        "const fs=require('fs');\n"
        "fs.unlinkSync('sync.txt');\n"
        "fs.unlink('callback.txt', async (error) => {\n"
        "  if (error) throw error;\n"
        "  await fs.promises.rm('promise.txt');\n"
        "});\n"
    )

    result = LocalBackend().run(f"node {shlex.quote(str(script))}", 10, str(work))

    assert result.exit_code == 0, result.stderr
    assert not any((work / name).exists() for name in ("sync.txt", "callback.txt", "promise.txt"))
    trash = agent_run / "home" / ".openprogram" / "trash" / "session_one" / "turn_two"
    assert len(_manifest(trash)) == 3


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_agent_node_rejects_buffer_and_file_url_paths_without_deleting(agent_run):
    from openprogram.backend.local import LocalBackend

    work = agent_run / "node-unsupported"
    work.mkdir()
    buffer_target = work / "buffer.txt"
    url_target = work / "url.txt"
    buffer_target.write_text("buffer")
    url_target.write_text("url")
    script = work / "unsupported.cjs"
    script.write_text(
        "const fs=require('node:fs');\n"
        "const {pathToFileURL}=require('node:url');\n"
        "let rejected=0;\n"
        "try { fs.unlinkSync(Buffer.from('buffer.txt')); } catch { rejected++; }\n"
        "try { fs.unlinkSync(pathToFileURL('url.txt')); } catch { rejected++; }\n"
        "if (rejected !== 2) process.exit(3);\n"
        "if (!fs.existsSync('buffer.txt') || !fs.existsSync('url.txt')) process.exit(4);\n"
    )

    result = LocalBackend().run(f"node {shlex.quote(str(script))}", 10, str(work))

    assert result.exit_code == 0, result.stderr
    assert buffer_target.read_text() == "buffer"
    assert url_target.read_text() == "url"


def test_wheel_contains_both_runtime_shims(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    config = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = config["tool"]["setuptools"]["package-data"]["openprogram"]
    assert "sandbox/shims/*.py" in package_data
    assert "programs/workflow/browser/*.cjs" in package_data

    project = tmp_path / "project"
    (project / "openprogram" / "sandbox" / "shims").mkdir(parents=True)
    for relative in (
        "pyproject.toml", "README.md", "LICENSE", "openprogram/__init__.py",
        "openprogram/sandbox/__init__.py", "openprogram/sandbox/shims/sitecustomize.py",
        "openprogram/sandbox/shims/node_preload.cjs",
        "openprogram/programs/__init__.py",
        "openprogram/programs/workflow/__init__.py",
        "openprogram/programs/workflow/browser/__init__.py",
        "openprogram/programs/workflow/browser/playwright_exact_page_mcp.cjs",
        "openprogram/sandbox/recoverable_delete.py",
        "openprogram/webui/__init__.py",
    ):
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / relative, destination)
    (project / "openprogram/webui/functions_meta.json").write_text(
        '{"profiles": {"must-not-ship": []}}\n', encoding="utf-8"
    )
    (project / "openprogram/webui/programs_meta.json").write_text(
        '{"favorites": ["must-not-ship"]}\n', encoding="utf-8",
    )
    wheel_dir = tmp_path / "wheel"
    uv_name = "uv.exe" if sys.platform == "win32" else "uv"
    uv = Path(sys.executable).with_name(uv_name)
    if not uv.is_file():
        resolved_uv = shutil.which("uv")
        assert resolved_uv is not None, "uv is required for packaging tests"
        uv = Path(resolved_uv)
    built = subprocess.run(
        [
            str(uv), "build", "--quiet", "--wheel",
            "--python", sys.executable, "--no-managed-python", "--out-dir",
            str(wheel_dir), ".",
        ],
        cwd=project,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=60,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    wheel = next(wheel_dir.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert "openprogram/sandbox/shims/sitecustomize.py" in names
    assert "openprogram/sandbox/shims/node_preload.cjs" in names
    assert (
        "openprogram/programs/workflow/browser/"
        "playwright_exact_page_mcp.cjs"
    ) in names
    assert "openprogram/webui/functions_meta.json" not in names
    assert "openprogram/webui/programs_meta.json" not in names
    installed = tmp_path / "installed"
    install = subprocess.run(
        [
            sys.executable, "-m", "pip", "install", "--no-deps", "--target",
            str(installed), str(wheel),
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
        timeout=60,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    installed_shims = installed / "openprogram/sandbox/shims"
    assert (installed_shims / "sitecustomize.py").is_file()
    assert (installed / "openprogram/sandbox/shims/node_preload.cjs").is_file()
    assert (
        installed / "openprogram/programs/workflow/browser/"
        "playwright_exact_page_mcp.cjs"
    ).is_file()

    runtime_work = tmp_path / "runtime-work"
    runtime_work.mkdir()
    target = runtime_work / "delete-me.txt"
    target.write_text("recoverable", encoding="utf-8")
    runtime_trash = tmp_path / "runtime-trash"
    probe = subprocess.run(
        [sys.executable, "-c", "import os; os.unlink('delete-me.txt')"],
        cwd=runtime_work,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join((str(installed_shims), str(installed))),
            "OPENPROGRAM_RECOVERABLE_TRASH": str(runtime_trash),
        },
        timeout=30,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert not target.exists()
    assert any((runtime_trash / "items").iterdir())
