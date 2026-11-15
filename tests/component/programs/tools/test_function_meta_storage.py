from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _set_test_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


@pytest.fixture()
def meta_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    legacy_dir = tmp_path / "installed" / "openprogram" / "webui"
    legacy_dir.mkdir(parents=True)
    _set_test_home(monkeypatch, home)
    monkeypatch.delenv("OPENPROGRAM_PROFILE", raising=False)

    import openprogram.state.paths as paths
    from openprogram.programs._host import meta_storage
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.routes.files import tree

    monkeypatch.setattr(paths, "_migration_checked", True)
    monkeypatch.setattr(meta_storage, "_legacy_directory", lambda: legacy_dir)
    monkeypatch.setattr(
        server,
        "__file__",
        str(tmp_path / "openprogram/interfaces/server/server.py"),
    )
    app = FastAPI()
    tree.register(app)
    return TestClient(app), home, legacy_dir


def test_program_meta_is_profile_scoped_and_never_written_to_package(
    meta_app,
    monkeypatch: pytest.MonkeyPatch,
):
    client, home, legacy_dir = meta_app
    monkeypatch.setenv("OPENPROGRAM_PROFILE", "alpha")

    response = client.post(
        "/api/programs/meta",
        json={"favorites": ["demo_agent"], "icons": {}},
    )

    assert response.status_code == 200
    alpha_path = home / ".openprogram-alpha" / "programs_meta.json"
    assert json.loads(alpha_path.read_text(encoding="utf-8"))["favorites"] == [
        "demo_agent"
    ]
    assert not (legacy_dir / "programs_meta.json").exists()

    monkeypatch.setenv("OPENPROGRAM_PROFILE", "beta")
    assert client.get("/api/programs/meta").json()["favorites"] == []
    monkeypatch.setenv("OPENPROGRAM_PROFILE", "alpha")
    assert client.get("/api/programs/meta").json()["favorites"] == ["demo_agent"]


def test_default_profile_copies_legacy_tool_profiles_into_state(meta_app):
    client, home, legacy_dir = meta_app
    legacy = legacy_dir / "functions_meta.json"
    legacy.write_text(
        json.dumps({"profiles": {"custom": ["read"]}, "active": "custom"}),
        encoding="utf-8",
    )

    response = client.get("/api/tool-profiles")

    assert response.status_code == 200
    assert response.json()["profiles"]["custom"] == ["read"]
    migrated = home / ".openprogram" / "functions_meta.json"
    assert json.loads(migrated.read_text(encoding="utf-8"))["profiles"]["custom"] == [
        "read"
    ]
    assert legacy.exists()


def test_program_meta_drops_the_retired_gui_agent_favorite(meta_app):
    client, home, _ = meta_app
    assert client.post(
        "/api/programs/meta",
        json={"favorites": ["demo_agent", "gui_agent"], "icons": {}},
    ).status_code == 200

    assert client.get("/api/programs/meta").json()["favorites"] == ["demo_agent"]
    stored = json.loads(
        (home / ".openprogram" / "programs_meta.json").read_text(encoding="utf-8")
    )
    assert stored["favorites"] == ["demo_agent"]


def test_toolset_resolution_reads_the_active_profile_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    home = tmp_path / "home"
    _set_test_home(monkeypatch, home)
    monkeypatch.setenv("OPENPROGRAM_PROFILE", "runtime")
    import openprogram.state.paths as paths

    monkeypatch.setattr(paths, "_migration_checked", True)
    state = home / ".openprogram-runtime"
    state.mkdir(parents=True)
    (state / "functions_meta.json").write_text(
        json.dumps({"profiles": {"runtime-only": ["read", "grep"]}}),
        encoding="utf-8",
    )

    from openprogram.programs import _resolve_folder_toolset

    assert _resolve_folder_toolset("runtime-only") == ["read", "grep"]


def test_failed_meta_write_keeps_the_previous_complete_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    home = tmp_path / "home"
    _set_test_home(monkeypatch, home)
    monkeypatch.setenv("OPENPROGRAM_PROFILE", "atomic")
    import openprogram.state.paths as paths
    from openprogram.programs._host.meta_storage import PROGRAMS_META, save_meta

    monkeypatch.setattr(paths, "_migration_checked", True)
    save_meta(PROGRAMS_META, {"favorites": ["old"]})
    state = home / ".openprogram-atomic"
    target = state / PROGRAMS_META

    original_write_text = Path.write_text

    def partial_then_fail(self: Path, data: str, **kwargs):
        # Only writes into this profile fail; other callers keep working.
        if not self.is_relative_to(state):
            return original_write_text(self, data, **kwargs)
        with self.open("w", encoding="utf-8") as handle:
            handle.write(data[:8])
        raise OSError("write interrupted")

    monkeypatch.setattr(Path, "write_text", partial_then_fail)

    with pytest.raises(OSError, match="write interrupted"):
        save_meta(PROGRAMS_META, {"favorites": ["new"]})

    assert json.loads(target.read_text(encoding="utf-8"))["favorites"] == ["old"]
    assert list(state.glob(f".{PROGRAMS_META}.*.tmp")) == []
