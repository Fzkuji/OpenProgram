from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

from openprogram.host._platform import environment
from openprogram.host._platform import processes


def test_posix_tree_kill_does_not_signal_an_inherited_process_group(
    monkeypatch,
) -> None:
    calls: list[tuple[str, int, int]] = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(os, "getpgid", lambda _pid: 400, raising=False)
    monkeypatch.setattr(os, "getpgrp", lambda: 400, raising=False)
    monkeypatch.setattr(
        os,
        "killpg",
        lambda pgid, sig: calls.append(("group", pgid, sig)),
        raising=False,
    )
    monkeypatch.setattr(
        os,
        "kill",
        lambda pid, sig: calls.append(("process", pid, sig)),
    )

    assert processes.kill_process_tree(401) is True
    assert calls == [("process", 401, 9)]


def test_posix_tree_kill_signals_a_detached_child_session(monkeypatch) -> None:
    calls: list[tuple[str, int, int]] = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(os, "getpgid", lambda pid: pid, raising=False)
    monkeypatch.setattr(os, "getpgrp", lambda: 400, raising=False)
    monkeypatch.setattr(
        os,
        "killpg",
        lambda pgid, sig: calls.append(("group", pgid, sig)),
        raising=False,
    )
    monkeypatch.setattr(
        os,
        "kill",
        lambda pid, sig: calls.append(("process", pid, sig)),
    )

    assert processes.kill_process_tree(500) is True
    assert calls == [("group", 500, 9)]


def test_linux_proc_port_fallback_maps_listening_socket_to_pids(
    tmp_path: Path, monkeypatch,
) -> None:
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    # Port 18100 == 0x46B4; only TCP state 0A is LISTEN.
    (proc / "net" / "tcp").write_text(
        "sl local_address rem_address st tx_queue rx_queue tr tm retr uid timeout inode\n"
        "0: 0100007F:46B4 00000000:0000 0A 0:0 00:0 0 1000 0 55555\n"
        "1: 0100007F:46B4 00000000:0000 01 0:0 00:0 0 1000 0 66666\n",
        encoding="ascii",
    )
    (proc / "net" / "tcp6").write_text(
        "sl local_address rem_address st tx_queue rx_queue tr tm retr uid timeout inode\n",
        encoding="ascii",
    )
    descriptors = [
        os.path.join(str(proc), "123", "fd", "4"),
        os.path.join(str(proc), "456", "fd", "9"),
        os.path.join(str(proc), "789", "fd", "2"),
    ]
    monkeypatch.setattr("glob.iglob", lambda _pattern: iter(descriptors))
    targets = {
        descriptors[0]: "socket:[55555]",
        descriptors[1]: "socket:[55555]",
        descriptors[2]: "socket:[66666]",
    }
    monkeypatch.setattr(os, "readlink", lambda path: targets[path])

    assert processes._linux_proc_pids_on_port(18100, proc_root=str(proc)) == [123, 456]


def test_linux_port_probe_falls_back_when_lsof_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        processes._subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(processes, "_linux_proc_pids_on_port", lambda port: [port])

    assert processes.pids_on_port(18100) == [18100]


def test_worker_pid_requires_a_live_lock(monkeypatch) -> None:
    from openprogram.operations.worker import lifecycle

    monkeypatch.setattr(lifecycle, "read_holder_pid", lambda: 321)
    monkeypatch.setattr(lifecycle, "_process_alive", lambda _pid: True)
    monkeypatch.setattr(lifecycle, "is_held_by", lambda _pid: False)

    assert lifecycle.current_worker_pid() is None

    monkeypatch.setattr(lifecycle, "is_held_by", lambda _pid: True)
    assert lifecycle.current_worker_pid() == 321


def test_worker_pid_files_reject_nonpositive_signal_targets(
    tmp_path: Path, monkeypatch,
) -> None:
    from openprogram.operations.worker import lock as worker_lock

    lock_file = tmp_path / "worker.lock"
    lock_file.write_text("0\n", encoding="utf-8")
    monkeypatch.setattr(worker_lock, "lock_path", lambda: lock_file)

    assert worker_lock.read_holder_pid() is None


def test_linux_doctor_advisories_keep_optional_capabilities_non_blocking(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        "openprogram.host.sandbox.unavailable_reason",
        lambda: "Linux needs bubblewrap",
    )
    monkeypatch.setattr(
        environment,
        "_linux_systemd_user_reason",
        lambda: "Failed to connect to bus",
    )

    rows = environment.platform_environment_advisories(tmp_path)

    assert all(ok is True for ok, _label, _detail in rows)
    assert rows[0][1:] == (
        "linux sandbox",
        "optional isolation unavailable: Linux needs bubblewrap",
    )
    assert rows[1][1] == "systemd user service"
    assert "openprogram worker start" in rows[1][2]


def test_linux_browser_launch_requires_a_graphical_session(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    assert environment.can_open_browser() is False

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert environment.can_open_browser() is True


def test_headless_browser_open_does_not_invoke_webbrowser(monkeypatch) -> None:
    import webbrowser

    monkeypatch.setattr(environment, "can_open_browser", lambda: False)
    monkeypatch.setattr(
        webbrowser,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("browser helper was launched")
        ),
    )

    assert environment.open_browser_url("http://127.0.0.1:18100") is False


def test_web_command_explicit_port_overrides_inherited_environment(
    monkeypatch,
) -> None:
    from openprogram.interfaces.cli.commands.services import web
    from openprogram.interfaces.cli import ink
    import openprogram.operations.worker

    observed: list[str | None] = []
    monkeypatch.setenv("OPENPROGRAM_WEB_PORT", "18100")
    monkeypatch.setattr(web, "_port_in_use", lambda _port: False)
    monkeypatch.setattr(web, "_browser_url", lambda port: f"http://localhost:{port}")
    monkeypatch.setattr(
        openprogram.operations.worker,
        "spawn_detached",
        lambda: observed.append(os.environ.get("OPENPROGRAM_WEB_PORT")) or 0,
    )
    monkeypatch.setattr(ink, "_wait_until_listening", lambda *_args, **_kwargs: True)
    # The worker names its port once it is started; without one the command
    # waits the whole start-up time for it.
    from openprogram.operations.worker import lifecycle
    monkeypatch.setattr(lifecycle, "read_worker_port", lambda: 19090 if observed else None)

    web._cmd_web(19090, open_browser=False)

    assert observed == ["19090"]
