"""The gateway key opens ``/gateway/`` and nothing else; launch overrides point a
harness at the gateway without touching its config."""
from __future__ import annotations

import tomllib

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from openprogram.interfaces.server.owner_auth import OwnerAuthMiddleware, OwnerAuthState


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


@pytest.fixture
def guarded(state_dir):
    app = FastAPI()

    @app.get("/gateway/openai/v1/models")
    def models():
        return {"object": "list", "data": []}

    @app.get("/gateway/anthropic/v1/models")
    def anthropic_models():
        return {"data": []}

    @app.get("/api/x")
    def api():
        return {"ok": True}

    state = OwnerAuthState.from_raw_token(bytes(range(32)), owner_principal_id="owner/install/0123456789abcdef",
                                          bind_host="127.0.0.1", port=18100, allowed_origins=())
    return OwnerAuthMiddleware(app, auth_state=state), state


def test_gateway_key_is_accepted_only_on_gateway_paths(guarded) -> None:
    from openprogram.models.gateway.keys import gateway_key

    app, state = guarded
    key = gateway_key()
    with TestClient(app, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 50000)) as client:
        assert client.get("/gateway/openai/v1/models", headers={"Authorization": f"Bearer {key}"}).status_code == 200
        assert client.get("/gateway/anthropic/v1/models", headers={"x-api-key": key}).status_code == 200
        assert client.get("/gateway/openai/v1/models", headers={"Authorization": f"Bearer {state.token}"}).status_code == 200
        assert client.get("/api/x", headers={"Authorization": f"Bearer {key}"}).status_code == 401

        missing = client.get("/gateway/openai/v1/models")
        assert missing.status_code == 401 and missing.json()["error"]["code"] == "invalid_api_key"
        wrong = client.get("/gateway/anthropic/v1/models", headers={"x-api-key": "opgw-wrong"})
        assert wrong.status_code == 401 and wrong.json()["error"]["type"] == "authentication_error"
    with TestClient(app, base_url="http://127.0.0.1:18100", client=("192.168.1.9", 50000)) as remote:
        assert remote.get("/gateway/openai/v1/models").status_code == 401
        assert remote.get("/gateway/openai/v1/models", headers={"Authorization": f"Bearer {key}"}).status_code == 200


def test_gateway_key_survives_and_is_private(state_dir) -> None:
    from openprogram.models.gateway.keys import gateway_key, verify_gateway_key

    first = gateway_key()
    assert gateway_key() == first and verify_gateway_key(first) and not verify_gateway_key(first + "x")
    assert (state_dir / "gateway" / "key").stat().st_mode & 0o077 == 0


def test_codex_launch_overrides(state_dir, monkeypatch) -> None:
    from openprogram.models.gateway import launch

    monkeypatch.setattr("openprogram.models.gateway.address.worker_base_url", lambda stable=False: "http://127.0.0.1:18200")
    monkeypatch.setattr(launch, "_context_window", lambda ref: 128000)
    args, env = launch.codex_launch("openprogram:deepseek/deepseek-chat")
    assert args[::2] == ["-c"] * (len(args) // 2)
    pairs = tomllib.loads("\n".join(args[1::2]))
    assert pairs["model_provider"] == "openprogram"
    assert pairs["model"] == "deepseek/deepseek-chat"
    assert pairs["model_context_window"] == 128000
    assert pairs["model_providers"]["openprogram"] == {
        "name": "OpenProgram", "base_url": "http://127.0.0.1:18200/gateway/openai/v1",
        "wire_api": "responses", "env_key": launch.KEY_ENV}
    assert env[launch.KEY_ENV].startswith("opgw-")
    assert env[launch.KEY_ENV] not in " ".join(args)
    with pytest.raises(ValueError):
        launch.codex_launch("openprogram:nothing")


def test_claude_code_launch_env(state_dir, monkeypatch) -> None:
    from openprogram.models.gateway import launch

    monkeypatch.setattr("openprogram.models.gateway.address.worker_base_url", lambda stable=False: "http://127.0.0.1:18200")
    env = launch.claude_code_launch("openprogram:zai/glm-5", {"haiku": "openprogram:deepseek/deepseek-chat"})
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:18200/gateway/anthropic"
    assert env["ANTHROPIC_AUTH_TOKEN"].startswith("opgw-")
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "zai/glm-5"
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == env["ANTHROPIC_SMALL_FAST_MODEL"] == "deepseek/deepseek-chat"
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == "zai/glm-5"
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "0" and "ANTHROPIC_MODEL" not in env
