import hashlib
import json
from pathlib import Path
import subprocess
import time

import pytest


def _probe_tree(tmp_path: Path):
    update = tmp_path / "update"
    base = update / "build-home/runtime-base"
    artifact = update / "artifact/OpenProgram.app"
    trusted = base / "assets/playwright/browser/data"
    candidate = artifact / "Contents/Resources/runtime/assets/playwright/browser/data"
    trusted.parent.mkdir(parents=True)
    candidate.parent.mkdir(parents=True)
    trusted.write_bytes(b"trusted browser")
    candidate.write_bytes(b"trusted browser")
    manifest = artifact / "Contents/Resources/runtime/runtime-manifest.json"
    manifest.write_text(json.dumps({
        "schema": 2, "browser_probe": "deferred",
        "capabilities": {"browser.playwright": {"present": True, "verified": False}},
    }))
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()
    binding = {
        "path": "runtime/runtime-manifest.json",
        "sha256": manifest_sha256,
    }
    update_resources = artifact / "Contents/Resources/update"
    update_resources.mkdir()
    for name in ("reopen-protocol.json", "ui-verification-protocol.json"):
        (update_resources / name).write_text(
            json.dumps({"bindings": {"runtime_manifest": dict(binding)}})
        )
    return update, base, artifact, manifest, candidate


@pytest.fixture
def package_validators(monkeypatch):
    from openprogram.self_update import package_protocol

    calls = []
    monkeypatch.setattr(
        package_protocol,
        "validate_reopen_package",
        lambda artifact: calls.append(("reopen", artifact)),
    )
    monkeypatch.setattr(
        package_protocol,
        "validate_ui_package",
        lambda artifact: calls.append(("ui", artifact)),
    )
    return calls


class _Process:
    pid = 12345
    returncode = 0

    def __init__(self, args, **kwargs):
        self.args, self.kwargs = args, kwargs

    def communicate(self, timeout):
        return "TRUSTED_BROWSER_PROBE_OK\n", ""

    def poll(self):
        return self.returncode


def test_trusted_browser_probe_binds_assets_and_finalizes_manifest(
    tmp_path, monkeypatch, package_validators
):
    from openprogram.self_update import controller_bundle, supervisor

    update, base, artifact, manifest, _candidate = _probe_tree(tmp_path)
    processes = []
    monkeypatch.setattr(controller_bundle, "_runtime_python", lambda root: root / "python/bin/python3")
    monkeypatch.setattr(
        supervisor.subprocess,
        "Popen",
        lambda *a, **kw: processes.append(_Process(*a, **kw)) or processes[-1],
    )
    supervisor._complete_browser_probe(artifact, base, update, deadline=time.time() + 10)
    result = json.loads(manifest.read_text())
    assert result["browser_probe"] == "complete"
    assert result["capabilities"]["browser.playwright"] == {
        "present": True,
        "verified": True,
    }
    receipt = json.loads((update / "browser-probe.json").read_text())
    assert receipt["schema"] == 1 and len(receipt["browser_sha256"]) == 64
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()
    for name in ("reopen-protocol.json", "ui-verification-protocol.json"):
        protocol = json.loads(
            (artifact / "Contents/Resources/update" / name).read_text()
        )
        assert protocol["bindings"]["runtime_manifest"]["sha256"] == manifest_sha256
    assert [name for name, _artifact in package_validators] == [
        "reopen",
        "ui",
        "reopen",
        "ui",
    ]
    assert processes[0].args[0] == str(base / "python/bin/python3")
    assert set(processes[0].kwargs["env"]) == {
        "PATH",
        "HOME",
        "TMPDIR",
        "PLAYWRIGHT_BROWSERS_PATH",
    }


@pytest.mark.parametrize("tamper", ["asset", "manifest"])
def test_trusted_browser_probe_rejects_unbound_input_before_launch(
    tmp_path, monkeypatch, package_validators, tamper
):
    from openprogram.self_update import supervisor

    update, base, artifact, manifest, candidate = _probe_tree(tmp_path)
    if tamper == "asset":
        candidate.write_bytes(b"different")
    else:
        value = json.loads(manifest.read_text())
        value["browser_probe"] = "complete"
        manifest.write_text(json.dumps(value))
    monkeypatch.setattr(
        supervisor.subprocess,
        "Popen",
        lambda *_a, **_kw: pytest.fail("unbound browser launched"),
    )
    with pytest.raises(RuntimeError, match="browser assets|deferred browser"):
        supervisor._complete_browser_probe(artifact, base, update, deadline=time.time() + 10)
    assert not (update / "browser-probe.json").exists()


def test_trusted_browser_probe_timeout_kills_its_process_group(
    tmp_path, monkeypatch, package_validators
):
    from openprogram.self_update import controller_bundle, supervisor

    update, base, artifact, _manifest, _candidate = _probe_tree(tmp_path)
    process = _Process([], env={})
    process.returncode = None
    process.communicate = lambda timeout: (_ for _ in ()).throw(
        subprocess.TimeoutExpired("probe", timeout)
    )
    process.poll = lambda: None
    process.wait = lambda: setattr(process, "returncode", -9)
    killed = []
    monkeypatch.setattr(controller_bundle, "_runtime_python", lambda root: root / "python/bin/python3")
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *_a, **_kw: process)
    monkeypatch.setattr(supervisor.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(subprocess.TimeoutExpired):
        supervisor._complete_browser_probe(artifact, base, update, deadline=time.time() + 1)
    assert killed and process.returncode == -9
    assert not (update / "browser-probe.json").exists()


def test_manifest_rebind_rejects_a_protocol_with_the_wrong_prior_hash(tmp_path):
    from openprogram.self_update import supervisor

    update, _base, artifact, manifest, _candidate = _probe_tree(tmp_path)
    manifest.write_text(manifest.read_text() + "\n")
    with pytest.raises(RuntimeError, match="prior manifest"):
        supervisor._rebind_runtime_manifest(
            artifact / "Contents/Resources", "0" * 64
        )
    assert not (update / "browser-probe.json").exists()
