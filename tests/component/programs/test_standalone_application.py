"""An application package run on its own (openprogram/interfaces/standalone).

The server is built in this process around a small package with no
backend, and asked through a TestClient without its lifespan; the
model behind a conversation is a function; the export writes into a
temporary folder and names this interpreter instead of copying one,
except where a fake interpreter prefix checks what is trimmed.
"""
from __future__ import annotations

import json
import os
import plistlib
import stat
import sys
from pathlib import Path

import pytest

from openprogram.programs._host import registry
from openprogram.programs._host.ui import host_calls, loader, state

APP_ID = "test.standalone"


def write_package(root: Path, **manifest) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    (root / "application.json").write_text(json.dumps({
        "id": APP_ID, "title": "Standalone Test", "version": "1.2.3",
        "capabilities": ["storage.app", "conversation"], **manifest}), encoding="utf-8")
    return root


@pytest.fixture
def data(tmp_path, monkeypatch):
    folder = tmp_path / "data"
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(folder))
    return folder


@pytest.fixture
def methods():
    saved = dict(host_calls.METHODS)
    yield
    host_calls.METHODS.clear()
    host_calls.METHODS.update(saved)


# --------------------------------------------------------------------------- state and registry

def test_the_state_folder_named_by_the_launcher_wins_over_a_profile(data, monkeypatch):
    from openprogram.state.paths import get_state_dir
    monkeypatch.setenv("OPENPROGRAM_PROFILE", "dev")
    assert get_state_dir() == data


def test_a_standalone_process_loads_no_other_application(monkeypatch):
    synced: list[str] = []
    monkeypatch.setattr(registry, "sync_builtin_application", lambda name: synced.append(name))
    monkeypatch.setenv(registry.STANDALONE_ENV, "/somewhere/app")
    registry.load_program_modules()
    assert synced == []
    monkeypatch.delenv(registry.STANDALONE_ENV)
    monkeypatch.setattr("openprogram.programs._host.packages.load_user_packages", lambda: None)
    monkeypatch.setattr(registry, "_iter_external_harness_dirs", lambda directory: [])
    registry.load_program_modules()
    assert "meeting" in synced


def test_a_host_answers_a_host_call_its_own_way(methods):
    capability, _ = host_calls.METHODS["conversation.ask"]
    host_calls.answer_with("conversation.ask", lambda instance, params: {"answer": "mine"})
    assert host_calls.METHODS["conversation.ask"][0] == capability
    assert host_calls.METHODS["conversation.ask"][1]({}, {}) == {"answer": "mine"}


# --------------------------------------------------------------------------- settings

def test_settings_are_copied_each_launch_and_speech_models_linked(tmp_path):
    from openprogram.interfaces.standalone import settings
    source, target = tmp_path / "openprogram", tmp_path / "app"
    (source / "auth" / "anthropic").mkdir(parents=True)
    (source / "auth" / "anthropic" / "key.json").write_text("{}", encoding="utf-8")
    (source / "config.json").write_text('{"model": "a"}', encoding="utf-8")
    (source / "voice").mkdir()
    (source / "sessions").mkdir()
    assert sorted(settings.sync(source, target)) == ["auth", "config.json", "voice"]
    assert stat.S_IMODE((target / "config.json").stat().st_mode) == 0o600
    assert (target / "auth" / "anthropic" / "key.json").is_file()
    assert (target / "voice").is_symlink() and not (target / "sessions").exists()
    (source / "config.json").write_text('{"model": "b"}', encoding="utf-8")
    settings.sync(source, target)
    assert json.loads((target / "config.json").read_text())["model"] == "b"
    assert settings.sync(tmp_path / "missing", target) == []
    assert settings.sync(None, target) == []


# --------------------------------------------------------------------------- conversations

