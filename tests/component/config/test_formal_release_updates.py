from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_immutable_runtime_takes_precedence_over_checkout(monkeypatch):
    from openprogram.updater import detect

    monkeypatch.setenv("OPENPROGRAM_IMMUTABLE_RUNTIME", "1")
    monkeypatch.setattr(detect, "repo_root", lambda: object())
    monkeypatch.setattr(detect, "is_pyinstaller_binary", lambda: True)

    assert detect.detect_install_method() is detect.InstallMethod.MANAGED_RELEASE


def test_source_checkout_is_not_a_managed_release(monkeypatch):
    from openprogram.updater import detect

    monkeypatch.delenv("OPENPROGRAM_IMMUTABLE_RUNTIME", raising=False)
    monkeypatch.setattr(detect, "repo_root", lambda: object())
    monkeypatch.setattr(detect, "is_pyinstaller_binary", lambda: False)

    assert detect.detect_install_method() is detect.InstallMethod.SOURCE_CHECKOUT


def test_worker_start_does_not_apply_product_updates():
    from pathlib import Path
    import openprogram.worker.runner as runner

    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "background_check_and_apply" not in source


def test_system_version_reports_managed_release(monkeypatch):
    from openprogram.updater.detect import InstallMethod
    from openprogram.webui.routes.config import register

    monkeypatch.setattr(
        "openprogram.updater.detect.detect_install_method",
        lambda: InstallMethod.MANAGED_RELEASE,
    )
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda package: "0.6.7" if package == "openprogram" else "unexpected",
    )
    app = FastAPI()
    register(app)

    response = TestClient(app).get("/api/system/version")

    assert response.status_code == 200
    assert response.json() == {
        "currentVersion": "0.6.7",
        "installType": "managed_release",
    }
