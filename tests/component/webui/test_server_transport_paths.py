from __future__ import annotations

from pathlib import Path

import openprogram

from openprogram.webui.routes import chat, docs, functions, programs


ROOT = Path(__file__).resolve().parents[3]
CORE_PROGRAMS = Path(openprogram.__file__).resolve().parent / "programs"


def test_moved_server_routes_resolve_source_and_core_roots() -> None:
    assert docs._repo_root() == ROOT
    assert programs.PROGRAMS_ROOT == CORE_PROGRAMS
    assert functions._programs_root() == CORE_PROGRAMS


def test_function_run_falls_back_to_configured_default_workdir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    selected = tmp_path / "selected-project"
    selected.mkdir()
    monkeypatch.setattr(chat, "get_default_workdir", lambda: str(selected))

    resolved = chat._resolve_work_dir()

    assert resolved == str(selected)

def test_function_run_inherits_the_bound_project_workdir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    selected = tmp_path / "bound-project"
    selected.mkdir()
    monkeypatch.setattr(
        chat,
        "project_workdir_for",
        lambda session_id: selected if session_id == "s1" else None,
        raising=False,
    )
    monkeypatch.setattr(
        chat,
        "get_default_workdir",
        lambda: str(tmp_path / "wrong-default"),
    )

    resolved = chat._resolve_work_dir("s1")

    assert resolved == str(selected)
