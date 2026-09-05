from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram_server._webui.routes import workdir


def _linux(monkeypatch) -> None:
    monkeypatch.setattr(workdir.sys, "platform", "linux")


def _client() -> TestClient:
    app = FastAPI()
    workdir.register(app)
    return TestClient(app)


def test_linux_native_picker_is_unsupported_without_a_display(monkeypatch):
    _linux(monkeypatch)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    def unexpected_run(*args, **kwargs):
        raise AssertionError("headless Linux must not launch a GUI picker")

    monkeypatch.setattr(subprocess, "run", unexpected_run)

    assert workdir._pick_folder_native("/srv/project") == (None, True)


def test_headless_linux_api_requests_manual_path_fallback(monkeypatch):
    _linux(monkeypatch)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("headless API must not launch a GUI picker")
        ),
    )

    response = _client().post("/api/pick-folder", json={"start": "/srv/project"})

    assert response.status_code == 200
    assert response.json() == {"path": None, "unsupported": True}


def test_manual_folder_path_is_validated_on_worker(tmp_path: Path):
    chosen = tmp_path / "server project"
    chosen.mkdir()

    response = _client().post(
        "/api/pick-folder", json={"manual_path": str(chosen)}
    )

    assert response.status_code == 200
    assert response.json() == {
        "path": str(chosen.resolve()),
        "unsupported": False,
    }


def test_manual_folder_path_rejects_relative_and_missing_paths(tmp_path: Path):
    relative = _client().post(
        "/api/pick-folder", json={"manual_path": "relative/project"}
    )
    missing = _client().post(
        "/api/pick-folder", json={"manual_path": str(tmp_path / "missing")}
    )

    assert relative.status_code == 400
    assert relative.json()["error"] == "folder path must be absolute"
    assert missing.status_code == 400
    assert missing.json()["error"].startswith("not a directory:")


def test_linux_native_picker_reports_an_explicit_cancel(monkeypatch):
    _linux(monkeypatch)
    monkeypatch.setenv("DISPLAY", ":0")
    calls: list[list[str]] = []

    def cancelled(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", cancelled)

    assert workdir._pick_folder_native("/srv/project") == (None, False)
    assert [command[0] for command in calls] == ["zenity"]


def test_linux_native_picker_tries_kde_after_gtk_startup_failure(monkeypatch):
    _linux(monkeypatch)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("DISPLAY", raising=False)
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        if command[0] == "zenity":
            return subprocess.CompletedProcess(
                command, 2, stdout="", stderr="cannot open display"
            )
        return subprocess.CompletedProcess(
            command, 0, stdout="/srv/project/child\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", run)

    assert workdir._pick_folder_native("/srv/project") == (
        "/srv/project/child",
        False,
    )
    assert [command[0] for command in calls] == ["zenity", "kdialog"]


def test_linux_native_picker_is_unsupported_when_all_frontends_fail(monkeypatch):
    _linux(monkeypatch)
    monkeypatch.setenv("DISPLAY", ":0")

    def broken(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 127, stdout="", stderr=f"{command[0]} failed"
        )

    monkeypatch.setattr(subprocess, "run", broken)

    assert workdir._pick_folder_native("/srv/project") == (None, True)
