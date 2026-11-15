"""The plugin HTTP routes take a declarative plugin from install through reload to disable."""
from __future__ import annotations

from tests.unit.plugins.test_plugin_runtime import write_plugin
from tests.unit.plugins import test_plugin_runtime as _test_plugin_runtime

# Shared pytest fixtures, bound at module level so pytest collects them here.
plugin_home = _test_plugin_runtime.plugin_home


def test_web_routes_serve_a_plugin_from_install_to_reload(plugin_home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.interfaces.server.routes.catalog import plugins as routes

    root = write_plugin(plugin_home / "src" / "web-demo", "web-demo")
    app = FastAPI()
    routes.register(app)
    client = TestClient(app)

    installed = client.post("/api/plugins/install", json={"source": "path", "spec": str(root)}).json()
    assert installed["success"], installed
    enabled = client.post("/api/plugins/web-demo/toggle", json={"enabled": True}).json()
    assert enabled["loaded"] and enabled["contributions"]["commands"] == ["greet"]
    assert enabled["code"] == []
    assert client.get("/api/plugins/commands").json()["commands"][0]["name"] == "greet"
    assert client.get("/api/plugins/client").status_code == 404
    facts = client.get("/api/plugins/inspect").json()
    assert "deferred" in facts["tiers"] and facts["entries"]

    (root / "commands" / "bye.md").write_text("Say goodbye\n", encoding="utf-8")
    reloaded = client.post("/api/plugins/web-demo/reload").json()
    assert sorted(reloaded["contributions"]["commands"]) == ["bye", "greet"]

    assert not client.post("/api/plugins/web-demo/toggle", json={"enabled": False}).json()["loaded"]
    assert client.get("/api/plugins/commands").json()["commands"] == []


def test_a_plugin_with_code_is_refused_by_the_routes(plugin_home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.interfaces.server.routes.catalog import plugins as routes

    root = write_plugin(plugin_home / "src" / "coder", "coder", client="./client.js")
    client = TestClient(_app(routes, FastAPI))
    refused = client.post("/api/plugins/install", json={"source": "path", "spec": str(root)}).json()
    assert not refused["success"] and "contains code (client)" in refused["log"]


def _app(routes, factory):
    app = factory()
    routes.register(app)
    return app
