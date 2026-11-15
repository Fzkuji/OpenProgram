"""The launcher on Windows: no console of its own for a detached worker, and
arguments read as Unicode.

The behaviour tests need Windows and its C compiler. Elsewhere the Windows
source is cross-compiled when zig is installed, and the two rules are checked
in the source, because nothing here can start a Windows process.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

REPOSITORY = Path(__file__).resolve().parents[4]
SOURCE = REPOSITORY / "scripts/release/runtime-launcher.c"
WINDOWS = os.name == "nt"


def _windows_function(name: str) -> str:
    """The body of ``name`` from the Windows half of the platform layer."""
    text = SOURCE.read_text(encoding="utf-8")
    windows_half = text.split("#else /* POSIX */", 1)[0]
    found = re.search(rf"^static [^\n]*\b{name}\([^\n]*\{{\n(.*?)^\}}\n", windows_half, re.S | re.M)
    assert found, f"{name} is not in the Windows platform layer"
    return found.group(1)


def test_a_child_of_a_consoleless_launcher_gets_no_console_and_keeps_the_handles():
    body = _windows_function("spawn_wait")
    guarded = re.search(r"if \(GetConsoleCP\(\) == 0\) \{(.*?)\n    \}", body, re.S)
    assert guarded, "the launcher does not ask whether it has a console"
    block = guarded.group(1)
    assert "CREATE_NO_WINDOW" in block
    assert "STARTF_USESTDHANDLES" in block
    for handle in ("STD_INPUT_HANDLE", "STD_OUTPUT_HANDLE", "STD_ERROR_HANDLE"):
        assert f"GetStdHandle({handle})" in block
    # A launcher run from a terminal keeps sharing that terminal.
    assert re.search(r"DWORD flags = 0;", body)
    assert re.search(r"CreateProcessW\(NULL, command, NULL, NULL, TRUE, flags,", body)


def test_main_reads_its_arguments_from_the_wide_command_line():
    text = SOURCE.read_text(encoding="utf-8")
    reader = _windows_function("utf8_arguments")
    assert "CommandLineToArgvW(GetCommandLineW()" in reader
    assert "CP_UTF8" in reader
    main = text[text.index("int main(int argc, char **argv)"):]
    replaced = main.index("argv = wide_arguments;")
    assert replaced < main.index('strcmp(argv[1], "--root")'), "--root is read before the arguments are replaced"


@pytest.mark.skipif(shutil.which("zig") is None, reason="needs zig to cross-compile for Windows")
@pytest.mark.parametrize("target", ["x86_64-windows-gnu", "aarch64-windows-gnu"])
def test_the_windows_source_compiles(tmp_path, target):
    environment = {
        **os.environ,
        "ZIG_GLOBAL_CACHE_DIR": str(tmp_path / "zig-global"),
        "ZIG_LOCAL_CACHE_DIR": str(tmp_path / "zig-local"),
    }
    done = subprocess.run(
        ["zig", "cc", "-target", target, "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
         str(SOURCE), "-o", str(tmp_path / "openprogram-launcher.exe"), "-lshell32"],
        capture_output=True, text=True, env=environment, timeout=600,
    )
    assert done.returncode == 0, done.stderr


# ---- on Windows itself ------------------------------------------------------

@pytest.fixture(scope="session")
def windows_launcher(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("build_launcher", REPOSITORY / "scripts/release/build-launcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_executable(tmp_path_factory.mktemp("launcher"))


def _developer_root(directory: Path) -> Path:
    """A root in developer mode: the launcher starts this Python and nothing else."""
    root = directory / "runtime"
    root.mkdir(parents=True)
    (root / "developer.json").write_text(
        json.dumps({"checkout": str(directory), "python": sys.executable}, ensure_ascii=False), encoding="utf-8",
    )
    return root


@pytest.mark.skipif(not WINDOWS, reason="needs Windows")
def test_a_detached_launcher_gives_its_child_no_console_and_its_own_log(windows_launcher, tmp_path):
    root = _developer_root(tmp_path)
    log = tmp_path / "worker.log"
    report = (
        "import ctypes; window = ctypes.windll.kernel32.GetConsoleWindow(); "
        "print('console', int(bool(window and ctypes.windll.user32.IsWindowVisible(window))))"
    )
    with log.open("wb") as output:
        done = subprocess.run(
            [str(windows_launcher), "--root", str(root), "-c", report],
            stdin=subprocess.DEVNULL, stdout=output, stderr=output, timeout=120,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    assert done.returncode == 0, log.read_text(errors="replace")
    assert log.read_text().split() == ["console", "0"]


@pytest.mark.skipif(not WINDOWS, reason="needs Windows")
def test_a_launcher_in_a_terminal_still_shares_it(windows_launcher, tmp_path):
    root = _developer_root(tmp_path)
    done = subprocess.run(
        [str(windows_launcher), "--root", str(root), "-c", "print('shared')"],
        capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["shared"]


@pytest.mark.skipif(not WINDOWS, reason="needs Windows")
def test_a_root_and_an_argument_outside_the_code_page_arrive_whole(windows_launcher, tmp_path):
    root = _developer_root(tmp_path / "用户 Ωμέγα")
    out = tmp_path / "argument.txt"
    report = "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2], encoding='utf-8')"
    done = subprocess.run(
        [str(windows_launcher), "--root", str(root), "-c", report, str(out), "参数 Ω"],
        capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert out.read_text(encoding="utf-8") == "参数 Ω"


def test_every_python_the_windows_launcher_starts_is_in_utf8_mode():
    """-I drops PYTHONUTF8, so the mode has to travel as an argument."""
    text = SOURCE.read_text(encoding="utf-8")
    assert re.search(r'#if defined\(_WIN32\)\n#define UTF8_MODE "-X", "utf8",\n#define UTF8_MODE_COUNT 2\n#else\n'
                     r"#define UTF8_MODE\n#define UTF8_MODE_COUNT 0\n#endif", text)
    isolated = [line for line in text.splitlines() if '"-I", "-B"' in line]
    assert len(isolated) == 3, "a new place starts Python; give it UTF8_MODE too"
    assert all('"-I", "-B", UTF8_MODE ' in line for line in isolated), isolated
    developer = text[text.index("static int follow_developer("):text.index("/* ---- starting the version")]
    assert "char *options[] = {UTF8_MODE NULL};" in developer


@pytest.mark.skipif(not WINDOWS, reason="needs Windows")
def test_the_launcher_starts_python_in_utf8_mode_whatever_the_environment_says(windows_launcher, tmp_path):
    root = _developer_root(tmp_path)
    done = subprocess.run(
        [str(windows_launcher), "--root", str(root), "-c",
         "import locale, sys; print(sys.flags.utf8_mode, locale.getpreferredencoding(False), sys.argv[1])", "页面"],
        capture_output=True, encoding="utf-8", timeout=120, env={**os.environ, "PYTHONUTF8": "0"},
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["1", "utf-8", "页面"]


def test_the_launcher_closes_handles_it_inherited_before_starting_anything():
    """The App's DevTools socket arrives inherited and must not outlive the App."""
    body = _windows_function("close_inherited_handles")
    assert "OP_PROCESS_HANDLE_INFORMATION" in body and "OP_OBJ_INHERIT" in body
    for handle in ("STD_INPUT_HANDLE", "STD_OUTPUT_HANDLE", "STD_ERROR_HANDLE"):
        assert f"GetStdHandle({handle})" in body, "the standard handles carry the worker log"
    assert "CloseHandle(handle)" in body
    text = SOURCE.read_text(encoding="utf-8")
    main = text[text.index("int main(int argc, char **argv)"):]
    closed = main.index("close_inherited_handles();")
    assert closed < main.index('strcmp(argv[1], "--root")'), "handles are closed before anything starts"


@pytest.mark.skipif(not WINDOWS, reason="needs Windows")
def test_a_listening_socket_the_launcher_inherits_is_not_held_by_it_or_its_python(windows_launcher, tmp_path):
    import socket

    root = _developer_root(tmp_path)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    listener.set_inheritable(True)
    child = subprocess.Popen(
        [str(windows_launcher), "--root", str(root), "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=False,
    )
    try:
        import time

        time.sleep(5)  # the launcher has started its Python by now
        assert child.poll() is None
        listener.close()
        # With the App's socket gone, nothing may still be listening on its port.
        probe = socket.socket()
        probe.settimeout(1)
        try:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
        finally:
            probe.close()
    finally:
        child.kill()
        child.wait(timeout=30)
