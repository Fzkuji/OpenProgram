from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)


def _client(monkeypatch) -> TestClient:
    from openprogram.interfaces.server.routes.settings import channels

    monkeypatch.setattr(channels, "_require_local_request", lambda _request: None)
    monkeypatch.setattr(
        "openprogram.interfaces.channels.accounts.list_all_accounts",
        lambda: [SimpleNamespace(channel="telegram", account_id="default")],
    )
    app = FastAPI()
    channels.register(app)
    return TestClient(app)


def test_access_management_rejects_non_loopback_requests():
    from openprogram.interfaces.server.routes.settings.channels import _require_local_request

    local = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    _require_local_request(local)

    remote = SimpleNamespace(client=SimpleNamespace(host="192.0.2.4"))
    with pytest.raises(HTTPException) as exc:
        _require_local_request(remote)
    assert exc.value.status_code == 403


def test_web_owner_can_list_and_approve_pending_pairing(monkeypatch):
    from openprogram.interfaces.channels import _access

    pending = _access.decide_inbound_sender(
        "telegram", "default", "u7", "[Eve]\n\u202e",
    )
    assert pending.reply is not None
    code = _access.describe("telegram", "default")["pending"]["u7"]["code"]
    client = _client(monkeypatch)

    listed = client.get("/api/channels/access")
    approved = client.post(
        "/api/channels/access/approve",
        json={"channel": "telegram", "account_id": "default", "code": code},
    )

    assert listed.status_code == 200
    row = listed.json()["accounts"][0]
    assert row["channel"] == "telegram"
    assert row["account_id"] == "default"
    assert row["paired"] == []
    assert row["pending"][0]["user_id"] == "u7"
    assert row["pending"][0]["display"] == "(Eve)"
    assert row["pending"][0]["code"] == code
    assert approved.status_code == 200
    assert approved.json() == {"ok": True, "user_id": "u7"}
    assert _access.decide_inbound_sender(
        "telegram", "default", "u7", "Renamed",
    ).allowed is True


def test_web_owner_can_revoke_paired_sender(monkeypatch):
    from openprogram.interfaces.channels import _access

    _access.approve_user("telegram", "default", "u7", display="Eve")
    client = _client(monkeypatch)

    response = client.delete(
        "/api/channels/access/telegram/default/u7",
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "user_id": "u7"}
    assert _access.decide_inbound_sender(
        "telegram", "default", "u7", "Eve",
    ).allowed is False


def test_web_approval_rejects_unknown_or_expired_code(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/api/channels/access/approve",
        json={
            "channel": "telegram",
            "account_id": "default",
            "code": "ABCDEFGH",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "pending pairing code not found or expired"


def test_web_access_rejects_invalid_account_paths(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/api/channels/access/approve",
        json={
            "channel": "telegram",
            "account_id": "../outside",
            "code": "ABCDEFGH",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid channel account id"


def test_channels_settings_exposes_pairing_management():
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    index = (root / "apps/web/components/settings/channels/index.tsx").read_text(
        encoding="utf-8"
    )
    access = root / "apps/web/components/settings/channels/access-list.tsx"

    assert access.is_file()
    source = access.read_text(encoding="utf-8")
    assert "AccessList" in index
    assert 'jsonFetch("/api/channels/access/approve"' in source
    assert 'method: "DELETE"' in source
    assert 'type="button"' in source
    assert 'role="alert"' in source


def test_web_owner_manages_the_group_lists(monkeypatch):
    from openprogram.interfaces.channels import _access, accounts

    accounts.create("telegram", "default")
    client = _client(monkeypatch)

    added = client.post("/api/channels/access/group-users", json={
        "channel": "telegram", "account_id": "default",
        "user_id": "123", "access": "chat",
    })
    row = client.get("/api/channels/access").json()["accounts"][0]
    assert added.json() == {"ok": True, "user_id": "123"}
    assert [u["user_id"] for u in row["group_chat_only"]] == ["123"]
    assert row["group_full_access"] == []
    assert _access.group_sender_access("telegram", "default", "-1", "123") == "chat"

    removed = client.delete("/api/channels/access/group-users/telegram/default/123")
    assert removed.status_code == 200
    assert _access.group_sender_access("telegram", "default", "-1", "123") is None


def test_web_refuses_a_name_and_a_channel_without_groups(monkeypatch):
    from openprogram.interfaces.channels import accounts

    accounts.create("telegram", "default")
    client = _client(monkeypatch)

    named = client.post("/api/channels/access/group-users", json={
        "channel": "telegram", "account_id": "default",
        "user_id": "@alice", "access": "full",
    })
    wechat = client.post("/api/channels/access/group-users", json={
        "channel": "wechat", "account_id": "default",
        "user_id": "x", "access": "full",
    })
    assert named.status_code == 400
    assert wechat.status_code == 404


def test_web_refuses_listing_a_bound_contact_as_chat_only(monkeypatch):
    from openprogram.interfaces.channels import _access, accounts

    accounts.create("telegram", "default")
    _access.approve_user("telegram", "default", "555")
    client = _client(monkeypatch)

    response = client.post("/api/channels/access/group-users", json={
        "channel": "telegram", "account_id": "default",
        "user_id": "555", "access": "chat",
    })
    assert response.status_code == 400
    assert "access revoke telegram 555" in response.json()["detail"]
