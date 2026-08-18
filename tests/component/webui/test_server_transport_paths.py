from __future__ import annotations

from pathlib import Path

import openprogram

from openprogram.webui.routes import chat, docs, functions, programs, workdir


ROOT = Path(__file__).resolve().parents[3]
CORE_PROGRAMS = Path(openprogram.__file__).resolve().parent / "programs"


def test_moved_server_routes_resolve_source_and_core_roots() -> None:
    assert docs._repo_root() == ROOT
    assert programs.PROGRAMS_ROOT == CORE_PROGRAMS
    assert functions._programs_root() == CORE_PROGRAMS


def test_installed_server_registers_bundled_docs(tmp_path, monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    site = tmp_path / "bundled-docs"
    site.mkdir()
    (site / "index.html").write_text("bundled docs", encoding="utf-8")
    monkeypatch.setattr(docs, "_packaged_site_dir", lambda: site)
    monkeypatch.setattr("openprogram.updater.detect.repo_root", lambda: None)
    app = FastAPI()

    docs.register(app)

    assert TestClient(app).get("/docs/").text == "bundled docs"


def test_function_run_falls_back_to_configured_default_workdir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    selected = tmp_path / "selected-project"
    selected.mkdir()
    monkeypatch.setattr(chat, "get_default_workdir", lambda: str(selected))

    conv: dict = {}
    resolved = chat._resolve_work_dir(conv, "gui_agent", None)

    assert resolved == str(selected)
    assert conv["last_workdirs"]["gui_agent"] == str(selected)


def test_workdir_defaults_reports_configured_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    selected = tmp_path / "selected-project"
    selected.mkdir()
    monkeypatch.setattr(workdir, "get_default_workdir", lambda: str(selected))
    app = FastAPI()
    workdir.register(app)

    response = TestClient(app).get("/api/workdir/defaults")

    assert response.status_code == 200
    assert response.json()["repo"] == str(selected)
