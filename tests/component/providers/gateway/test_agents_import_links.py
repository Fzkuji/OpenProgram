"""Apps that take the gateway only through an import link of their own: Cindy,
AionUi, Cherry Studio and Lody, after yetone/magpie's ``applink_test.go``,
``cindy_test.go``, ``aionui_test.go``, ``cherrystudio_test.go`` and
``lody_test.go`` (MIT License, Copyright (c) yetone)."""
from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urlparse

import pytest

GW = "http://127.0.0.1:18100/gateway"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home" / ".config"))
    (tmp_path / "home").mkdir()
    from openprogram.models.gateway import address, conversion
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [])
    return tmp_path


def _at(agent, worker: str = "http://127.0.0.1:18100"):
    from openprogram.models.gateway.agents import Wiring
    from openprogram.models.gateway.keys import token_for
    return Wiring(agent=agent, values={}, worker=worker, key=token_for(agent.id), models=[])


def _data(link: str, prefix: str, urlsafe: bool = True) -> dict:
    assert link.startswith(prefix + "v=1&data="), link
    raw = link[len(prefix + "v=1&data="):]
    if urlsafe:
        return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    return json.loads(base64.b64decode(unquote_plus(raw)))


def _db(path: Path, schema: str, *rows: str, wal: bool = False) -> None:
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(schema)
    for row in rows:
        conn.execute(row)
    conn.commit()
    conn.close()


# applink.go

def test_command_program() -> None:
    from openprogram.models.gateway.agents.applink import command_program
    cases = {
        r'"C:\Users\a b\AppData\Local\Programs\Cindy\Cindy.exe" "%1"': r"C:\Users\a b\AppData\Local\Programs\Cindy\Cindy.exe",
        r'"C:\Program Files\Lody\Lody.exe" -- "%1"': r"C:\Program Files\Lody\Lody.exe",
        r"C:\Tools\aionui.exe %1": r"C:\Tools\aionui.exe",
        r'  "C:\x\y.exe"': r"C:\x\y.exe",
        r'"C:\unclosed\y.exe': r"C:\unclosed\y.exe",
        "": "",
        "rundll32.exe url.dll,FileProtocolHandler %1": "rundll32.exe",
    }
    for given, want in cases.items():
        assert command_program(given) == want


def test_open_app_link_says_nothing_opens_it(state, tmp_path) -> None:
    """The link is handed over only when something opens it; otherwise the
    reason is said (the system starts its opener and never says nothing did)."""
    from openprogram.models.gateway.agents import applink, cindy
    there = tmp_path / "Cindy.exe"
    there.write_text("")
    missing = str(tmp_path / "gone" / "Cindy.exe")
    link = cindy.link(_at(cindy.agent()))
    cases = [
        ("none", "", False, None, False, ""),
        ("program gone", missing, True, None, False, missing),
        ("program there", str(there), True, None, True, None),
        ("found, no path (macOS)", "", True, None, True, None),
        ("bare name on PATH", "cindy.exe", True, None, True, None),
        ("unreadable is unknown", "", False, applink.Unknown("access denied"), True, None),
    ]
    for _name, exe, found, error, opens, want_exe in cases:
        asked, opened = [], []

        def handler(scheme, exe=exe, found=found, error=error):
            asked.append(scheme)
            if error:
                raise error
            return exe, found
        if opens:
            applink.open_app_link(link, opened.append, handler)
        else:
            with pytest.raises(applink.NoLinkHandler) as caught:
                applink.open_app_link(link, opened.append, handler)
            assert (caught.value.app, caught.value.scheme, caught.value.exe) == ("Cindy", "cindy", want_exe)
        assert asked == ["cindy"] and (opened == [link]) == opens
    for given, app in (("lody://provider/import?v=1", "Lody"), ("aionui://add-provider?a=1", "AionUi")):
        with pytest.raises(applink.NoLinkHandler) as caught:
            applink.open_app_link(given, lambda _: None, lambda _: ("", False))
        assert caught.value.app == app


def test_system_link_handler_no_such_scheme() -> None:
    from openprogram.models.gateway.agents import applink
    try:
        exe, found = applink.link_handler("openprogram-no-such-scheme-4711")
    except applink.Unknown as exc:
        pytest.skip(f"can't be read here: {exc}")
    assert not found and exe == ""