def test_a_conversation_keeps_its_messages_and_answers_with_the_earlier_ones(data, monkeypatch):
    from openprogram.interfaces.standalone import conversations
    from openprogram.host._platform import changes
    remembered: set[tuple[str, str]] = set()
    monkeypatch.setattr(state, "remember_conversation", lambda i, s: remembered.add((i, s)))
    monkeypatch.setattr(state, "started_conversation", lambda i, s: (i, s) in remembered)
    instance = {"id": "c" * 64}
    session_id = conversations.open_conversation(instance, {"title": "  Weekly\nsync "})["session_id"]
    assert conversations.describe(session_id)["title"] == "Weekly sync"
    prompts: list[str] = []
    listener = changes.listen("standalone-conversation")

    def reply(prompt: str) -> str:
        prompts.append(prompt)
        return "first answer" if len(prompts) == 1 else "second answer"

    assert conversations.ask(instance, {"session_id": session_id, "text": "Summarize"}, reply) == \
        {"answer": "first answer", "error": ""}
    conversations.ask(instance, {"session_id": session_id, "text": "Who decided?"}, reply)
    assert prompts[0] == "Summarize"
    assert "[assistant]\nfirst answer" in prompts[1] and prompts[1].endswith("Who decided?")
    shown = conversations.describe(session_id)
    assert [m["role"] for m in shown["messages"]] == ["user", "assistant", "user", "assistant"]
    assert shown["replying"] is False and shown["error"] == ""
    assert f"standalone-conversation:{session_id}" in listener.wait(0)
    listener.close()

    def broken(prompt: str) -> str:
        raise RuntimeError("no model is configured")

    assert conversations.ask(instance, {"session_id": session_id, "text": "Again"}, broken)["error"] == \
        "no model is configured"
    with pytest.raises(host_calls.HostCallError):
        conversations.ask(instance, {"session_id": "application_" + "0" * 16, "text": "x"}, reply)
    with pytest.raises(host_calls.HostCallError):
        conversations.read("../../config")


# --------------------------------------------------------------------------- server

@pytest.fixture
def server(tmp_path, data, methods):
    from fastapi.testclient import TestClient
    from openprogram.interfaces.standalone import server
    package = write_package(tmp_path / "package")
    app = server.build(package, data)
    client = TestClient(app, base_url="http://127.0.0.1:8123")
    yield client, server.token(data), app
    registry.dispose_program(f"application:{APP_ID}")


def test_the_server_answers_only_its_signed_in_page(server):
    client, token, _ = server
    assert client.get("/").status_code == 200
    assert "Standalone Test" in client.get("/").text
    assert client.get("/shell/shell.js").status_code == 200
    assert client.get("/shell/%2E%2E/server.py").status_code == 404
    assert client.get("/shell/index.html").status_code == 404
    assert client.get("/api/standalone").status_code == 401
    assert client.post("/api/standalone/sign-in", json={"token": "wrong"}).status_code == 401
    assert client.post("/api/standalone/sign-in", json={"token": token}).status_code == 200
    described = client.get("/api/standalone").json()
    assert described["application"]["id"] == APP_ID
    assert described["ui_url"].startswith(f"/application-assets/{described['instance_id']}/")
    page = client.get(described["ui_url"])
    assert page.status_code == 200
    # The frame's origin is opaque: its own folder is named, not only 'self',
    # or WebKit refuses the application's scripts.
    folder = "http://127.0.0.1:8123" + described["ui_url"].rsplit("/", 1)[0] + "/"
    policy = dict(part.strip().split(" ", 1) for part in page.headers["content-security-policy"].split(";")[1:])
    assert folder in policy["script-src"] and folder in policy["style-src"] and folder in policy["font-src"]
    assert policy["frame-ancestors"] == "'self' http://127.0.0.1:8123"
    assert client.get("/api/standalone", headers={"host": "attacker.example"}).status_code == 403
    assert client.get("/api/applications").status_code == 200  # signed in: the App's routes


def test_the_window_shows_and_continues_only_its_own_conversations(server, monkeypatch):
    client, token, app = server
    from openprogram.interfaces.standalone import conversations
    client.post("/api/standalone/sign-in", json={"token": token})
    instance = app.state.standalone["instance"]
    monkeypatch.setattr(conversations, "model_reply", lambda prompt: "an answer")
    opened = host_calls.METHODS["conversation.open"][1](instance, {"title": "Notes"})
    session_id = opened["session_id"]
    assert client.post(f"/api/application-instances/{instance['id']}/conversations/{session_id}/show").status_code == 200
    answer = client.post(f"/api/standalone/conversations/{session_id}/messages", json={"text": "Hello?"}).json()
    assert answer == {"answer": "an answer", "error": ""}
    assert [m["text"] for m in client.get(f"/api/standalone/conversations/{session_id}").json()["messages"]] == \
        ["Hello?", "an answer"]
    other = "application_" + "f" * 16
    assert client.get(f"/api/standalone/conversations/{other}").status_code == 404
    assert client.post(f"/api/standalone/conversations/{other}/messages", json={"text": "x"}).status_code == 400


def test_the_server_refuses_what_needs_the_app(tmp_path, data):
    from openprogram.interfaces.standalone import server
    with pytest.raises(ValueError, match="chat"):
        server.build(write_package(tmp_path / "chat", capabilities=["chat"]), data)
    with pytest.raises(ValueError, match="global"):
        server.build(write_package(tmp_path / "project", scope="project"), data)
    assert loader.builtin(APP_ID) is None


