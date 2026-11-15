"""The first install from a seed, and the shell reading what Python writes."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

from openprogram.operations.self_update.versions import pointer
from tests.support.sealed_versions import BASE, REPOSITORY, make_layout, make_version, point_at

SHELL = REPOSITORY / "apps/desktop/shell"
RELEASE = REPOSITORY / "scripts/release"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


install_seed = _load(RELEASE / "bootstrap/install_seed.py", "install_seed")
shell_config = _load(RELEASE / "thin-shell-config.py", "thin_shell_config")


def _node_with_zstd():
    node = shutil.which("node")
    if node is None:
        return None
    probe = subprocess.run([node, "-e", "process.exit(require('zlib').createZstdDecompress ? 0 : 1)"], check=False)
    return node if probe.returncode == 0 else None


NODE = _node_with_zstd()


def _node(script, *arguments):
    done = subprocess.run([NODE, "-e", script, "--", *arguments], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.mark.skipif(
    os.name == "nt" or NODE is None or not all(shutil.which(tool) for tool in ("cc", "zstd", "git")),
    reason="needs cc, zstd, git and Node with zstd",
)
def test_the_smoke_install_passes(tmp_path):
    done = subprocess.run(
        ["bash", str(RELEASE / "smoke-thin-shell.sh")],
        capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHON": sys.executable, "TMPDIR": str(tmp_path)},
    )
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.skipif(NODE is None, reason="needs Node")
def test_the_shell_and_python_agree_on_the_pointer(tmp_path):
    layout = make_layout(tmp_path / "runtime")
    make_version(layout, "v1")
    make_version(layout, "v2")
    point_at(layout, "v2", last_good="v1", last_good_base=BASE, probation="u1", probation_deadline=1794134010)
    original = layout.pointer.read_bytes()
    rendered = _node(
        "const p=require(process.argv[1]);process.stdout.write(p.render(p.read(process.argv[2])))",
        str(SHELL / "pointer.js"), str(layout.root),
    )
    assert rendered.encode("ascii") == original

    _node(
        "const p=require(process.argv[1]);p.compareAndSet(process.argv[2],5,'shell',{version:'v1',probation:'',probation_deadline:''})",
        str(SHELL / "pointer.js"), str(layout.root),
    )
    after = pointer.read(layout)
    assert (after.generation, after.version, after.writer, after.probation) == (6, "v1", "shell", "")
    assert pointer.parse(layout.previous_pointer.read_text()).version == "v2"

    moved = pointer.Pointer(**{**after.__dict__, "generation": 9, "version": "v2", "writer": "controller"})
    layout.pointer.unlink()
    pointer.create(layout, moved)
    seen = json.loads(_node(
        "const p=require(process.argv[1]);process.stdout.write(JSON.stringify(p.read(process.argv[2])))",
        str(SHELL / "pointer.js"), str(layout.root),
    ))
    assert (seen["generation"], seen["version"], seen["writer"]) == (9, "v2", "controller")


def _manifest():
    return {"version": "v1", "base": BASE, "commit": "a" * 40, "built_root": "/built"}


def test_a_seed_pointer_starts_on_its_own_version(tmp_path):
    layout = make_layout(tmp_path / "runtime")
    install_seed.write_pointer(layout.root, _manifest(), point_at_seed=False)
    read = pointer.read(layout)
    assert (read.generation, read.version, read.base, read.last_good, read.writer) == (1, "v1", BASE, "v1", "installer")


def test_an_existing_pointer_only_moves_when_asked(tmp_path):
    layout = make_layout(tmp_path / "runtime")
    make_version(layout, "v2")
    point_at(layout, "v2")
    install_seed.write_pointer(layout.root, _manifest(), point_at_seed=False)
    assert pointer.read(layout).version == "v2"
    install_seed.write_pointer(layout.root, _manifest(), point_at_seed=True)
    read = pointer.read(layout)
    assert (read.generation, read.version) == (6, "v1")
    assert pointer.parse(layout.previous_pointer.read_text()).version == "v2"


def test_a_repository_with_code_is_never_replaced(tmp_path):
    root = tmp_path / "runtime"
    (root / "repo").mkdir(parents=True)
    (root / "repo" / "HEAD").write_text("kept\n")
    install_seed.create_repository(tmp_path / "no-seed", root, _manifest())
    assert (root / "repo" / "HEAD").read_text() == "kept\n"


@pytest.mark.parametrize("name", ["../escape", "/absolute", "a/../../b", "a\\b"])
def test_paths_that_leave_the_seed_are_refused(tmp_path, name):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.addfile(tarfile.TarInfo(name))
    with pytest.raises(install_seed.SeedError):
        install_seed._extract_tar(archive, tmp_path / "out", "/built")
    assert not (tmp_path / "escape").exists()


def test_a_link_that_leaves_the_seed_is_refused(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo("a/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        tar.addfile(member)
    with pytest.raises(install_seed.SeedError):
        install_seed._extract_tar(archive, tmp_path / "out", "/built")


@pytest.mark.parametrize(("target", "stays"), [
    ("python.exe", True),
    ("..\\..\\bases\\x", True),
    ("D:/a/seed/store/runtime/bases/x/python.exe", True),  # As Windows' tar writes it.
    ("d:\\A\\seed\\store\\runtime\\bases\\x", True),
    ("D:/a/seed/store/runtime-other/x", False),
    ("D:/a/seed/store/runtime/../x", False),
    ("C:/Windows/System32/cmd.exe", False),
    ("C:\\Windows\\System32\\cmd.exe", False),
    ("\\\\server\\share\\x", False),
    ("/etc/passwd", False),
])
def test_a_windows_link_is_judged_by_where_it_points_not_by_its_slashes(target, stays):
    assert install_seed.link_stays(target, "D:\\a\\seed\\store\\runtime") is stays


def test_a_drive_link_that_leaves_the_seed_is_refused(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo("a/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "C:/Windows/System32/cmd.exe"
        tar.addfile(member)
    with pytest.raises(install_seed.SeedError):
        install_seed._extract_tar(archive, tmp_path / "out", "D:\\a\\seed\\store\\runtime")


def test_the_installed_batch_file_has_the_line_ends_cmd_needs(tmp_path):
    source = RELEASE / "bootstrap/bin-openprogram.cmd"
    target = tmp_path / "openprogram.cmd"
    install_seed.write_batch_file(source, target)
    written = target.read_bytes()
    assert written.count(b"\r\n") == written.count(b"\n") > 20, "a bare line feed can hide a goto label from cmd.exe"
    assert written.replace(b"\r\n", b"\n") == source.read_bytes().replace(b"\r\n", b"\n")
    # install_tools is what writes it on Windows.
    text = (RELEASE / "bootstrap/install_seed.py").read_text(encoding="utf-8")
    assert 'write_batch_file(seed / "bootstrap" / "bin-openprogram.cmd", binary / "openprogram.cmd")' in text


def test_the_installed_command_starts_python_in_utf8_mode_and_gives_the_console_back():
    lines = (RELEASE / "bootstrap/bin-openprogram.cmd").read_text(encoding="ascii").splitlines()
    starts = [line for line in lines if "python.exe\" -I" in line or "%BASE_PYTHON%\" -I" in line]
    assert len(starts) == 2
    assert all(" -I -B -X utf8 " in line for line in starts), starts
    saved, switched, restored = (lines.index(line) for line in (
        """if not defined _OPENPROGRAM_CODEPAGE for /f "tokens=2 delims=:." %%P in ('chcp') do set "_OPENPROGRAM_CODEPAGE=%%P\"""",
        "chcp 65001 >nul",
        "chcp %_OPENPROGRAM_CODEPAGE% >nul",
    ))
    assert saved < switched < min(lines.index(line) for line in starts)
    # Both ways through the file reach the restore with Python's own exit status.
    assert lines[restored - 1] == ":finish" and lines[restored + 1] == "exit /b %_OPENPROGRAM_EXIT%"
    for start in starts:
        assert lines[lines.index(start) + 1] == 'set "_OPENPROGRAM_EXIT=%ERRORLEVEL%"'
    # The same preamble the complete-runtime installer writes.
    release = (RELEASE / "install-release.ps1").read_text(encoding="utf-8").replace("''", "'")
    for line in (lines[saved], lines[switched], lines[restored], "setlocal DisableDelayedExpansion"):
        assert line in release and line in lines