# Cindy

def test_cindy_link(state) -> None:
    from openprogram.models.gateway.agents import cindy
    from openprogram.models.gateway.keys import token_for
    payload = _data(cindy.link(_at(cindy.agent())), cindy.SCHEME)
    assert payload["kind"] == "custom" and payload["id"] == "openprogram" and payload["name"] == "OpenProgram"
    assert payload["auth"] == {"method": "apiKey", "apiKey": token_for("cindy")}
    assert [(e["protocol"], e["baseUrl"], e["targets"]) for e in payload["endpoints"]] == [
        ("anthropic-messages", GW + "/anthropic", ["claude-code"]),
        ("openai-responses", GW + "/openai/v1", ["codex"]),
        ("openai-chat", GW + "/openai/v1", ["pi"]),
    ]
    assert {e["modelsUrl"] for e in payload["endpoints"]} == {GW + "/openai/v1/models"}


SCHEMA = "CREATE TABLE custom_providers (id text PRIMARY KEY, name text, runtimes text DEFAULT '{}');"


def test_cindy_has(tmp_path) -> None:
    from openprogram.models.gateway.agents import cindy
    path = tmp_path / "cindy-local-v1.db"
    assert cindy.has(path, GW) == (False, True)
    _db(path, SCHEMA)
    assert cindy.has(path, GW) == (False, True)
    _db(path, "", "INSERT INTO custom_providers VALUES ('p1', 'Other', '{\"codex\":{\"baseUrl\":\"https://x\"}}')")
    assert cindy.has(path, GW)[0] is False
    _db(path, "", f"INSERT INTO custom_providers VALUES ('p2', 'Mine', '{{\"codex\":{{\"baseUrl\":\"{GW}/openai/v1\"}}}}')")
    assert cindy.has(path, GW) == (True, True)


def test_cindy_added_in_an_account_database(tmp_path) -> None:
    from openprogram.models.gateway.agents import cindy
    local, glob = tmp_path / "local", tmp_path / "global"
    local.mkdir()
    glob.mkdir()
    assert cindy.added([local, glob], GW) == (False, True)
    _db(glob / "cindy-cmubb4059023mzr0152yezm4r.db", SCHEMA,
        "INSERT INTO custom_providers VALUES ('openprogram-73271b6d', 'OpenProgram', '{}')")
    assert cindy.added([local, glob], GW)[0] is True


def test_cindy_closed_wal_and_unreadable(tmp_path) -> None:
    """A closed WAL-mode database is read and nothing is written in Cindy's
    folder; a file that is no database is unknown, never "not added"."""
    from openprogram.models.gateway.agents import cindy
    path = tmp_path / "cindy-local-v1.db"
    _db(path, SCHEMA, "INSERT INTO custom_providers VALUES ('openprogram', 'OpenProgram', '{}')", wal=True)
    before = sorted(os.listdir(tmp_path))
    assert cindy.has(path, GW) == (True, True)
    assert sorted(os.listdir(tmp_path)) == before
    bad = tmp_path / "cindy-bad.db"
    bad.write_bytes(b"not a database, a half-written one")
    assert cindy.has(bad, GW) == (False, False)
    assert cindy.added([tmp_path], GW) == (True, True)
    path.unlink()
    assert cindy.added([tmp_path], GW) == (False, False)


def test_open_wal_database_is_read_from_a_copy(tmp_path) -> None:
    """An app running in WAL mode: its log is read too, from a copy."""
    from openprogram.models.gateway.agents import cindy
    path = tmp_path / "cindy-local-v1.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO custom_providers VALUES ('openprogram', 'OpenProgram', '{}')")
    conn.commit()
    try:
        assert Path(str(path) + "-wal").stat().st_size > 0
        before = sorted(os.listdir(tmp_path))
        assert cindy.has(path, GW) == (True, True)
        assert sorted(os.listdir(tmp_path)) == before
    finally:
        conn.close()


# AionUi

