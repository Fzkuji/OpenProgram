"""Another computer's OpenProgram: paired once, listed beside this computer's, driven by forwarding.

The other computer is a real Worker app in this process for pairing; the
lists and the forwarding rules are exercised on what a connection stores,
without a second Worker running.
"""
from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest

from openprogram.host import machines
from openprogram.host.machines import peer
from openprogram.interfaces.server.remote_machines import forward, links


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    os.chmod(p, 0o600)
    monkeypatch.setattr("openprogram.state.paths.get_config_path", lambda: p)
    monkeypatch.setattr("openprogram.state.config.get_config_path", lambda: p)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(machines, "_remove_instances_later", lambda machine_id: None)
    return p


@pytest.fixture(autouse=True)
def clean_links():
    yield
    for machine_id in list(links._sessions) + list(links._projects):
        links._sessions.pop(machine_id, None)
        links._projects.pop(machine_id, None)
    links._owner.clear()
    links._route.clear()


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(links, "_lists_changed", lambda: None)


def test_a_pairing_link_carries_every_address_and_round_trips():
    link = peer.build_link(["http://192.168.1.20:18100", "https://studio.tail1.ts.net"], "c0de")
    assert link == "http://192.168.1.20:18100/#pair=c0de&via=https%3A%2F%2Fstudio.tail1.ts.net"
    parsed = peer.parse_link(link)
    assert parsed.routes == ("http://192.168.1.20:18100", "https://studio.tail1.ts.net")
    assert parsed.code == "c0de"
    with pytest.raises(peer.PairingError):
        peer.parse_link("http://192.168.1.20:18100/")


def test_redeem_tries_each_address_and_keeps_them_all(monkeypatch):
    calls = []

    def post(url, json, timeout, trust_env):
        calls.append(url)
        if url.startswith("http://192.168.1.20"):
            raise httpx.ConnectError("away from home")
        assert json["code"] == "c0de" and json["label"].startswith("OpenProgram")
        return httpx.Response(200, json={"device_id": "a" * 16, "token": "opd1.x.y"})

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(peer.here, "is_this_computer", lambda host: False)
    paired = peer.redeem(peer.build_link(["http://192.168.1.20:18100", "https://studio.tail1.ts.net"], "c0de"))
    assert paired == {"url": "https://studio.tail1.ts.net", "routes": ["http://192.168.1.20:18100"],
                      "token": "opd1.x.y", "device_id": "a" * 16}
    assert len(calls) == 2


def test_redeem_refuses_this_very_worker_but_not_another_profile(monkeypatch):
    monkeypatch.setattr(peer.here, "is_this_computer", lambda host: True)
    with pytest.raises(peer.PairingError):
        peer.redeem("http://127.0.0.1:18200/#pair=c0de", own_port=18200)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(401))
    with pytest.raises(peer.PairingError, match="expired"):
        peer.redeem("http://127.0.0.1:18100/#pair=c0de", own_port=18200)


def test_the_credential_is_kept_out_of_the_configuration(cfg_path):
    machine = machines.save_machine({"name": "Studio", "connection": {
        "kind": "openprogram", "url": "http://192.168.1.20:18100/", "routes": ["https://studio.tail1.ts.net"],
        "token": "opd1.0123456789abcdef.secret", "device_id": "0123456789abcdef"}})
    assert machine.id == "studio"
    stored = cfg_path.read_text(encoding="utf-8")
    assert "secret" not in stored.replace('"secret": true', "")
    assert machines.secret(machine, "token") == "opd1.0123456789abcdef.secret"
    public = machine.public()["connection"]
    assert public["url"] == "http://192.168.1.20:18100"
    assert public["token"] == {"secret": True}
    assert public["where"] == "http://192.168.1.20:18100"
    assert not machines.usable_as_environment(machine)


def test_lists_merge_marked_with_the_machine_and_a_local_id_wins(quiet):
    links._store("studio", "sessions", [{"id": "s-remote", "title": "On the studio"},
                                        {"id": "s-local", "title": "Same id as one here"}])
    links._store("studio", "projects", [
        {"id": "home", "name": "Home", "is_default": True, "machine_id": "local", "session_ids": ["s-remote"]},
        {"id": "p1", "name": "Paper", "is_default": False, "machine_id": "local", "session_ids": []},
    ])
    rows = links.remote_session_rows({"s-local"})
    assert [(row["id"], row["machine_id"]) for row in rows] == [("s-remote", "studio")]
    projects = links.remote_projects({"s-local"})
    assert [(p["id"], p["machine_id"], p["is_default"]) for p in projects] == [
        ("@studio/home", "studio", False), ("@studio/p1", "studio", False)]
    assert links.owner_of("s-remote") == "studio"
    assert links.remote_project_for("s-remote") == "@studio/home"
    links._forget("studio")
    assert links.owner_of("s-remote") is None and links.remote_session_rows(set()) == []


def test_actions_go_to_the_machine_the_conversation_lives_on(quiet):
    links._store("studio", "sessions", [{"id": "s-remote", "title": "x"}])
    assert forward.target({"action": "load_session", "session_id": "s-remote"}) == (
        "studio", {"action": "load_session", "session_id": "s-remote"})
    # Lists are answered here, merged.
    assert forward.target({"action": "list_projects", "session_id": "s-remote"}) is None
    assert forward.target({"action": "load_session", "session_id": "s-here"}) is None
    # A first send in a project there creates the conversation there.
    machine_id, sent = forward.target({"action": "chat", "session_id": "draft-1", "project_id": "@studio/p1"})
    assert (machine_id, sent["project_id"]) == ("studio", "p1")
    assert links.owner_of("draft-1") == "studio"
    assert forward.target({"action": "mark_sessions_read", "session_ids": ["s-remote"]})[0] == "studio"
    assert forward.target({"action": "mark_sessions_read", "session_ids": ["s-remote", "s-here"]}) is None


class _Page:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


def test_frames_from_there_reach_the_page_but_not_the_other_workers_own(quiet, monkeypatch):
    links._store("studio", "sessions", [{"id": "s-remote", "title": "x"}])
    links._store("lab", "sessions", [{"id": "s-lab", "title": "y"}])
    monkeypatch.setattr(forward, "_is_local", lambda sid: sid == "s-here")
    page = _Page()
    upstream = forward.Upstream(page, "studio")
    frames = [
        {"type": "functions_list", "data": []},
        {"type": "frontend_build", "data": {"id": "x"}},
        {"type": "sessions_list", "data": []},
        {"type": "chat_response", "data": {"session_id": "s-remote", "type": "text"}},
        {"type": "chat_response", "data": {"session_id": "s-lab"}},
        {"type": "chat_response", "data": {"session_id": "s-here"}},
        {"type": "chat_ack", "data": {"session_id": "s-new"}},
        {"type": "branches_list", "data": {"branches": []}},
    ]
    for frame in frames:
        asyncio.run(upstream._relay(json.dumps(frame)))
    assert [(f["type"], (f.get("data") or {}).get("session_id")) for f in page.sent] == [
        ("chat_response", "s-remote"), ("chat_ack", "s-new"), ("branches_list", None)]
    assert links.owner_of("s-new") == "studio"


def test_this_worker_says_it_can_be_listed_from_elsewhere():
    from openprogram.interfaces.server.handshake import REMOTE_CAPABILITY, server_identity
    assert REMOTE_CAPABILITY in server_identity()["capabilities"]
