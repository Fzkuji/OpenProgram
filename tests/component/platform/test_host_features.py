"""The worker says which platform-dependent features its computer has, before a client offers them.

Windows and Linux are simulated at the platform seam (``sys.platform``,
``platform.system``, PATH lookups, session variables); nothing here ran on
either system.
"""
import platform
import shutil
import sys
import types

import pytest

from openprogram.interfaces.server import host_features


@pytest.fixture
def host(monkeypatch):
    """Become another platform: its name, and a PATH that holds only the named tools."""
    from openprogram.host._platform import environment

    def become(name, tools=(), environ=()):
        monkeypatch.setattr(sys, "platform", name)
        monkeypatch.setattr(platform, "system", lambda: {"win32": "Windows", "darwin": "Darwin"}.get(name, "Linux"))
        monkeypatch.setattr(shutil, "which", lambda tool, *_a, **_k: f"/tools/{tool}" if tool in tools else None)
        if name == "win32":
            # A registry with no WSL key, as on a Windows without WSL.
            def no_key(*_args):
                raise FileNotFoundError("Lxss")
            monkeypatch.setitem(sys.modules, "winreg", types.SimpleNamespace(
                HKEY_CURRENT_USER=1, OpenKey=no_key, QueryValueEx=no_key))
        for variable in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE"):
            monkeypatch.delenv(variable, raising=False)
        for variable, value in dict(environ).items():
            monkeypatch.setenv(variable, value)
        environment.windows_wsl_sandbox_reason.cache_clear()
        environment._windows_default_wsl2_distribution.cache_clear()
        return {row["id"]: row for row in host_features.report()["features"]}

    yield become
    environment.windows_wsl_sandbox_reason.cache_clear()
    environment._windows_default_wsl2_distribution.cache_clear()


def test_every_row_is_available_or_says_why_on_this_computer():
    report = host_features.report()
    assert report["schema"] == 1 and report["platform"] in ("macos", "windows", "linux")
    assert [row["id"] for row in report["features"]] == [feature[0] for feature in host_features.FEATURES]
    for row in report["features"]:
        assert row["available"] is (row["reason"] == ""), row
        assert "could not be checked" not in row["reason"], row


def test_a_bare_windows_refuses_each_missing_feature_by_name(host):
    rows = host("win32")
    assert host_features.report()["platform"] == "windows"
    for row in rows.values():
        assert "could not be checked" not in row["reason"] and "Traceback" not in row["reason"], row
        assert row["available"] is (row["reason"] == ""), row
    assert rows["command_sandbox"]["reason"].startswith("Sandbox unavailable: needs WSL2 (Windows sandbox needs wsl.exe)")
    assert "wsl --install" in rows["command_sandbox"]["reason"]
    assert "macOS only" in rows["sandbox_network_domains"]["reason"]
    assert "macOS" in rows["computer_use_code"]["reason"]
    assert rows["workflow_recording"]["reason"] == "Recording is available on macOS only."
    assert rows["meeting_system_audio"]["reason"] == "The computer's sound is recorded on macOS only."
    assert "UI Automation is not available" in rows["computer_use_app"]["reason"]
    assert "powershell.exe" in rows["system_notifications"]["reason"]
    assert "clip.exe" in rows["clipboard"]["reason"]
    assert "openprogram upgrade" in rows["conversational_update"]["reason"]
    # What Windows has without anything installed.
    assert rows["keep_awake"]["available"] and rows["reveal_in_file_manager"]["available"]
    assert rows["worker_service"]["available"]


def test_windows_with_its_stock_tools_notifies_and_copies(host):
    rows = host("win32", tools=("powershell.exe", "clip.exe"))
    assert rows["system_notifications"]["available"] and rows["clipboard"]["available"]


