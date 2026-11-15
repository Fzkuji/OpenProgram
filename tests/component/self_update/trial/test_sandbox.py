"""A trial reaches loopback only, and cannot read or write what is live."""
from __future__ import annotations

from pathlib import Path
import socket
import subprocess
import sys
import textwrap
import threading

import pytest

from openprogram.operations.self_update.trial import sandbox

pytestmark = pytest.mark.skipif(sys.platform != "darwin" or sandbox.isolation_level() != "sandbox",
                                reason="needs sandbox-exec")


@pytest.fixture
def listener():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    stop = threading.Event()

    def accept():
        while not stop.is_set():
            try:
                connection, _ = server.accept()
                connection.close()
            except OSError:
                return

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield server.getsockname()[1]
    stop.set()
    server.close()


def _policy(tmp_path: Path, *, live_ports=(), worker_port=None) -> tuple[sandbox.TrialPolicy, Path, Path]:
    live = tmp_path / "live"
    trial = live / "runtime" / "trials" / "u1"
    trial.mkdir(parents=True)
    (live / "secret.txt").write_text("secret")
    readable = live / "runtime" / "versions" / "v1"
    readable.mkdir(parents=True)
    (readable / "code.py").write_text("code")
    return sandbox.TrialPolicy(directory=trial, live_state=live, readable=(readable,), worker_port=worker_port or sandbox.free_port(),
                               model_port=sandbox.free_port(), live_ports=live_ports, home=tmp_path / "home"), live, trial


def _run(policy: sandbox.TrialPolicy, trial: Path, code: str) -> subprocess.CompletedProcess:
    argv = [sys.executable, "-I", "-B", "-S", "-c", textwrap.dedent(code)]
    return subprocess.run(sandbox.wrap_command(policy, argv, profile_path=trial / "sandbox.sb"), capture_output=True,
                          text=True, timeout=30)


def _connect(host: str, port: int) -> str:
    return f"""
    import socket
    try:
        socket.create_connection(({host!r}, {port}), timeout=3).close()
        print("connected")
    except OSError as error:
        print(type(error).__name__, error.errno)
    """


def test_the_outside_network_is_unreachable(tmp_path):
    policy, _live, trial = _policy(tmp_path)
    done = _run(policy, trial, _connect("1.1.1.1", 443))
    assert done.returncode == 0, done.stderr
    assert "connected" not in done.stdout and "PermissionError" in done.stdout


def test_the_trials_own_ports_are_reachable(tmp_path, listener):
    policy, _live, trial = _policy(tmp_path, worker_port=listener)
    done = _run(policy, trial, _connect("127.0.0.1", listener))
    assert done.stdout.strip() == "connected", done.stderr


def test_a_live_port_is_not(tmp_path, listener):
    policy, _live, trial = _policy(tmp_path, live_ports=(listener,))
    done = _run(policy, trial, _connect("127.0.0.1", listener))
    assert "connected" not in done.stdout and "PermissionError" in done.stdout


def test_live_state_cannot_be_read_but_the_version_can(tmp_path):
    policy, live, trial = _policy(tmp_path)
    secret, code = live / "secret.txt", live / "runtime" / "versions" / "v1" / "code.py"
    done = _run(policy, trial, f"""
    print(open({str(code)!r}).read())
    try:
        open({str(secret)!r}).read()
        print("read secret")
    except PermissionError:
        print("denied")
    """)
    assert done.stdout.split() == ["code", "denied"], done.stderr


def test_writes_stay_inside_the_trial(tmp_path):
    policy, live, trial = _policy(tmp_path)
    outside = tmp_path / "outside.txt"
    done = _run(policy, trial, f"""
    open({str(trial / "inside.txt")!r}, "w").write("ok")
    for path in ({str(outside)!r}, {str(live / "new.txt")!r}):
        try:
            open(path, "w").write("no")
            print("wrote")
        except PermissionError:
            print("denied")
    """)
    assert done.stdout.split() == ["denied", "denied"], done.stderr
    assert (trial / "inside.txt").read_text() == "ok" and not outside.exists()


def test_another_loopback_port_is_refused(tmp_path, listener):
    """One could be a proxy to the Internet, or another service of the owner's."""
    policy, _live, trial = _policy(tmp_path)
    done = _run(policy, trial, _connect("127.0.0.1", listener))
    assert "connected" not in done.stdout and "PermissionError" in done.stdout, done.stdout + done.stderr


def test_a_local_proxy_gives_no_way_out(tmp_path, listener):
    policy, _live, trial = _policy(tmp_path)
    done = _run(policy, trial, f"""
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({{"http": "http://127.0.0.1:{listener}"}}))
    try:
        opener.open("http://example.com/", timeout=3)
        print("reached")
    except OSError as error:
        print("refused", type(error).__name__)
    """)
    assert done.stdout.startswith("refused"), done.stdout + done.stderr