def test_aionui_link(state) -> None:
    from openprogram.models.gateway.agents import aionui
    from openprogram.models.gateway.keys import token_for
    link = aionui.link(_at(aionui.agent()))
    assert link.startswith(aionui.SCHEME)
    query = parse_qs(urlparse(link).query)
    assert query == {"api_key": [token_for("aionui")], "base_url": [GW + "/openai/v1"],
                     "name": ["openprogram"], "platform": ["custom"]}


AIONUI = "CREATE TABLE providers (id text PRIMARY KEY, platform text, name text, base_url text, api_key text);"


def test_aionui_has_and_rereads_on_change(tmp_path) -> None:
    from openprogram.models.gateway.agents import aionui, appdb
    path = tmp_path / "aionui-backend.db"
    assert aionui.has(path, GW) == (False, True)
    _db(path, AIONUI, "INSERT INTO providers VALUES ('a', 'custom', 'x', 'https://api.example.com/v1', 'k')")
    status = appdb.DbStatus(aionui.has)
    assert status.read(path, GW) == (False, True)
    _db(path, "", f"INSERT INTO providers VALUES ('b', 'custom', 'openprogram', '{GW}/openai/v1', 'k')")
    assert status.read(path, GW) == (True, True)
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"garbage, not sqlite")
    assert aionui.has(bad, GW) == (False, False)


def test_aionui_detect(state, monkeypatch) -> None:
    from openprogram.models.gateway.agents import aionui
    monkeypatch.setattr(aionui, "user_config_dir", lambda: state / "cfg")
    monkeypatch.setattr(aionui, "mac_app_removed", lambda *apps: False)
    assert not aionui.agent().installed()
    (state / "cfg" / "AionUi").mkdir(parents=True)
    assert not aionui.agent().installed()  # Electron's folder alone, before its database folder
    (state / "cfg" / "AionUi" / "aionui").mkdir()
    assert aionui.agent().installed()
    monkeypatch.setattr(aionui, "mac_app_removed", lambda *apps: True)
    assert not aionui.agent().installed()


# Cherry Studio

def test_cherry_studio_link(state) -> None:
    from openprogram.models.gateway.agents import cherry_studio
    from openprogram.models.gateway.keys import token_for
    link = cherry_studio.link(_at(cherry_studio.agent()))
    payload = _data(link, cherry_studio.SCHEME, urlsafe=False)
    assert payload == {"id": "openprogram", "baseUrl": GW + "/openai/v1", "apiKey": token_for("cherry_studio"),
                       "name": "openprogram", "type": "openai"}
    raw = link.split("data=", 1)[1]
    # standard base64, query-escaped: no raw + or / for Cherry Studio's own swap to break
    assert "+" not in raw and "/" not in raw


CHERRY = "CREATE TABLE user_provider (provider_id text PRIMARY KEY, endpoint_configs text);"


def test_cherry_studio_has(tmp_path) -> None:
    from openprogram.models.gateway.agents import cherry_studio
    path = tmp_path / "cherrystudio.sqlite"
    assert cherry_studio.has(path, GW) == (False, True)
    _db(path, CHERRY, "INSERT INTO user_provider VALUES ('a', '{\"openai\":{\"baseUrl\":\"http://127.0.0.1:181000/gateway/openai/v1\"}}')")
    assert cherry_studio.has(path, "http://127.0.0.1:18100") == (False, True)  # another port that begins with this one
    _db(path, "", f"INSERT INTO user_provider VALUES ('b', '{{\"openai\":{{\"baseUrl\":\"{GW}/openai/v1\"}}}}')")
    assert cherry_studio.has(path, GW) == (True, True)