def test_git_messages_are_read_as_utf8_whatever_the_code_page(tmp_path, monkeypatch):
    seen = []

    def run(command, **options):
        seen.append(options)
        return subprocess.CompletedProcess(command, 1, "", "fatal: 用户/目录 is not a bundle")

    monkeypatch.setattr(install_seed.subprocess, "run", run)
    with pytest.raises(install_seed.SeedError, match="用户/目录"):
        install_seed._git(("git", {}), tmp_path, "update-ref", "refs/heads/local", "0" * 40)
    assert seen[0]["encoding"] == "utf-8" and seen[0]["errors"] == "replace" and "text" not in seen[0]


def test_both_bootstrap_tools_look_for_the_bundled_git_in_the_same_place(tmp_path):
    versions = _load(RELEASE / "bootstrap/versions.py", "bootstrap_versions")
    root = tmp_path / "runtime"
    for windows in (True, False):
        git = root / "bases" / ("windows" if windows else "posix") / "git"
        command = install_seed.git_executable(git, windows)
        command.parent.mkdir(parents=True)
        command.write_text("")
    # Git for Windows has no bin\git.exe; the POSIX builds have no cmd.
    assert versions._git(root, windows=True) == str(root / "bases/windows/git/cmd/git.exe")
    assert versions._git(root, windows=False) == str(root / "bases/posix/git/bin/git")
    builder = (RELEASE / "install-release.ps1").read_text(encoding="utf-8")
    assert 'Join-Path $ReleaseDir "git\\cmd"' in builder


def test_a_failed_install_leaves_the_marker_so_it_starts_over(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "seed.json").write_text(json.dumps(_manifest()))
    root = tmp_path / "runtime"
    with pytest.raises(install_seed.SeedError):
        install_seed.install(seed, root, unpack=False, point_at_seed=False)
    assert (root / ".seeding").exists()
    assert not (root / "current").exists()


def test_the_thin_build_ships_the_shell_and_the_seed_only():
    build = shell_config.config("build/seed")
    assert build["files"] == ["shell/**"]
    assert build["extraResources"] == [{"from": "build/seed", "to": "seed", "filter": ["**/*"]}]
    assert "afterPack" not in build
