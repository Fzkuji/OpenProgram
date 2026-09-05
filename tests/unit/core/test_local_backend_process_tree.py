from __future__ import annotations

import shlex
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from openprogram.backend import local
from tests.support.waiting import wait_until


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX process groups")
def test_local_backend_timeout_closes_a_background_shell_listener(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A timed-out shell must not leave its long-running child behind."""

    script = tmp_path / "listener.py"
    port_file = tmp_path / "port.txt"
    script.write_text(
        """
import pathlib
import socket
import sys
import time

listener = socket.socket()
listener.bind(("127.0.0.1", 0))
listener.listen()
pathlib.Path(sys.argv[1]).write_text(
    str(listener.getsockname()[1]), encoding="ascii"
)
time.sleep(30)
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        local,
        "_invocation",
        lambda command, cwd=None: (command, True, None, False),
    )
    command = " ".join(
        shlex.quote(value)
        for value in (sys.executable, str(script), str(port_file))
    )

    # The shell exits immediately, leaving the listener as an orphan that owns
    # both capture pipes.  Looking the tree up through the now-dead shell PID
    # cannot find it; cleanup has to retain the original PGID.
    result = local.LocalBackend().run(command + " &", timeout=2, cwd=str(tmp_path))

    assert result.timed_out is True
    assert result.exit_code == -1
    port = int(port_file.read_text(encoding="ascii"))
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.2)


def test_local_backend_timeout_owns_tree_after_root_exits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A dead root PID must not make a pipe-holding grandchild unreachable.

    This is the portable form of the Windows Job Object regression.  The
    launcher exits immediately after spawning the listener, so by timeout the
    original Popen PID is gone while the grandchild is still alive.
    """

    listener = tmp_path / "listener.py"
    launcher = tmp_path / "launcher.py"
    state = tmp_path / "state.txt"
    listener.write_text(
        """
import os
import pathlib
import socket
import sys
import time

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
sock.listen()
pathlib.Path(sys.argv[1]).write_text(
    f"{os.getpid()} {sock.getsockname()[1]}", encoding="ascii"
)
time.sleep(30)
""".lstrip(),
        encoding="utf-8",
    )
    launcher.write_text(
        """
import subprocess
import sys

subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])
""".lstrip(),
        encoding="utf-8",
    )
    argv = [sys.executable, str(launcher), str(listener), str(state)]
    monkeypatch.setattr(
        local,
        "_invocation",
        lambda command, cwd=None: (argv, False, None, False),
    )

    result = local.LocalBackend().run("ignored", timeout=2, cwd=str(tmp_path))

    assert result.timed_out is True
    _pid, port = map(int, state.read_text(encoding="ascii").split())
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.2)


def test_timeout_drain_never_closes_a_pipe_owned_by_communicate_thread(
    monkeypatch,
) -> None:
    closed: list[str] = []

    class Pipe:
        def close(self) -> None:
            closed.append("closed")
            raise AssertionError("closing a reader-owned pipe can deadlock")

    class Process:
        pid = 42
        returncode = None
        stdout = Pipe()
        stderr = Pipe()
        stdin = None

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(
                "child",
                timeout,
                output=b"partial stdout",
                stderr=b"partial stderr",
            )

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("child", timeout)

        def kill(self) -> None:
            raise AssertionError("the owner reported successful termination")

    process = Process()

    class Owner:
        def popen(self, *args, **kwargs):
            return process

        def terminate(self) -> bool:
            return True

        def release(self) -> None:
            raise AssertionError("a timed-out tree must not be released")

    monkeypatch.setattr(local, "ProcessTreeOwner", Owner)
    monkeypatch.setattr(
        local,
        "_invocation",
        lambda command, cwd=None: (command, True, None, False),
    )

    result = local.LocalBackend().run("ignored", timeout=0.01)

    assert result.timed_out is True
    assert result.stdout == "partial stdout"
    assert result.stderr == "partial stderr"
    assert closed == []


def test_normal_completion_releases_a_deliberately_detached_child(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Tree ownership must not turn an ordinary successful run into cleanup."""

    listener = tmp_path / "detached_listener.py"
    launcher = tmp_path / "detached_launcher.py"
    state = tmp_path / "detached_state.txt"
    listener.write_text(
        """
import os
import pathlib
import socket
import sys
import time

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
sock.listen()
pathlib.Path(sys.argv[1]).write_text(
    f"{os.getpid()} {sock.getsockname()[1]}", encoding="ascii"
)
time.sleep(30)
""".lstrip(),
        encoding="utf-8",
    )
    launcher.write_text(
        """
import subprocess
import sys

subprocess.Popen(
    [sys.executable, sys.argv[1], sys.argv[2]],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
""".lstrip(),
        encoding="utf-8",
    )
    argv = [sys.executable, str(launcher), str(listener), str(state)]
    monkeypatch.setattr(
        local,
        "_invocation",
        lambda command, cwd=None: (argv, False, None, False),
    )

    result = local.LocalBackend().run("ignored", timeout=5, cwd=str(tmp_path))

    assert result.exit_code == 0
    assert wait_until(
        lambda: state.exists()
        and len(state.read_text(encoding="ascii").split()) == 2,
        timeout=3,
    )
    pid, port = map(int, state.read_text(encoding="ascii").split())
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
    finally:
        from openprogram import _compat

        _compat.kill_process_tree(pid)