# --------------------------------------------------------------------------- export

def test_a_folder_export_carries_the_package_and_launchers_but_no_settings(tmp_path, monkeypatch):
    from openprogram.interfaces.standalone import export
    package = write_package(tmp_path / "package")
    (package / "web").mkdir()
    (package / "web" / "build.mjs").write_text("", encoding="utf-8")
    (package / "__pycache__").mkdir()
    result = export.export(str(package), tmp_path / "out", format="dir", python="reference")
    target = Path(result["path"])
    assert result["python"] == "reference" and target.name == "Standalone Test"
    assert (target / "app" / "application.json").is_file()
    assert not (target / "app" / "web").exists() and not (target / "app" / "__pycache__").exists()
    run = (target / "run.sh").read_text()
    assert sys.executable in run and "--settings-from" in run and "openprogram-applications/test.standalone" in run
    assert os.access(target / "run.sh", os.X_OK) and (target / "run.cmd").is_file()
    assert not any(p.name in ("config.json", "auth") for p in target.rglob("*"))
    with pytest.raises(FileExistsError):
        export.export(str(package), tmp_path / "out", format="dir", python="reference")


@pytest.mark.skipif(sys.platform != "darwin", reason="an .app is made on macOS")
def test_an_app_export_is_a_macos_bundle(tmp_path):
    from openprogram.interfaces.standalone import export
    result = export.export(str(write_package(tmp_path / "package")), tmp_path, format="app", python="reference")
    contents = Path(result["path"]) / "Contents"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "StandaloneTest" and info["CFBundleShortVersionString"] == "1.2.3"
    assert "NSMicrophoneUsageDescription" in info
    assert os.access(contents / "MacOS" / "StandaloneTest", os.X_OK)
    assert (contents / "Resources" / "app" / "application.json").is_file()
    assert "CFBundleIconFile" not in info


@pytest.mark.skipif(sys.platform != "darwin", reason="an .app is made on macOS")
def test_an_app_export_takes_the_drawn_icon(tmp_path):
    import base64
    from openprogram.interfaces.standalone import export
    # A 1x1 PNG: sips scales it to every size an .icns holds.
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    package = write_package(tmp_path / "package")
    result = export.export(str(package), tmp_path, format="app", python="reference", icon=png)
    contents = Path(result["path"]) / "Contents"
    assert plistlib.loads((contents / "Info.plist").read_bytes())["CFBundleIconFile"] == "AppIcon"
    assert (contents / "Resources" / "AppIcon.icns").read_bytes()[:4] == b"icns"
    with pytest.raises(ValueError, match="PNG"):
        export.export(str(package), tmp_path / "again", format="app", python="reference", icon=b"GIF89a")
    assert not (tmp_path / "again" / "Standalone Test.app").exists()


def test_the_launcher_opens_a_native_window_on_macos(tmp_path, monkeypatch):
    from openprogram.interfaces.standalone import server, window
    package = write_package(tmp_path / "package")
    opened: list[tuple] = []
    monkeypatch.setattr(server, "running", lambda data: 4321)
    monkeypatch.setattr(window, "available", lambda: True)
    monkeypatch.setattr(window, "run", lambda url, title, **_: opened.append(("native", title)))
    monkeypatch.setattr(server, "open_window", lambda url: opened.append(("browser", url)))
    server.launch(package, tmp_path / "data", settings_from=None, idle_minutes=1)
    monkeypatch.setattr(window, "available", lambda: False)
    server.launch(package, tmp_path / "data", settings_from=None, idle_minutes=1)
    assert opened[0] == ("native", "Standalone Test")
    assert opened[1][0] == "browser" and opened[1][1].startswith("http://127.0.0.1:4321/#token=")


def test_applications_that_need_the_app_are_not_exported(tmp_path):
    from openprogram.interfaces.standalone import export
    with pytest.raises(ValueError, match="chat"):
        export.export(str(write_package(tmp_path / "a", capabilities=["chat"])), tmp_path, format="dir", python="reference")
    with pytest.raises(ValueError, match="project scope"):
        export.export(str(write_package(tmp_path / "b", scope="project")), tmp_path, format="dir", python="reference")
    with pytest.raises(FileNotFoundError):
        export.resolve_package("no-such-application")
    assert export.resolve_package("meeting").name == "meeting"


