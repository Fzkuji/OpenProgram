"""The skills API writes into the active instance's own folder."""
from __future__ import annotations


def test_a_skill_made_in_the_ui_lands_in_this_instance(tmp_path, monkeypatch):
    # The server runs in the home folder; a UI-made skill must go to the
    # active profile's skills folder, not to <cwd>/skills shared by both instances.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.interfaces.server.routes.catalog import skills as skills_route
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENPROGRAM_PROFILE", "dev")
    monkeypatch.chdir(tmp_path)
    app = FastAPI()
    skills_route.register(app)
    response = TestClient(app).post("/api/skills", json={"name": "notes/daily", "description": "d"})
    assert response.status_code == 200
    assert (tmp_path / ".openprogram-dev" / "skills" / "notes" / "daily" / "SKILL.md").is_file()
    assert not (tmp_path / "skills").exists()
    # A name some source already provides is refused, so a new copy is never shadowed.
    again = TestClient(app).post("/api/skills", json={"name": "notes/daily"})
    assert again.status_code == 409