def test_cherry_studio_unreadable_and_moved_user_data(state, monkeypatch) -> None:
    from openprogram.models.gateway.agents import cherry_studio
    home = state / "home"
    default = state / "cfg" / "CherryStudio"
    monkeypatch.setattr(cherry_studio, "user_config_dir", lambda: state / "cfg")
    monkeypatch.setattr(cherry_studio, "mac_app_removed", lambda *apps: False)
    status = cherry_studio.Status()
    assert status.read(home, GW) == (False, True) and not cherry_studio.agent().installed()
    (default / "Local Storage").mkdir(parents=True)  # 1.x only: no database to read
    assert status.read(home, GW) == (False, False) and cherry_studio.agent().installed()
    moved = state / "elsewhere"
    (moved / "Data").mkdir(parents=True)
    _db(cherry_studio.database(moved), CHERRY,
        f"INSERT INTO user_provider VALUES ('b', '{{\"openai\":{{\"baseUrl\":\"{GW}/openai/v1\"}}}}')")
    (home / ".cherrystudio").mkdir()
    (home / ".cherrystudio" / "boot-config.json").write_text(json.dumps({"app.user_data_path": {"/x/Cherry": str(moved)}}))
    assert cherry_studio.dirs(home) == ([default, moved], True)
    assert status.read(home, GW) == (True, True)
    (home / ".cherrystudio" / "boot-config.json").write_text("{broken")
    assert cherry_studio.dirs(home)[1] is False


# Lody

def test_lody_link(state) -> None:
    from openprogram.models.gateway.agents import lody
    from openprogram.models.gateway.keys import token_for
    agent = lody.agent()
    for worker in ("http://127.0.0.1:18100", "http://localhost:4567", "http://[::1]:4567"):
        link = lody.link(_at(agent, worker))
        url = urlparse(link)
        assert (url.scheme, url.netloc, url.path) == ("lody", "provider", "/import")
        assert set(parse_qs(url.query)) == {"v", "data"}
        payload = _data(link, lody.SCHEME)
        gw = worker + "/gateway"
        assert payload == {
            "kind": "custom", "id": "openprogram", "name": "OpenProgram",
            "auth": {"method": "apiKey", "apiKey": token_for("lody")},
            "endpoints": [
                {"protocol": "anthropic-messages", "baseUrl": gw + "/anthropic", "targets": ["claude-code"],
                 "modelsUrl": gw + "/openai/v1/models"},
                {"protocol": "openai-responses", "baseUrl": gw + "/openai/v1", "targets": ["codex"],
                 "modelsUrl": gw + "/openai/v1/models"},
                {"protocol": "openai-chat", "baseUrl": gw + "/openai/v1", "targets": ["pi", "dsh", "kimi-code", "grok", "bub"],
                 "modelsUrl": gw + "/openai/v1/models"},
            ],
        }
        assert lody.loopback_base(worker + "/") == lody.loopback_base(worker)
    assert lody.loopback_base("http://localhost:80") == "http://localhost"
    assert lody.loopback_base("http://127.0.0.1:03425") == "http://127.0.0.1:3425"
    for bad in ("", "https://127.0.0.1:3425", "http://192.168.1.2:3425", "http://example.com", "http://127.0.0.1.evil.test",
                "http://user:secret@127.0.0.1", "http://127.0.0.1/v1", "http://localhost?token=secret",
                "http://localhost#fragment", "http://localhost?", "http://localhost:0", "http://localhost:65536",
                "http://localhost:", "http://::1"):
        assert lody.loopback_base(bad) == "", bad
        assert lody.link(_at(agent, bad)) == ""


def test_lody_detection(state, monkeypatch) -> None:
    from openprogram.models.gateway.agents import lody
    home = state / "home"
    monkeypatch.setattr(lody, "user_config_dir", lambda: state / "cfg")
    monkeypatch.setattr(lody.sys, "platform", "linux")
    assert not lody.detect(home)
    (home / ".lody").mkdir()
    assert not lody.detect(home)  # a folder of that name, none of Lody's own entries in it
    (home / ".lody" / "loro-repo").mkdir()
    assert lody.detect(home)
    shutil.rmtree(home / ".lody")
    (state / "cfg" / "Lody Nightly").mkdir(parents=True)
    assert lody.detect(home)


def test_lody_bundle(tmp_path) -> None:
    from openprogram.models.gateway.agents import lody

    def plist(name, body):
        folder = tmp_path / name / "Contents"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "Info.plist").write_bytes(body)
    assert not lody.bundle([tmp_path])
    plist("Lody.app", b"<plist><dict><key>CFBundleIdentifier</key><string>com.other.lody</string></dict></plist>")
    assert not lody.bundle([tmp_path])
    plist("Lody.app", b"<plist><dict><key>CFBundleIdentifier</key>\n<string>ai.lody.desktop</string></dict></plist>")
    assert lody.bundle([tmp_path])
    plist("Lody.app", b"bplist00\x00binary")
    assert lody.bundle([tmp_path])