def test_a_bundled_python_leaves_out_what_one_application_never_uses(tmp_path, monkeypatch):
    from openprogram.interfaces.standalone import export
    prefix = tmp_path / "cpython-3.12.0-test"
    site = prefix / "lib" / "python3.12" / "site-packages"
    for folder in (prefix / "bin", prefix / "include", prefix / "lib" / "python3.12" / "test",
                   prefix / "lib" / "python3.12" / "json", site / "playwright", site / "anthropic", site / "openprogram"):
        folder.mkdir(parents=True)
    (prefix / "bin" / "python3").write_text("", encoding="utf-8")
    copied: list[Path] = []
    monkeypatch.setattr(export, "movable_python", lambda: prefix)
    monkeypatch.setattr(export, "_copy_openprogram", lambda source, target: copied.append(target))
    result = export.export(str(write_package(tmp_path / "package")), tmp_path / "out", format="dir", python="bundle")
    python = Path(result["path"]) / "python"
    assert (python / "bin" / "python3").is_file() and (python / "lib" / "python3.12" / "json").is_dir()
    for left_out in ("include", "lib/python3.12/test", "lib/python3.12/site-packages/playwright"):
        assert not (python / left_out).exists()
    assert (python / "lib" / "python3.12" / "site-packages" / "anthropic").is_dir()
    assert copied == [python / "lib" / "python3.12" / "site-packages" / "openprogram"]
    assert '"$here/python/bin/python3"' in (Path(result["path"]) / "run.sh").read_text()


def test_the_copied_openprogram_has_only_the_exported_application_s_host(tmp_path):
    from openprogram.interfaces.standalone import export
    source = tmp_path / "openprogram"
    for folder in ("programs/applications/meeting", "programs/applications/browser",
                   "interfaces/server/_frontend/docs", "interfaces/server/routes", "state"):
        (source / folder).mkdir(parents=True)
    (source / "programs" / "applications" / "__init__.py").write_text("", encoding="utf-8")
    export._copy_openprogram(source, tmp_path / "copy")
    assert sorted(p.name for p in (tmp_path / "copy" / "programs" / "applications").iterdir()) == ["__init__.py"]
    assert not (tmp_path / "copy" / "interfaces" / "server" / "_frontend").exists()
    assert (tmp_path / "copy" / "interfaces" / "server" / "routes").is_dir()


def test_a_second_export_beside_the_first_is_numbered(tmp_path):
    from openprogram.interfaces.standalone import export
    package = write_package(tmp_path / "package")
    first = export.export(str(package), tmp_path, format="dir", python="reference", next_free=True)
    second = export.export(str(package), tmp_path, format="dir", python="reference", next_free=True)
    assert Path(first["path"]).name == "Standalone Test" and Path(second["path"]).name == "Standalone Test 2"


def test_the_applications_page_exports_and_shows_only_its_own_exports(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.interfaces.server.routes.catalog import applications
    from openprogram.interfaces.server.ws_actions.files import mutations
    from openprogram.interfaces.standalone import export
    written: list[tuple] = []

    def fake_export(name, output, **options):
        written.append((name, output, options))
        return {"path": str(tmp_path / "Meetings.app"), "application": name, "format": options["format"],
                "python": "bundle", "size": 1}

    shown: list[str] = []
    monkeypatch.setattr(export, "export", fake_export)
    monkeypatch.setattr(mutations, "reveal_in_file_manager", lambda path: shown.append(path) or {"ok": True})
    app = FastAPI()
    applications.register(app)
    client = TestClient(app)  # no lifespan: nothing loads
    png = b"\x89PNG\r\n\x1a\nicon"
    import base64
    drawn = "data:image/png;base64," + base64.b64encode(png).decode()
    result = client.post("/api/applications/meeting/export", json={"output": str(tmp_path), "icon": drawn})
    assert result.status_code == 200, result.text
    [(name, output, options)] = written
    assert name == "meeting" and output == tmp_path and options["next_free"] is True and options["icon"] == png
    assert client.post("/api/applications/meeting/export",
                       json={"output": str(tmp_path), "icon": "data:image/png;base64,%%"}).status_code == 400
    assert client.post("/api/applications/meeting/export", json={"output": str(tmp_path / "missing")}).status_code == 400
    (Path.home() / "Downloads").mkdir(exist_ok=True)
    client.post("/api/applications/meeting/export", json={})
    assert written[1][1] == Path("~/Downloads")
    assert client.post("/api/applications/exports/reveal", json={"path": "/etc"}).status_code == 404
    assert client.post("/api/applications/exports/reveal", json={"path": result.json()["path"]}).status_code == 200
    assert shown == [result.json()["path"]]
