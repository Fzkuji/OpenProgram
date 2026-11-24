"""Backend contract tests for non-retrievable Web UI credentials."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.models.auth.store import AuthStore, set_store_for_testing
from openprogram.models.auth.types import Credential, CredentialData
from openprogram.interfaces.server.routes.identity import provider_credentials, providers
from openprogram.interfaces.server.routes.settings import config


_LONG_SECRET = "sk-123456789abc4"
_NEW_SECRET = "sk-new-secret-abc4"


@pytest.fixture
def secret_api(tmp_path, monkeypatch):
    """Minimal route app with isolated config, environment and AuthStore."""
    from openprogram.state import config as user_config
    from openprogram.models.listing import credentials

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(user_config, "get_config_path", lambda: config_path)

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
    provider_credentials.register(app)
    config.register(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(
            client=client,
            store=store,
            read_config=user_config.read_config,
            write_config=user_config.write_config,
        )

    set_store_for_testing(None)


def _put_credential(
    store: AuthStore,
    *,
    provider: str = "openai",
    name: str = "work",
    kind: str = "api_key",
    value: str = _LONG_SECRET,
) -> str:
    cred = store.add_credential(
        Credential(
            provider_id=provider,
            kind=kind,
            label=name,
            payload=CredentialData(kind=kind, auth_value=value),
        )
    )
    return cred.id


def _stored_key(store: AuthStore, provider: str = "openai") -> str:
    return store.get(provider).credentials[0].payload.auth_value


def _files(store: AuthStore) -> dict:
    return {
        path.relative_to(store.root): path.read_bytes()
        for path in store.root.rglob("*")
        if path.is_file() and path.suffix != ".lock"
    }


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


def test_credential_list_carries_only_nonsecret_key_fields(secret_api):
    _put_credential(secret_api.store)

    response = secret_api.client.get("/api/providers/openai/credentials")

    assert response.status_code == 200
    row = response.json()["credentials"][0]
    assert row["key_tail"] == "abc4"
    assert {"value", "api_key", "auth_value", "payload", "can_reveal"}.isdisjoint(row)
    assert _LONG_SECRET not in response.text


def test_config_replace_stores_the_key_and_leaves_the_rest_alone(
    secret_api, monkeypatch
):
    from openprogram.models.auth.services import stored_key

    secret_api.store.add_company_key("brave", "existing-brave-key")
    secret_api.store.add_company_key("tavily", "old-tavily-key")
    monkeypatch.setenv("BRAVE_API_KEY", "live-brave-key")

    response = secret_api.client.post(
        "/api/config",
        json={"api_keys": {"TAVILY_API_KEY": "new-tavily-key"}},
    )

    assert response.status_code == 200
    assert response.json() == {"saved": True}
    # The key is the store's: one Tavily key, replaced; nothing in config.json
    # and nothing written to the process environment.
    assert [k["value"] for k in secret_api.store.company_keys("tavily")] == ["new-tavily-key"]
    assert stored_key("BRAVE_API_KEY") == "existing-brave-key"
    assert "api_keys" not in secret_api.read_config()
    assert os.environ["BRAVE_API_KEY"] == "live-brave-key"
    assert "TAVILY_API_KEY" not in os.environ


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


def test_config_delete_is_idempotent_and_leaves_the_environment(
    secret_api, monkeypatch
):
    secret_api.store.add_company_key("tavily", "stored-key")
    monkeypatch.setenv("TAVILY_API_KEY", "live-key")

    first = secret_api.client.delete("/api/config/key/TAVILY_API_KEY")
    second = secret_api.client.delete("/api/config/key/TAVILY_API_KEY")

    assert first.status_code == second.status_code == 204
    assert first.content == second.content == b""
    assert secret_api.store.company_keys("tavily") == []
    # A variable is read where it is set and never written.
    assert os.environ["TAVILY_API_KEY"] == "live-key"


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


def test_credential_replace_exact_response_keeps_name_and_place(secret_api):
    cid = _put_credential(secret_api.store)

    response = secret_api.client.post(
        f"/api/providers/openai/credentials/{cid}/replace",
        json={"api_key": _NEW_SECRET},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert (body["credential"]["id"], body["credential"]["label"]) == (cid, "work")
    assert body["credential"]["key_tail"] == "abc4"
    assert _NEW_SECRET not in response.text
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
    ],
)
def test_credential_replace_invalid_body_does_not_mutate(secret_api, body):
    cid = _put_credential(secret_api.store)

    response = secret_api.client.post(
        f"/api/providers/openai/credentials/{cid}/replace", json=body
    )

    assert response.status_code == 400
    assert _stored_key(secret_api.store) == _LONG_SECRET


_DISPLAY_VALUES = [
    "sk-…abc4",
    "•" * 8,
    "REDACTED",
    "<redacted>",
    "[redacted]",
    "***REDACTED***",
]


@pytest.mark.parametrize("display_value", _DISPLAY_VALUES)
def test_credential_replace_rejects_every_display_value_without_rewriting_storage(
    secret_api, display_value
):
    cid = _put_credential(secret_api.store)
    before = _files(secret_api.store)

    response = secret_api.client.post(
        f"/api/providers/openai/credentials/{cid}/replace",
        json={"api_key": display_value},
    )

    assert 400 <= response.status_code < 500
    assert _files(secret_api.store) == before


@pytest.mark.parametrize("display_value", _DISPLAY_VALUES)
def test_credential_add_rejects_every_display_value_without_writing_storage(
    secret_api, display_value
):
    before = _files(secret_api.store)

    response = secret_api.client.post(
        "/api/providers/openai/credentials",
        json={"api_key": display_value, "label": "work", "validate": False},
    )

    assert 400 <= response.status_code < 500
    assert secret_api.store.get("openai").credentials == []
    assert _files(secret_api.store) == before


@pytest.mark.parametrize("kind", ["oauth", "device_code"])
def test_credential_replace_of_a_sign_in_is_refused_without_probe(
    secret_api, monkeypatch, kind
):
    from openprogram.models.listing import credentials

    cid = _put_credential(secret_api.store, kind=kind, value="access-token")
    calls = []
    monkeypatch.setattr(
        credentials, "validate_credential", lambda *a, **k: calls.append((a, k))
    )

    response = secret_api.client.post(
        f"/api/providers/openai/credentials/{cid}/replace",
        json={"api_key": _NEW_SECRET},
    )

    assert response.status_code == 400
    assert calls == []
    assert _stored_key(secret_api.store) == "access-token"


def test_credential_replace_missing_is_404_without_probe(secret_api, monkeypatch):
    from openprogram.models.listing import credentials

    calls = []
    monkeypatch.setattr(
        credentials, "validate_credential", lambda *a, **k: calls.append((a, k))
    )

    response = secret_api.client.post(
        "/api/providers/openai/credentials/missing/replace",
        json={"api_key": _NEW_SECRET},
    )

    assert response.status_code == 404
    assert calls == []


def test_credential_replace_rejected_key_does_not_mutate(secret_api, monkeypatch):
    from openprogram.models.listing import credentials

    cid = _put_credential(secret_api.store)
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
        f"/api/providers/openai/credentials/{cid}/replace",
        json={"api_key": _NEW_SECRET},
    )

    assert response.status_code == 400
    assert _stored_key(secret_api.store) == _LONG_SECRET


def test_credential_add_validate_false_skips_probe(secret_api, monkeypatch):
    from openprogram.models.listing import credentials

    def unexpected_probe(*args, **kwargs):
        raise AssertionError("validation should be skipped")

    monkeypatch.setattr(credentials, "validate_credential", unexpected_probe)

    response = secret_api.client.post(
        "/api/providers/openai/credentials",
        json={"api_key": _NEW_SECRET, "label": "work", "validate": False},
    )

    assert response.status_code == 200
    assert response.json()["credential"]["label"] == "work"
    assert _NEW_SECRET not in response.text
    assert _stored_key(secret_api.store) == _NEW_SECRET


def test_credential_remove_drops_the_row(secret_api):
    cid = _put_credential(secret_api.store)

    response = secret_api.client.delete(f"/api/providers/openai/credentials/{cid}")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert secret_api.store.get("openai").credentials == []


def test_check_key_stores_nothing_and_the_add_that_follows_reuses_its_check(secret_api, monkeypatch):
    from openprogram.models.auth import manage
    from openprogram.models.listing import credentials

    manage._checked.clear()
    probes = []
    monkeypatch.setattr(
        credentials,
        "validate_credential",
        lambda *args, **kwargs: probes.append(1) or SimpleNamespace(
            status="valid", detail=None, to_dict=lambda: {"status": "valid"}),
    )
    before = _files(secret_api.store)

    checked = secret_api.client.post(
        "/api/providers/openai/credentials/check-key", json={"api_key": _NEW_SECRET})

    assert checked.status_code == 200
    assert checked.json()["ok"] is True
    assert _NEW_SECRET not in checked.text
    assert _files(secret_api.store) == before  # a check stores nothing
    assert len(probes) == 1

    added = secret_api.client.post(
        "/api/providers/openai/credentials",
        json={"api_key": _NEW_SECRET, "label": "named"})

    assert added.status_code == 200
    assert added.json()["credential"]["label"] == "named"  # name in the same request
    assert len(probes) == 1  # the dialog's check was reused, not repeated


@pytest.mark.parametrize(
    "body",
    [{}, {"api_key": _NEW_SECRET, "label": "x"}, {"api_key": "sk-…abc4"}, {"api_key": _NEW_SECRET, "replace": ""}],
)
def test_check_key_refuses_a_bad_body(secret_api, body):
    response = secret_api.client.post("/api/providers/openai/credentials/check-key", json=body)
    assert response.status_code == 400