def test_the_desktop_check_runs_the_candidates_code_inside_the_sandbox(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from openprogram.operations.self_update.trial import engine
    from tests.support.sealed_versions import BASE, make_layout

    layout = make_layout(tmp_path / "runtime")
    main = layout.version("v1") / "desktop" / "main.js"
    main.parent.mkdir(parents=True)
    main.write_text("exports.start = () => {};")
    shell = tmp_path / "App"
    shell.write_text("")
    run = object.__new__(engine._Run)
    run.request = SimpleNamespace(shell_executable=shell, candidate="v1")
    run.layout, run.manifest = layout, {"base": BASE}
    run.workspace = SimpleNamespace(temporary=tmp_path, profile=tmp_path / "desktop.sb")
    run.candidate = SimpleNamespace(policy="the policy", environment=lambda: {})
    run.result = SimpleNamespace(notes=[])
    recorded = []
    run._record = lambda *args, **_: recorded.append(args)
    wrapped = []
    monkeypatch.setattr(engine.sandbox, "wrap_command", lambda policy, argv, **_: wrapped.append((policy, argv)) or ["sandboxed", *argv])
    outcomes = iter(["OPENPROGRAM_SHELL_STARTED\nOPENPROGRAM_MAIN_READY\n", ""])
    ran = []

    def fake_run(command, **_):
        ran.append(command)
        return SimpleNamespace(returncode=0, stdout=next(outcomes), stderr="")

    monkeypatch.setattr(engine.subprocess, "run", fake_run)

    run.desktop_check()
    assert ran[0][0] == "sandboxed" and wrapped[0][0] == "the policy"
    assert recorded == [("desktop_smoke",)]

    run.desktop_check()  # The App did not start at all under the sandbox: nothing of the candidate was judged.
    assert recorded[-1] == ("desktop_smoke", "unavailable") and run.result.notes


def _desktop_run(tmp_path, monkeypatch, fake_run):
    from types import SimpleNamespace

    from openprogram.operations.self_update.trial import engine
    from tests.support.sealed_versions import BASE, make_layout

    layout = make_layout(tmp_path / "runtime")
    main = layout.version("v1") / "desktop" / "main.js"
    main.parent.mkdir(parents=True)
    main.write_text("exports.start = () => {};")
    shell = tmp_path / "App"
    shell.write_text("")
    run = object.__new__(engine._Run)
    run.request = SimpleNamespace(shell_executable=shell, candidate="v1")
    run.layout, run.manifest = layout, {"base": BASE}
    run.workspace = SimpleNamespace(temporary=tmp_path, profile=tmp_path / "desktop.sb")
    run.candidate = SimpleNamespace(policy="the policy", environment=lambda: {})
    run.result = SimpleNamespace(notes=[])
    run.recorded = []
    run._record = lambda *args, **_: run.recorded.append(args)
    monkeypatch.setattr(engine.sandbox, "wrap_command", lambda policy, argv, **_: argv)
    monkeypatch.setattr(engine.subprocess, "run", fake_run)
    return run


def test_an_app_that_hangs_before_reaching_the_candidate_judges_nothing(tmp_path, monkeypatch):
    import subprocess

    def hung(command, **_):
        raise subprocess.TimeoutExpired(command, 60, output=b"")

    run = _desktop_run(tmp_path, monkeypatch, hung)
    run.desktop_check()
    assert run.recorded == [("desktop_smoke", "unavailable")] and run.result.notes


def test_a_candidate_that_hangs_once_loaded_fails_the_check(tmp_path, monkeypatch):
    import subprocess

    from openprogram.operations.self_update.trial.instance import TrialError

    def hung(command, **_):
        raise subprocess.TimeoutExpired(command, 60, output=b"OPENPROGRAM_SHELL_STARTED\n")

    run = _desktop_run(tmp_path, monkeypatch, hung)
    with pytest.raises(TrialError):
        run.desktop_check()


def test_a_window_the_sandbox_may_have_stopped_is_not_held_against_the_candidate(tmp_path, monkeypatch):
    from types import SimpleNamespace

    run = _desktop_run(tmp_path, monkeypatch, lambda command, **_: SimpleNamespace(
        returncode=1, stdout="OPENPROGRAM_SHELL_STARTED\n", stderr="Error: the first window did not load within 30 s"))
    run.desktop_check()
    assert run.recorded == [("desktop_smoke", "unavailable")]


def test_a_candidate_whose_code_throws_fails_the_check(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from openprogram.operations.self_update.trial.instance import TrialError

    run = _desktop_run(tmp_path, monkeypatch, lambda command, **_: SimpleNamespace(
        returncode=1, stdout="OPENPROGRAM_SHELL_STARTED\n", stderr="TypeError: not a function"))
    with pytest.raises(TrialError):
        run.desktop_check()


def test_a_trial_reaches_loopback_directly_whatever_proxy_the_system_has(tmp_path):
    """The sandbox refuses a loopback proxy, so a client that followed the system's would never get to the fake model."""
    import subprocess
    import sys
    from types import SimpleNamespace

    from openprogram.operations.self_update.trial.instance import TrialInstance
    from tests.support.sealed_versions import BASE, make_layout

    instance = object.__new__(TrialInstance)
    instance.layout, instance.base, instance.port = make_layout(tmp_path / "runtime"), BASE, 40000
    instance.workspace = SimpleNamespace(home=tmp_path, temporary=tmp_path)
    environment = instance.environment()
    assert "127.0.0.1" in environment["NO_PROXY"] and "127.0.0.1" in environment["no_proxy"]
    shown = subprocess.run([sys.executable, "-I", "-c", "import urllib.request as u; print(sorted(u.getproxies()))"],
                           env={**environment, "PATH": "/usr/bin:/bin"}, capture_output=True, text=True, check=True)
    assert shown.stdout.strip() == "['no']"
