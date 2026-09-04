"""Native verification uses approved checks, not model-supplied commands."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

import pytest

from openprogram.self_update import native_checks, repair_candidate
from tests.component.self_update.test_verification_plan import _plan, _public_prepare
from tests.component.programs.test_self_update_tools import _isolated_owner  # noqa: F401
from tests.component.self_update.test_verification_channel import (
    consume, live, store_fixture, verifier,  # noqa: F401
)


def _cli_plan(entry="cli:version"):
    plan = _plan()
    plan["checks"][0]["entry"] = entry
    return plan


def _app(tmp_path):
    app = tmp_path / "fixture-app"
    resources = app / "Contents/Resources"
    runtime = resources / "runtime"
    prefix = runtime / "python"
    executable = prefix / "bin/python3.12"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture executable")
    executable.chmod(0o700)
    package = prefix / "lib/python3.12/site-packages/openprogram"
    package.mkdir(parents=True)
    (package / "_build_revision.txt").write_text("2" * 40)
    (package / "__main__.py").write_text("print('fixture')")
    (resources / "openprogram-source-revision").write_text("2" * 40)
    (runtime / "runtime-manifest.json").write_text(json.dumps({"schema": 2, "python": "python/bin/python3.12"}))
    return app


def test_public_prepare_accepts_fixed_installed_cli_check(tmp_path, monkeypatch):
    app = _app(tmp_path)
    actual = native_checks.runtime_identity
    # Redirect only the installation location; parse/hash actual fixture files.
    monkeypatch.setattr(native_checks, "runtime_identity", lambda _app, **kw: actual(app, **kw))
    result, store = _public_prepare(tmp_path, monkeypatch, _cli_plan())
    assert not result.is_error, result.content
    from openprogram.self_update.verifier_config import load_verifier_config
    record = store.load(json.loads(result.content[0].text)["update_id"])
    assert load_verifier_config(store, record)["verification_plan"] == _cli_plan()


@pytest.mark.parametrize("change", ["missing", "revision", "symlink", "escape", "schema"])
def test_runtime_identity_rejects_incompatible_installation(tmp_path, change):
    app = _app(tmp_path)
    identity = native_checks.runtime_identity(app, expected_revision="2" * 40)
    prefix = Path(identity["prefix"])
    if change == "missing":
        (prefix / "lib/python3.12/site-packages/openprogram/_build_revision.txt").unlink()
    elif change == "revision":
        (app / "Contents/Resources/openprogram-source-revision").write_text("3" * 40)
    elif change == "symlink":
        executable = Path(identity["python"])
        executable.rename(executable.with_suffix(".real"))
        executable.symlink_to(executable.with_suffix(".real"))
    else:
        manifest = {"schema": True if change == "schema" else 2,
                    "python": "../../outside" if change == "escape" else "python/bin/python3.12"}
        (app / "Contents/Resources/runtime/runtime-manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="runtime identity"):
        native_checks.runtime_identity(app, expected_revision="2" * 40)


@pytest.fixture
def installed_cli(monkeypatch):
    prefix = "/Applications/OpenProgram.app/Contents/Resources/runtime/python"
    identity = dict(python=prefix + "/bin/python3.12", prefix=prefix, revision="2" * 40,
                    python_sha256="a" * 64, manifest_sha256="b" * 64, cli_sha256="c" * 64)
    monkeypatch.setattr(native_checks, "runtime_identity", lambda *a, **kw: deepcopy(identity))
    actual = repair_candidate._test
    control = {}

    def execute(argv, *args, **kwargs):
        assert argv[0] == identity["python"]
        control["argv"] = list(argv)
        # Map installed interpreter location only. Watchdog, sandbox, CLI,
        # subprocess output and cleanup execute normally, not mocked results.
        mapped = [sys.executable, *argv[1:]]
        if control.get("nonzero"):
            mapped = [sys.executable, "-I", "-B", "-c", "print('failed CLI');raise SystemExit(3)"]
        result = actual(mapped, *args, **kwargs)
        if control.get("drift"):
            identity["python_sha256"] = "d" * 64
        return result

    monkeypatch.setattr(repair_candidate, "_test", execute)
    return control


@pytest.fixture
def native_verifier(installed_cli, verifier):
    verifier.native = installed_cli
    return verifier


@pytest.mark.parametrize("verifier", [_cli_plan(), _cli_plan("cli:help")], indirect=True)
def test_registered_cli_observer_produces_bound_native_receipt(native_verifier):
    v = native_verifier
    v.run()
    assert not v.control["tool_result"].is_error, v.control["tool_result"]
    assert consume(v)["verdict"] == "pass"
    observed = v.control["observed"]
    assert observed["status"] == 0 and observed["body"]
    assert observed["execution"]["argv"] == v.native["argv"]
    assert observed["execution"]["cleanup_complete"] is True
    assert not list((v.store.root / v.request.update_id).glob("native-check-*"))


@pytest.mark.parametrize("verifier", [_cli_plan()], indirect=True)
@pytest.mark.parametrize("failure", ["nonzero", "drift"])
def test_native_failure_cannot_be_reported_as_pass(native_verifier, failure):
    v = native_verifier
    v.native[failure] = True
    v.run()
    assert consume(v)["verdict"] == "inconclusive"
    assert not list((v.store.root / v.request.update_id).glob("native-check-*"))


@pytest.mark.parametrize("attempt", ["write", "home", "network"])
def test_native_sandbox_denies_source_write_home_read_and_network(tmp_path, attempt):
    candidate, scratch = tmp_path / "candidate", tmp_path / "scratch"
    candidate.mkdir()
    scratch.mkdir()
    source = candidate / "source.txt"
    source.write_text("unchanged")
    # conftest redirects HOME into its own temporary test directory.
    private = Path.home() / "native-check-private"
    private.write_text("private-marker")
    code = {"write": f"open({str(source)!r}, 'w').write('changed')",
            "home": f"print(open({str(private)!r}).read())",
            "network": "import socket;socket.create_connection(('127.0.0.1',18100), timeout=1)"}[attempt]
    status, output = repair_candidate._test([sys.executable, "-I", "-B", "-c", code],
                                            candidate, scratch, 5, lambda: None, verification=True, output_limit=4096)
    assert status != 0 and "private-marker" not in output
    assert "PermissionError" in output or "Operation not permitted" in output
    assert source.read_text() == "unchanged"
    assert not (scratch / "test.log").exists()


@pytest.mark.parametrize("case", ["shell", "output", "timeout", "cancel"])
def test_native_command_limits(tmp_path, case):
    candidate, scratch = tmp_path / "candidate", tmp_path / "scratch"
    candidate.mkdir()
    scratch.mkdir()
    command = [sys.executable, "-I", "-B", "-c", "print('x'*1000)" if case == "output" else "import time;time.sleep(10)"]
    if case == "shell":
        command = "python --version"
    started = time.monotonic()

    def check():
        if case == "cancel" and time.monotonic() - started > .2:
            raise ValueError("cancelled")

    if case == "timeout":
        status, _ = repair_candidate._test(command, candidate, scratch, .3, check, verification=True, output_limit=100)
        assert status != 0
    else:
        with pytest.raises(ValueError, match={"shell": "fixed argv", "output": "approved limit", "cancel": "cancelled"}[case]):
            repair_candidate._test(command, candidate, scratch, 5, check, verification=True, output_limit=100)
    assert time.monotonic() - started < 5
    assert not (scratch / "test.log").exists()