@pytest.mark.skipif(sys.platform == "win32", reason="desktop entries are Linux's")
def test_open_lody_runs_its_desktop_entry(state, monkeypatch, tmp_path) -> None:
    from openprogram.models.gateway.agents import lody
    data = tmp_path / "share"
    apps = data / "applications"
    apps.mkdir(parents=True)
    image = tmp_path / "Lody App" / "Lody.AppImage"
    image.parent.mkdir()
    image.write_text("#!/bin/sh\n")
    image.chmod(0o755)
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "none"))
    (apps / "ai.lody.desktop.desktop").write_text(f'[Desktop Entry]\nExec="{image}" --no-sandbox %U\n')
    started, opened = [], []
    lody.open_lody("lody://x", opened.append, started.append, platform="linux")
    assert started == [[str(image), "--no-sandbox", "lody://x"]] and not opened
    lody.open_lody("lody://x", opened.append, started.append, platform="darwin")
    assert opened == ["lody://x"]
    image.unlink()  # an AppImage moved since Lody wrote its entry
    lody.open_lody("lody://y", opened.append, started.append, platform="linux")
    assert opened[-1] == "lody://y" and len(started) == 1


def test_desktop_exec() -> None:
    from openprogram.models.gateway.agents.applink import desktop_exec
    sh = shutil.which("sh")
    if not sh:
        pytest.skip("no sh")
    cases = [
        ("[Desktop Entry]\nExec=/bin/sh -c 'x' %U\n", ["/bin/sh", "-c", "'x'", "L"]),
        ('[Desktop Entry]\nExec="/bin/sh" "100%%" %u %f %i\n', ["/bin/sh", "100%", "L"]),
        ("[Desktop Action new]\nExec=/bin/false\n[Desktop Entry]\nExec=sh %U\n", [sh, "L"]),
        ("[Desktop Entry]\nName=Lody\n", None),
        ("[Desktop Entry]\nExec=/no/such/lody %U\n", None),
        ("[Desktop Entry]\nExec=no-such-lody-program %U\n", None),
    ]
    for entry, want in cases:
        assert desktop_exec(entry, "L") == want, entry


# The rows

def test_link_rows_and_the_import_route(state, monkeypatch, tmp_path) -> None:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from openprogram.interfaces.server.routes.settings import harness_models
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import applink, cindy, engine
    monkeypatch.setattr(cindy, "user_config_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(cindy, "mac_app_removed", lambda *apps: False)
    agent = cindy.agent()
    monkeypatch.setattr(agents, "all_agents", lambda: [agent])
    row = engine.status("cindy")
    assert row["link"] and row["added"] is False and row["fields"] == [] and not row["installed"]
    with pytest.raises(engine.AgentConfigError):
        engine.connect("cindy", {"model": "x/y"})
    folder = tmp_path / "cfg" / "Cindy"
    folder.mkdir(parents=True)
    _db(folder / "cindy-local-v1.db", SCHEMA, "INSERT INTO custom_providers VALUES ('openprogram', 'OpenProgram', '{}')")
    row = engine.status("cindy")
    assert row["installed"] and row["added"] is True and row["config"]["applied"]

    opened = []
    monkeypatch.setattr(applink, "system_open", opened.append)
    monkeypatch.setattr(applink, "link_handler", lambda scheme: ("", True))
    app = FastAPI()
    harness_models.register(app)
    with TestClient(app) as client:
        listed = client.get("/api/harnesses/models").json()["harnesses"]
        assert [r["id"] for r in listed] == ["cindy"] and listed[0]["link"]
        answer = client.post("/api/harnesses/import", json={"harness": "cindy"})
        assert answer.status_code == 200 and opened and opened[0].startswith(cindy.SCHEME)
        monkeypatch.setattr(applink, "link_handler", lambda scheme: ("", False))
        refused = client.post("/api/harnesses/import", json={"harness": "cindy"})
        assert refused.status_code == 409 and "cindy://" in refused.json()["error"]
        assert client.post("/api/harnesses/import", json={"harness": "nobody"}).status_code == 400