def test_a_headless_linux_refuses_each_desktop_feature_by_name(host):
    rows = host("linux")
    for row in rows.values():
        assert "could not be checked" not in row["reason"] and "Traceback" not in row["reason"], row
        assert row["available"] is (row["reason"] == ""), row
    assert "DISPLAY is not set" in rows["computer_use_app"]["reason"]
    assert "systemd-inhibit" in rows["keep_awake"]["reason"]
    assert "notify-send" in rows["system_notifications"]["reason"]
    assert "no graphical session" in rows["reveal_in_file_manager"]["reason"]
    assert "Wayland" in rows["clipboard"]["reason"]
    assert rows["command_sandbox"]["available"] is False and rows["command_sandbox"]["reason"]
    assert rows["worker_service"]["available"]


def test_a_linux_desktop_with_the_tools_has_the_desktop_features(host):
    rows = host("linux", tools=("systemd-inhibit", "notify-send", "xdg-open", "xclip"), environ={"DISPLAY": ":0"})
    for name in ("keep_awake", "system_notifications", "reveal_in_file_manager", "clipboard"):
        assert rows[name] == {**rows[name], "available": True, "reason": ""}, name


def test_a_probe_that_raises_is_one_unavailable_row(monkeypatch):
    def broken():
        raise ImportError("No module named 'Quartz'")
    monkeypatch.setattr(host_features, "FEATURES", (("x", "X", "X", None, broken),
                                                    ("y", "Y", "Y", "a.b", lambda: {"available": True, "reason": ""})))
    rows = host_features.report()["features"]
    assert rows[0]["available"] is False and "ImportError" in rows[0]["reason"]
    assert rows[1] == {"id": "y", "label": "Y", "label_zh": "Y", "setting": "a.b", "available": True, "reason": ""}


def test_every_setting_a_row_names_is_a_real_setting():
    from openprogram.state.settings import SETTINGS
    keys = {spec.key for spec in SETTINGS}
    named = {feature[3] for feature in host_features.FEATURES if feature[3]}
    assert named <= keys, named - keys


def test_the_route_serves_the_report():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.interfaces.server.routes.settings import misc

    app = FastAPI()
    misc.register(app)
    response = TestClient(app).get("/api/system/features")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert [row["id"] for row in response.json()["features"]] == [feature[0] for feature in host_features.FEATURES]


def test_a_windows_runtime_in_the_version_store_updates_from_a_conversation(host, monkeypatch):
    # The version store's controller runs on Windows as a Scheduled Task, so
    # only an install outside it is sent to the CLI and App updaters.
    monkeypatch.setattr("openprogram.operations.self_update.versions.layout.managed", lambda: True)
    row = host("win32")["conversational_update"]
    assert (row["available"], row["reason"]) == (True, "")


def test_a_disabled_application_is_reported_as_not_enabled(monkeypatch):
    monkeypatch.setattr(host_features, "_application", lambda _name: None)
    rows = {row["id"]: row for row in host_features.report()["features"]}
    assert rows["workflow_recording"]["reason"] == "Workflow recording is not enabled."
    assert rows["meeting_system_audio"]["reason"] == "Meetings is not enabled."


def test_the_command_shell_row_names_the_shell_and_its_promises(host, monkeypatch):
    from openprogram.host._platform import command_shell

    monkeypatch.setattr(command_shell, "_HOST_CACHE", {})
    monkeypatch.setattr("openprogram.host.sandbox.resolve_policy", lambda **kw: None)
    # The Windows probe reads PATH itself, never shutil.which.
    monkeypatch.setattr(command_shell, "_exists", lambda path: path == r"C:\tools\powershell.exe")
    row = host("win32", environ={"PATH": r"C:\tools"})["command_shell"]
    assert row["available"] is True and row["reason"] == ""
    shell = row["shell"]
    assert (shell["kind"], shell["edition"], shell["path"]) == (
        "powershell51", "5.1", r"C:\tools\powershell.exe")
    assert shell["sandboxed"] is False
    # A promise this shell does not keep is stated, never left out.
    assert shell["promises"]["recoverable_delete"] == "unsupported"
    assert shell["promises"]["utf8_file_output"] == "partial"
    assert tuple(shell["promises"]) == command_shell.PROMISES
