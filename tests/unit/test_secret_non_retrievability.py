"""Backend contract tests for non-retrievable Web UI credentials."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.auth.store import AuthStore, set_store_for_testing
from openprogram.auth.types import Credential, CredentialData, CredentialPool
from openprogram.webui.routes import accounts, config, providers


_LONG_SECRET = "sk-123456789abc4"
_NEW_SECRET = "sk-new-secret-abc4"


@pytest.fixture
def secret_api(tmp_path, monkeypatch):
    """Minimal route app with isolated config, environment and AuthStore."""
    from openprogram import setup
    from openprogram.auth import account_priority, account_selection, rotation
    from openprogram.webui._model_listing import credentials

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(setup, "get_config_path", lambda: config_path)

    sidecar_root = tmp_path / "sidecars"
    for module in (account_priority, account_selection, rotation):
        monkeypatch.setattr(module, "DEFAULT_ROOT", sidecar_root)

    for name in (
        "OPENAI_API_KEY",
        "TAVILY_API_KEY",
        "BRAVE_API_KEY",
        "EXA_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    # Route tests must never perform a live provider probe.
    monkeypatch.setattr(
        credentials,
        "validate_credential",
        lambda *args, **kwargs: SimpleNamespace(
            status="valid",
            detail=None,
            to_dict=lambda: {"status": "valid"},
        ),
    )

    store = AuthStore(root=tmp_path / "store")
    set_store_for_testing(store)

    app = FastAPI()
    providers.register(app)
    accounts.register(app)
    config.register(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(
            client=client,
            store=store,
            read_config=setup._read_config,
            write_config=setup._write_config,
        )

    set_store_for_testing(None)


def _put_credential(
    store: AuthStore,
    *,
    provider: str = "openai",
    name: str = "work",
    kind: str = "api_key",
    value: str = _LONG_SECRET,
) -> None:
    store.put_pool(
        CredentialPool(
            provider_id=provider,
            account_id=name,
            credentials=[
                Credential(
                    provider_id=provider,
                    account_id=name,
                    kind=kind,
                    payload=CredentialData(kind=kind, auth_value=value),
                )
            ],
        )
    )


def _stored_key(store: AuthStore, provider: str = "openai", name: str = "work") -> str:
    pool = store.find_pool(provider, name)
    assert pool is not None
    return pool.credentials[0].payload.auth_value


@pytest.mark.parametrize(
    ("env_var", "secret", "masked"),
    [
        ("OPENAI_API_KEY", _LONG_SECRET, "sk-…abc4"),
        ("BRAVE_API_KEY", "abc12345wxyz", "abc…wxyz"),
        ("EXA_API_KEY", "12345678901", "••••••••"),
    ],
)
def test_config_key_get_returns_only_stable_mask(
    secret_api, monkeypatch, env_var, secret, masked
):
    monkeypatch.setenv(env_var, secret)

    response = secret_api.client.get(f"/api/config/key/{env_var}")

    assert response.status_code == 200
    assert response.json() == {"has_value": True, "masked": masked}
    assert secret not in response.text


def test_config_key_get_unset_has_exact_schema(secret_api):
    response = secret_api.client.get("/api/config/key/TAVILY_API_KEY")

    assert response.status_code == 200
    assert response.json() == {"has_value": False, "masked": ""}


def test_config_key_get_rejects_unknown_name(secret_api):
    response = secret_api.client.get("/api/config/key/UNDECLARED_API_KEY")

    assert response.status_code == 404


@pytest.mark.parametrize("query", ["?reveal=1", "?reveal=0", "?reveal"])
def test_config_key_reveal_query_is_not_a_route(secret_api, monkeypatch, query):
    monkeypatch.setenv("TAVILY_API_KEY", _LONG_SECRET)

    response = secret_api.client.get(f"/api/config/key/TAVILY_API_KEY{query}")

    assert response.status_code == 404
    assert _LONG_SECRET not in response.text


def test_account_reveal_route_answers_a_stable_deprecation(secret_api):
    """The legacy route answers, but never with a credential value.

    A client scripted against the old reveal endpoint gets a fixed 410
    with no credential field, so it fails loudly instead of quietly
    reading a secret that no longer leaves the store.
    """
    _put_credential(secret_api.store)

    response = secret_api.client.get(
        "/api/providers/openai/accounts/work/reveal"
    )

    assert response.status_code == 410
    assert response.json() == {
        "error": "credential reveal is no longer supported"
    }
    assert _LONG_SECRET not in response.text


def test_account_list_uses_only_nonsecret_key_fields(secret_api):
    _put_credential(secret_api.store)

    response = secret_api.client.get("/api/providers/openai/accounts")

    assert response.status_code == 200
    record = response.json()["accounts"][0]
    assert record["has_value"] is True
    assert record["masked_key"] == "sk-…abc4"
    assert {"identity", "can_reveal", "value"}.isdisjoint(record)
    assert _LONG_SECRET not in response.text


def test_config_replace_preserves_omitted_keys_and_has_exact_response(
    secret_api, monkeypatch
):
    secret_api.write_config(
        {
            "api_keys": {
                "BRAVE_API_KEY": "existing-brave-key",
                "TAVILY_API_KEY": "old-tavily-key",
            }
        }
    )
    monkeypatch.setenv("BRAVE_API_KEY", "existing-brave-key")
    monkeypatch.setenv("TAVILY_API_KEY", "old-tavily-key")

    response = secret_api.client.post(
        "/api/config",
        json={"api_keys": {"TAVILY_API_KEY": "new-tavily-key"}},
    )

    assert response.status_code == 200
    assert response.json() == {"saved": True}
    assert secret_api.read_config()["api_keys"] == {
        "BRAVE_API_KEY": "existing-brave-key",
        "TAVILY_API_KEY": "new-tavily-key",
    }
    assert os.environ["BRAVE_API_KEY"] == "existing-brave-key"
    assert os.environ["TAVILY_API_KEY"] == "new-tavily-key"


@pytest.mark.parametrize(
    "body",
    [
        {"api_keys": {"TAVILY_API_KEY": "new-key"}, "extra": True},
        {"api_keys": "not-a-map"},
        {
            "api_keys": {
                "TAVILY_API_KEY": "new-key",
                "UNDECLARED_API_KEY": "other-key",
            }
        },
        {"api_keys": {"TAVILY_API_KEY": None}},
        {"api_keys": {"TAVILY_API_KEY": ""}},
        {"api_keys": {"TAVILY_API_KEY": "••••••••"}},
        {"api_keys": {"TAVILY_API_KEY": "line\nbreak"}},
    ],
)
def test_config_replace_rejects_invalid_schema_without_mutation(
    secret_api, monkeypatch, body
):
    secret_api.write_config({"api_keys": {"TAVILY_API_KEY": "original-key"}})
    monkeypatch.setenv("TAVILY_API_KEY", "original-key")

    response = secret_api.client.post("/api/config", json=body)

    assert response.status_code == 400
    assert secret_api.read_config() == {
        "api_keys": {"TAVILY_API_KEY": "original-key"}
    }
    assert os.environ["TAVILY_API_KEY"] == "original-key"


def test_config_replace_rejected_credential_does_not_mutate(
    secret_api, monkeypatch
):
    secret_api.write_config({"api_keys": {"OPENAI_API_KEY": "original-key"}})
    monkeypatch.setenv("OPENAI_API_KEY", "original-key")
    monkeypatch.setattr(config, "_validate_api_key", lambda *args: "rejected")

    response = secret_api.client.post(
        "/api/config",
        json={"api_keys": {"OPENAI_API_KEY": _NEW_SECRET}},
    )

    assert response.status_code == 400
    assert secret_api.read_config()["api_keys"]["OPENAI_API_KEY"] == "original-key"
    assert os.environ["OPENAI_API_KEY"] == "original-key"


def test_config_delete_is_idempotent_and_clears_live_environment(
    secret_api, monkeypatch
):
    secret_api.write_config({"api_keys": {"TAVILY_API_KEY": "stored-key"}})
    monkeypatch.setenv("TAVILY_API_KEY", "live-key")

    first = secret_api.client.delete("/api/config/key/TAVILY_API_KEY")
    second = secret_api.client.delete("/api/config/key/TAVILY_API_KEY")

    assert first.status_code == second.status_code == 204
    assert first.content == second.content == b""
    assert secret_api.read_config().get("api_keys", {}) == {}
    assert "TAVILY_API_KEY" not in os.environ


def test_config_delete_rejects_body_without_mutation(secret_api, monkeypatch):
    secret_api.write_config({"api_keys": {"TAVILY_API_KEY": "stored-key"}})
    monkeypatch.setenv("TAVILY_API_KEY", "live-key")

    response = secret_api.client.request(
        "DELETE",
        "/api/config/key/TAVILY_API_KEY",
        json={},
    )

    assert response.status_code == 400
    assert secret_api.read_config()["api_keys"]["TAVILY_API_KEY"] == "stored-key"
    assert os.environ["TAVILY_API_KEY"] == "live-key"


def test_config_delete_unknown_name_is_404_without_mutation(secret_api):
    secret_api.write_config({"api_keys": {"TAVILY_API_KEY": "stored-key"}})

    response = secret_api.client.delete("/api/config/key/UNDECLARED_API_KEY")

    assert response.status_code == 404
    assert secret_api.read_config()["api_keys"] == {
        "TAVILY_API_KEY": "stored-key"
    }


def test_account_update_exact_schema_and_response(secret_api):
    _put_credential(secret_api.store)

    response = secret_api.client.post(
        "/api/providers/openai/accounts/work/update",
        json={"api_key": _NEW_SECRET},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert _stored_key(secret_api.store) == _NEW_SECRET


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"api_key": _NEW_SECRET, "extra": True},
        {"api_key": None},
        {"api_key": ""},
        {"api_key": "sk-…abc4"},
        {"api_key": "line\nbreak"},
        {"api_key": _NEW_SECRET, "validate": "false"},
        {"api_key": _NEW_SECRET, "validate": None},
    ],
)
def test_account_update_invalid_body_does_not_mutate(secret_api, body):
    _put_credential(secret_api.store)

    response = secret_api.client.post(
        "/api/providers/openai/accounts/work/update",
        json=body,
    )

    assert response.status_code == 400
    assert _stored_key(secret_api.store) == _LONG_SECRET


@pytest.mark.parametrize(
    "display_value",
    [
        "sk-…abc4",
        "•" * 8,
        "REDACTED",
        "<redacted>",
        "[redacted]",
        "***REDACTED***",
    ],
)
def test_account_update_rejects_every_display_value_without_rewriting_storage(
    secret_api, display_value
):
    _put_credential(secret_api.store)
    credential_path = secret_api.store.root / "auth/openai/work.json"
    before = credential_path.read_bytes()

    response = secret_api.client.post(
        "/api/providers/openai/accounts/work/update",
        json={"api_key": display_value, "validate": False},
    )

    assert 400 <= response.status_code < 500
    assert credential_path.read_bytes() == before


@pytest.mark.parametrize(
    "display_value",
    [
        "sk-…abc4",
        "•" * 8,
        "REDACTED",
        "<redacted>",
        "[redacted]",
        "***REDACTED***",
    ],
)
def test_account_add_rejects_every_display_value_without_writing_storage(
    secret_api, display_value
):
    before = {
        path.relative_to(secret_api.store.root): path.read_bytes()
        for path in secret_api.store.root.rglob("*")
        if path.is_file()
    }

    response = secret_api.client.post(
        "/api/providers/openai/accounts/keys",
        json={"api_key": display_value, "name": "work", "validate": False},
    )

    after = {
        path.relative_to(secret_api.store.root): path.read_bytes()
        for path in secret_api.store.root.rglob("*")
        if path.is_file()
    }
    assert 400 <= response.status_code < 500
    assert secret_api.store.list_pools() == []
    assert after == before


@pytest.mark.parametrize("kind", ["oauth", "device_code"])
def test_account_update_non_api_key_account_is_404_without_probe(
    secret_api, monkeypatch, kind
):
    from openprogram.webui._model_listing import credentials

    _put_credential(secret_api.store, kind=kind, value="access-token")
    calls = []
    monkeypatch.setattr(
        credentials,
        "validate_credential",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    response = secret_api.client.post(
        "/api/providers/openai/accounts/work/update",
        json={"api_key": _NEW_SECRET},
    )

    assert response.status_code == 404
    assert calls == []
    assert _stored_key(secret_api.store) == "access-token"


def test_account_update_missing_account_is_404_without_probe(
    secret_api, monkeypatch
):
    from openprogram.webui._model_listing import credentials

    calls = []
    monkeypatch.setattr(
        credentials,
        "validate_credential",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    response = secret_api.client.post(
        "/api/providers/openai/accounts/missing/update",
        json={"api_key": _NEW_SECRET},
    )

    assert response.status_code == 404
    assert calls == []
    assert secret_api.store.find_pool("openai", "missing") is None


def test_account_update_rejected_credential_does_not_mutate(
    secret_api, monkeypatch
):
    from openprogram.webui._model_listing import credentials

    _put_credential(secret_api.store)
    monkeypatch.setattr(
        credentials,
        "validate_credential",
        lambda *args, **kwargs: SimpleNamespace(
            status="invalid_credential",
            detail="rejected",
            to_dict=lambda: {"status": "invalid_credential"},
        ),
    )

    response = secret_api.client.post(
        "/api/providers/openai/accounts/work/update",
        json={"api_key": _NEW_SECRET},
    )

    assert response.status_code == 400
    assert _stored_key(secret_api.store) == _LONG_SECRET


def test_account_update_validate_false_skips_probe(secret_api, monkeypatch):
    from openprogram.webui._model_listing import credentials

    _put_credential(secret_api.store)

    def unexpected_probe(*args, **kwargs):
        raise AssertionError("validation should be skipped")

    monkeypatch.setattr(credentials, "validate_credential", unexpected_probe)

    response = secret_api.client.post(
        "/api/providers/openai/accounts/work/update",
        json={"api_key": _NEW_SECRET, "validate": False},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert _stored_key(secret_api.store) == _NEW_SECRET


def test_account_remove_has_exact_response_and_clears_active_pin(secret_api):
    from openprogram.auth.account_selection import get_active_pin, set_active_account

    _put_credential(secret_api.store)
    set_active_account("openai", "work")

    response = secret_api.client.post(
        "/api/providers/openai/accounts/remove",
        json={"id": "work"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "removed": True,
        "name": "work",
        "cleared_active": True,
    }
    assert secret_api.store.find_pool("openai", "work") is None
    assert get_active_pin("openai") == ""


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({}, 404),
        ({"id": "missing"}, 404),
        ({"name": "work"}, 400),
        ({"id": "work", "extra": True}, 400),
        ({"id": None}, 400),
        ({"id": ""}, 400),
        ({"id": "bad\nname"}, 400),
    ],
)
def test_account_remove_invalid_request_does_not_mutate(
    secret_api, body, status
):
    _put_credential(secret_api.store)

    response = secret_api.client.post(
        "/api/providers/openai/accounts/remove",
        json=body,
    )

    assert response.status_code == status
    assert _stored_key(secret_api.store) == _LONG_SECRET
