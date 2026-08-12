"""MCP server credentials are storable but never retrievable.

An MCP server config holds secrets in four places: any ``env`` value
(local transport), any ``headers`` value (remote), the bearer token, and
the OAuth client secret. These tests pin the same contract the provider
credential routes already keep — a route may report that a secret exists
and show a mask, and may accept a replacement, but never returns the
stored value.

Values are masked wholesale rather than by name-matching: an MCP
server's env is free-form, so ``ENDPOINT`` can hold a signed URL just as
easily as ``API_KEY`` holds a key.
"""
from __future__ import annotations

import json
import os
import stat

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.mcp.config import (
    MCPServerConfig,
    OAuthSettings,
    load_configs,
    save_configs,
)


BEARER = "bearer-secret-token-value"
CLIENT_SECRET = "oauth-client-secret-value"
ENV_SECRET = "ghp_env_secret_value_here"
HEADER_SECRET = "tenant-signed-value-here"


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Redirect ``get_state_dir`` so config I/O lands in a tmp dir."""
    from openprogram import paths

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    return tmp_path


def local_config() -> MCPServerConfig:
    return MCPServerConfig(
        name="local-tools",
        type="local",
        command=["python", "server.py"],
        # Neither name looks key-shaped; both must still be masked.
        env={"ENDPOINT": ENV_SECRET, "REGION": "us-east-1"},
    )


def remote_config(auth_kind: str = "bearer") -> MCPServerConfig:
    return MCPServerConfig(
        name="remote-tools",
        type="http",
        url="https://mcp.example.com/mcp",
        headers={"X-Tenant": HEADER_SECRET},
        auth_kind=auth_kind,
        bearer_token=BEARER if auth_kind == "bearer" else None,
        oauth=(OAuthSettings(client_id="cid", client_secret=CLIENT_SECRET)
               if auth_kind == "oauth" else None),
    )


def secrets_absent(blob: str) -> None:
    for secret in (BEARER, CLIENT_SECRET, ENV_SECRET, HEADER_SECRET):
        assert secret not in blob, f"response leaked {secret!r}"


# --- serialization split ---------------------------------------------


def test_storage_dict_keeps_values_and_response_dict_masks_them():
    stored = local_config().to_storage_dict()
    assert stored["env"] == {"ENDPOINT": ENV_SECRET, "REGION": "us-east-1"}

    response = local_config().to_response_dict()
    assert response["env"]["ENDPOINT"] == {
        "has_value": True,
        "masked": "ghp…here",
    }
    # A value nobody would call a secret is masked all the same — the
    # rule is the field, not the name.
    assert response["env"]["REGION"]["has_value"] is True
    assert response["env"]["REGION"]["masked"] != "us-east-1"
    secrets_absent(json.dumps(response))


def test_response_dict_masks_bearer_and_oauth_secrets():
    bearer = remote_config("bearer").to_response_dict()
    assert bearer["auth"]["has_token"] is True
    assert bearer["auth"]["masked_token"] == "bea…alue"
    assert "token" not in bearer["auth"]
    secrets_absent(json.dumps(bearer))

    oauth = remote_config("oauth").to_response_dict()
    assert oauth["auth"]["has_client_secret"] is True
    assert "client_secret" not in oauth["auth"]
    # client_id is an identifier, not a secret — it stays readable so
    # the edit dialog can prefill it.
    assert oauth["auth"]["client_id"] == "cid"
    secrets_absent(json.dumps(oauth))


def test_config_with_no_secret_reports_absence():
    cfg = MCPServerConfig(name="plain", type="http",
                          url="https://x.example/mcp", auth_kind="bearer")
    assert cfg.to_response_dict()["auth"] == {
        "kind": "bearer", "has_token": False,
    }


# --- list / detail / catalog responses --------------------------------


def registry_with(cfg: MCPServerConfig, monkeypatch):
    """Install a fake ready client for ``cfg`` in the live registry."""
    from openprogram.mcp import registry

    class FakeClient:
        def __init__(self, config):
            self.config = config
            self.tools = []
            self.error = None
            self.error_kind = None
            self.is_ready = True

        def auth_status(self):
            return {"kind": self.config.auth_kind, "authenticated": True}

    monkeypatch.setitem(registry._clients, cfg.name, FakeClient(cfg))
    return registry


def test_list_and_detail_routes_return_no_plaintext(monkeypatch, state_dir):
    from openprogram.webui.routes import mcp

    for cfg in (local_config(), remote_config("bearer"),
                remote_config("oauth")):
        registry_with(cfg, monkeypatch)
    app = FastAPI()
    mcp.register(app)
    client = TestClient(app)

    listing = client.get("/api/mcp/servers")
    assert listing.status_code == 200
    secrets_absent(listing.text)
    local = next(s for s in listing.json()["servers"]
                 if s["name"] == "local-tools")
    assert local["env"]["ENDPOINT"]["has_value"] is True

    for name in ("local-tools", "remote-tools"):
        detail = client.get(f"/api/mcp/servers/{name}")
        assert detail.status_code == 200
        secrets_absent(detail.text)


def test_catalog_route_returns_no_plaintext(monkeypatch, state_dir):
    """A catalog can ship an entry carrying a token; the echo is masked."""
    from openprogram.webui.routes import mcp

    catalog = {
        "name": "test catalog",
        "servers": [{
            "name": "remote-tools",
            "type": "http",
            "url": "https://mcp.example.com/mcp",
            "headers": {"X-Tenant": HEADER_SECRET},
            "auth": {"kind": "bearer", "token": BEARER},
        }],
    }

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        url = "https://catalog.example/c.json"

        def raise_for_status(self):
            return None

        def json(self):
            return catalog

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return FakeResponse()

    from openprogram.security import safe_http

    monkeypatch.setattr(
        safe_http,
        "configured_safe_async_client",
        lambda _consumer, _url, **_kwargs: FakeAsyncClient(),
    )
    app = FastAPI()
    mcp.register(app)

    response = TestClient(app).get(
        "/api/mcp/catalog?url=https://catalog.example/c.json")

    assert response.status_code == 200
    secrets_absent(response.text)
    entry = response.json()["servers"][0]
    assert entry["auth"]["has_token"] is True
    assert entry["headers"]["X-Tenant"]["has_value"] is True


def test_catalog_update_missing_entry_hides_signed_source_url(monkeypatch, state_dir):
    from openprogram.webui.routes import mcp

    source = "https://catalog.example/TOKEN-PATH/catalog.json?sig=QUERY-SECRET"
    cfg = remote_config()
    cfg.source_catalog_url = source
    save_configs([cfg])

    async def fake_fetch(_url):
        return {"servers": []}

    monkeypatch.setattr(mcp, "_fetch_catalog_json", fake_fetch)
    app = FastAPI()
    mcp.register(app)

    response = TestClient(app).post(
        f"/api/mcp/servers/{cfg.name}/update_from_catalog"
    )

    assert response.status_code == 502
    rendered = response.text
    assert "https://catalog.example" in rendered
    assert "TOKEN-PATH" not in rendered
    assert "QUERY-SECRET" not in rendered


# --- file permissions -------------------------------------------------


def test_saved_config_is_owner_only(state_dir):
    path = save_configs([local_config()])

    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"config file is {oct(mode)}, expected 0o600"
    # No temp file left behind holding the same secrets.
    assert not list(state_dir.glob("*.tmp"))


def test_existing_world_readable_config_narrows_on_next_save(state_dir):
    path = save_configs([local_config()])
    os.chmod(path, 0o644)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644

    save_configs([local_config()])

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_save_configs_preserves_roots_block(state_dir):
    from openprogram.mcp.config import load_roots, save_roots

    save_roots([{"uri": "file:///work", "name": "work"}])
    save_configs([local_config()])

    assert load_roots() == [{"uri": "file:///work", "name": "work"}]
    assert [c.name for c in load_configs()] == ["local-tools"]


# --- preserve / replace / delete --------------------------------------


def patch_server(cfg: MCPServerConfig, body: dict, monkeypatch, *,
                 restart_fails: bool = False):
    """PATCH ``cfg`` with ``body`` and return (response, stored config)."""
    from openprogram.webui.routes import mcp

    save_configs([cfg])

    async def fake_restart(name, new_cfg=None):
        if restart_fails and new_cfg is not None and new_cfg is not cfg:
            raise RuntimeError("spawn failed")
        return {"name": name, "ready": True}

    monkeypatch.setattr(mcp, "restart_server", fake_restart)
    app = FastAPI()
    mcp.register(app)
    response = TestClient(app).patch(
        f"/api/mcp/servers/{cfg.name}", json=body)
    stored = next(c for c in load_configs(include_disabled=True)
                  if c.name == cfg.name)
    return response, stored


def test_omitted_env_field_preserves_every_stored_value(monkeypatch, state_dir):
    response, stored = patch_server(
        local_config(), {"timeout_seconds": 45}, monkeypatch)

    assert response.status_code == 200
    assert stored.env == {"ENDPOINT": ENV_SECRET, "REGION": "us-east-1"}
    assert stored.timeout_seconds == 45


def test_omitted_env_name_preserves_that_name(monkeypatch, state_dir):
    """A patch naming one variable must not wipe its siblings."""
    response, stored = patch_server(
        local_config(), {"env": {"REGION": "eu-west-1"}}, monkeypatch)

    assert response.status_code == 200
    assert stored.env == {"ENDPOINT": ENV_SECRET, "REGION": "eu-west-1"}


def test_submitted_value_replaces_and_empty_value_deletes(monkeypatch, state_dir):
    response, stored = patch_server(
        local_config(),
        {"env": {"ENDPOINT": "replaced-value", "REGION": ""}},
        monkeypatch,
    )

    assert response.status_code == 200
    assert stored.env == {"ENDPOINT": "replaced-value"}


def test_echoed_mask_is_treated_as_preserve(monkeypatch, state_dir):
    """The frontend never posts a mask back, but if the response shape
    is echoed by a scripted caller it must not overwrite the secret."""
    response, stored = patch_server(
        local_config(),
        {"env": {"ENDPOINT": {"has_value": True, "masked": "ghp…here"}}},
        monkeypatch,
    )

    assert response.status_code == 200
    assert stored.env["ENDPOINT"] == ENV_SECRET


def test_omitted_bearer_token_is_preserved(monkeypatch, state_dir):
    response, stored = patch_server(
        remote_config("bearer"),
        {"auth": {"kind": "bearer"}, "timeout_seconds": 15},
        monkeypatch,
    )

    assert response.status_code == 200
    assert stored.bearer_token == BEARER


def test_submitted_bearer_token_replaces(monkeypatch, state_dir):
    _, stored = patch_server(
        remote_config("bearer"),
        {"auth": {"kind": "bearer", "token": "fresh-token-value"}},
        monkeypatch,
    )

    assert stored.bearer_token == "fresh-token-value"


def test_omitted_client_secret_is_preserved(monkeypatch, state_dir):
    _, stored = patch_server(
        remote_config("oauth"),
        {"auth": {"kind": "oauth", "client_id": "cid", "scope": "read"}},
        monkeypatch,
    )

    assert stored.oauth is not None
    assert stored.oauth.client_secret == CLIENT_SECRET
    assert stored.oauth.scope == "read"


def test_switching_auth_kind_drops_the_other_kinds_secret(monkeypatch, state_dir):
    """Moving bearer → oauth must not leave the bearer token stored: it
    is unreachable through the UI and no longer serves any purpose."""
    _, stored = patch_server(
        remote_config("bearer"),
        {"auth": {"kind": "oauth", "client_name": "OpenProgram"}},
        monkeypatch,
    )

    assert stored.auth_kind == "oauth"
    assert stored.bearer_token is None
    assert json.dumps(stored.to_storage_dict()).count(BEARER) == 0


def test_headers_follow_the_same_preserve_rule(monkeypatch, state_dir):
    _, stored = patch_server(
        remote_config("bearer"), {"timeout_seconds": 12}, monkeypatch)

    assert stored.headers == {"X-Tenant": HEADER_SECRET}


# --- failure atomicity -------------------------------------------------


def test_failed_restart_leaves_the_stored_secret_untouched(monkeypatch, state_dir):
    """A rejected edit must not destroy a working server's credentials."""
    response, stored = patch_server(
        remote_config("bearer"),
        {"auth": {"kind": "bearer", "token": "would-be-new-token"}},
        monkeypatch,
        restart_fails=True,
    )

    assert response.status_code == 500
    assert stored.bearer_token == BEARER
    assert stored.headers == {"X-Tenant": HEADER_SECRET}
