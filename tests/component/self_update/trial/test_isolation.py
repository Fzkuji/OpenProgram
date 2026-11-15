"""A trial is confined only where the sandbox really runs, and says why not."""
import json
from types import SimpleNamespace

from openprogram.host import sandbox as host_sandbox
from openprogram.operations.self_update.trial import sandbox


def _linux(monkeypatch, reason):
    monkeypatch.setattr(sandbox, "sys", SimpleNamespace(platform="linux", executable="python"))
    monkeypatch.setattr(host_sandbox, "bubblewrap_unavailable_reason", lambda: reason)


def test_restricted_namespaces_fall_back_to_data_only_and_record_why(monkeypatch, tmp_path):
    reason = "Linux bubblewrap cannot create the required namespaces: setting up uid map: Permission denied"
    _linux(monkeypatch, reason)

    assert sandbox.isolation_level() == "data-only"
    assert not sandbox.needs_host_relay()
    sandbox.record_isolation(tmp_path)
    assert json.loads((tmp_path / "isolation.json").read_text()) == {"level": "data-only", "reason": reason}


def test_data_only_runs_the_command_unwrapped(monkeypatch, tmp_path):
    _linux(monkeypatch, "no namespaces")
    policy = sandbox.TrialPolicy(tmp_path, tmp_path / "live", (), 1, 2)
    assert sandbox.wrap_command(policy, ["python", "-V"], profile_path=tmp_path / "profile") == ["python", "-V"]


def test_a_working_bubblewrap_confines_the_trial(monkeypatch):
    _linux(monkeypatch, None)
    assert sandbox.isolation_level() == "sandbox" and sandbox.needs_host_relay()
    assert sandbox.isolation_reason() is None


def test_the_probe_failure_names_the_apparmor_remedy(monkeypatch):
    host_sandbox._bwrap_unavailable_reason.cache_clear()
    monkeypatch.setattr(host_sandbox, "_apparmor_restricts_namespaces", lambda: True)
    monkeypatch.setattr(host_sandbox.subprocess, "run", lambda *_a, **_o: SimpleNamespace(
        returncode=1, stderr="bwrap: setting up uid map: Permission denied\n", stdout="",
    ))
    try:
        reason = host_sandbox._bwrap_unavailable_reason("/usr/bin/bwrap")
    finally:
        host_sandbox._bwrap_unavailable_reason.cache_clear()
    assert "Permission denied" in reason and "apparmor_restrict_unprivileged_userns=0" in reason