def test_process_tree_creation_options_are_platform_owned(monkeypatch) -> None:
    from openprogram import _compat

    monkeypatch.setattr(_compat._sys, "platform", "linux")
    assert _compat.process_tree_popen_kwargs() == {"start_new_session": True}

    monkeypatch.setattr(_compat._sys, "platform", "win32")
    monkeypatch.setattr(_compat, "no_window_creation_flags", lambda: 0x08000000)
    monkeypatch.setattr(
        _compat._subprocess,
        "CREATE_NEW_PROCESS_GROUP",
        0x00000200,
        raising=False,
    )
    assert _compat.process_tree_popen_kwargs() == {
        "creationflags": 0x08000200,
    }


def test_posix_owner_keeps_the_original_pgid_after_leader_exit(
    monkeypatch,
) -> None:
    from openprogram import _compat

    seen: dict[str, object] = {}

    class Process:
        pid = 7123

    def popen(*args, **kwargs):
        seen.update(args=args, **kwargs)
        return Process()

    monkeypatch.setattr(_compat._sys, "platform", "linux")
    monkeypatch.setattr(_compat._signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(_compat._subprocess, "Popen", popen)
    monkeypatch.setattr(
        _compat._os,
        "getpgid",
        lambda _pid: (_ for _ in ()).throw(ProcessLookupError()),
        raising=False,
    )
    monkeypatch.setattr(
        _compat._os,
        "killpg",
        lambda pgid, sig: seen.update(killed=(pgid, sig)),
        raising=False,
    )

    owner = _compat.ProcessTreeOwner()
    owner.popen(["sh", "-c", "sleep 30"])

    assert owner.terminate() is True
    assert seen["start_new_session"] is True
    assert seen["killed"] == (7123, 9)


def test_long_lived_spawn_keeps_its_existing_process_semantics(
    monkeypatch,
) -> None:
    seen: dict[str, object] = {}

    class Process:
        pass

    def popen(*args, **kwargs):
        seen.update(args=args, **kwargs)
        return Process()

    monkeypatch.setattr(
        local,
        "_invocation",
        lambda command, cwd=None: (command, True, None, False),
    )
    monkeypatch.setattr(local.subprocess, "Popen", popen)
    monkeypatch.setattr(local, "no_window_creation_flags", lambda: 0)

    proc = local.LocalBackend().spawn("long-running")

    assert getattr(proc, "_openprogram_sandboxed") is False
    assert seen["creationflags"] == 0
    assert "start_new_session" not in seen


def test_local_backend_replaces_invalid_utf8_without_reader_thread_failure(
    monkeypatch,
) -> None:
    argv = [sys.executable, "-c", "import os; os.write(1, b'\\x82')"]
    monkeypatch.setattr(
        local,
        "_invocation",
        lambda command, cwd=None: (argv, False, None, False),
    )

    result = local.LocalBackend().run("ignored", timeout=5)

    assert result.exit_code == 0
    assert result.stdout == "\ufffd"
