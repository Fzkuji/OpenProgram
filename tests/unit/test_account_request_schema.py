"""Strict request schemas for the account / provider-config endpoints.

Every case asserts the SAME invariant: a request the schema rejects leaves
storage exactly as it was. A validation error that still wrote something
would be worse than no validation at all, because the caller reads "400" and
believes nothing happened.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.auth.store import AuthStore, set_store_for_testing
from openprogram.auth.types import Credential, CredentialData, CredentialPool
from openprogram.webui.routes import accounts, config, providers


_SECRET = "sk-123456789abc4"


@pytest.fixture
def account_api(tmp_path, monkeypatch):
    """Route app over an isolated AuthStore, sidecar root and config file."""
    from openprogram import setup
    from openprogram.auth import account_priority, account_selection, rotation
    from openprogram.providers import storage as provider_storage
    from openprogram.webui._model_listing import credentials

    monkeypatch.setattr(setup, "get_config_path", lambda: tmp_path / "config.json")

    sidecar_root = tmp_path / "sidecars"
    for module in (account_priority, account_selection, rotation):
        monkeypatch.setattr(module, "DEFAULT_ROOT", sidecar_root)

    monkeypatch.setattr(
        credentials,
        "validate_credential",
        lambda *args, **kwargs: SimpleNamespace(
            status="valid", detail=None, to_dict=lambda: {"status": "valid"}
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
            client=client, store=store, provider_storage=provider_storage
        )

    set_store_for_testing(None)


def _put_account(store: AuthStore, name: str = "work", value: str = _SECRET) -> None:
    store.put_pool(
        CredentialPool(
            provider_id="openai",
            account_id=name,
            credentials=[
                Credential(
                    provider_id="openai",
                    account_id=name,
                    kind="api_key",
                    payload=CredentialData(kind="api_key", auth_value=value),
                )
            ],
        )
    )


def _account_state(store: AuthStore) -> list[tuple[str, str]]:
    """Every stored openai account as (account, secret) — the mutation probe."""
    return sorted(
        (pool.account_id, pool.credentials[0].payload.auth_value)
        for pool in store.list_pools()
        if pool.provider_id == "openai" and pool.credentials
    )


# ---------------------------------------------------------------------------
# accounts/use
# ---------------------------------------------------------------------------


def test_accounts_use_activates_a_real_account(account_api):
    from openprogram.auth.account_selection import get_active_pin

    _put_account(account_api.store)

    response = account_api.client.post(
        "/api/providers/openai/accounts/use", json={"id": "work"}
    )

    assert response.status_code == 200
    assert response.json() == {"active": "work"}
    assert get_active_pin("openai") == "work"


def test_accounts_use_empty_id_clears_the_pin(account_api):
    from openprogram.auth.account_selection import get_active_pin, set_active_account

    _put_account(account_api.store)
    set_active_account("openai", "work")

    response = account_api.client.post(
        "/api/providers/openai/accounts/use", json={"id": ""}
    )

    assert response.status_code == 200
    assert get_active_pin("openai") == ""


@pytest.mark.parametrize(
    ("body", "status"),
    [
        (None, 400),
        ({}, 400),
        ({"name": "work"}, 400),
        ({"id": "work", "extra": True}, 400),
        ({"id": None}, 400),
        ({"id": "bad\nname"}, 400),
        ({"id": "work", "api_key": _SECRET}, 400),
        ({"id": "missing"}, 404),
    ],
)
def test_accounts_use_invalid_request_leaves_the_pin_untouched(
    account_api, body, status
):
    from openprogram.auth.account_selection import get_active_pin, set_active_account

    _put_account(account_api.store)
    set_active_account("openai", "work")

    response = account_api.client.post(
        "/api/providers/openai/accounts/use", json=body
    )

    assert response.status_code == status
    assert get_active_pin("openai") == "work"
    assert _account_state(account_api.store) == [("work", _SECRET)]


# ---------------------------------------------------------------------------
# accounts/rename
# ---------------------------------------------------------------------------


def test_accounts_rename_moves_the_credential(account_api):
    _put_account(account_api.store)

    response = account_api.client.post(
        "/api/providers/openai/accounts/rename",
        json={"id": "work", "name": "personal"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "name": "personal"}
    assert _account_state(account_api.store) == [("personal", _SECRET)]


@pytest.mark.parametrize(
    ("body", "status"),
    [
        (None, 400),
        ({}, 400),
        ({"id": "work"}, 400),
        ({"name": "personal"}, 400),
        ({"old": "work", "new": "personal"}, 400),
        ({"id": "work", "name": "personal", "extra": 1}, 400),
        ({"id": "work", "name": ""}, 400),
        ({"id": "work", "name": None}, 400),
        ({"id": "work", "name": "bad\nname"}, 400),
        ({"id": "work", "name": "personal", "api_key": _SECRET}, 400),
        ({"id": "missing", "name": "personal"}, 404),
        ({"id": "work", "name": "other"}, 409),
    ],
)
def test_accounts_rename_invalid_request_does_not_mutate(account_api, body, status):
    _put_account(account_api.store)
    _put_account(account_api.store, name="other", value="sk-other-secret-9999")
    before = _account_state(account_api.store)

    response = account_api.client.post(
        "/api/providers/openai/accounts/rename", json=body
    )

    assert response.status_code == status
    assert _account_state(account_api.store) == before


# ---------------------------------------------------------------------------
# accounts/rotation
# ---------------------------------------------------------------------------


def test_accounts_rotation_enables_a_known_strategy(account_api):
    from openprogram.auth.rotation import get_rotation

    response = account_api.client.post(
        "/api/providers/openai/accounts/rotation",
        json={"enabled": True, "strategy": "round_robin"},
    )

    assert response.status_code == 200
    assert get_rotation("openai") == {
        "enabled": True,
        "strategy": "round_robin",
    }


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"strategy": "round_robin"},
        {"enabled": "yes"},
        {"enabled": 1},
        {"enabled": True, "strategy": "made_up"},
        {"enabled": True, "extra": 1},
        {"enabled": True, "api_key": _SECRET},
    ],
)
def test_accounts_rotation_invalid_request_does_not_mutate(account_api, body):
    from openprogram.auth.rotation import get_rotation, set_rotation

    set_rotation("openai", enabled=False, strategy="fill_first")
    before = get_rotation("openai")

    response = account_api.client.post(
        "/api/providers/openai/accounts/rotation", json=body
    )

    assert response.status_code == 400
    assert get_rotation("openai") == before


def test_accounts_rotation_rejects_claude_code(account_api):
    response = account_api.client.post(
        "/api/providers/claude-code/accounts/rotation", json={"enabled": True}
    )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# accounts/reorder
# ---------------------------------------------------------------------------


def test_accounts_reorder_stores_the_order(account_api):
    from openprogram.auth.account_priority import get_account_priority

    response = account_api.client.post(
        "/api/providers/openai/accounts/reorder",
        json={"order": ["work", "other"]},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "order": ["work", "other"]}
    assert get_account_priority("openai") == ["work", "other"]


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"order": "work"},
        {"order": ["work", ""]},
        {"order": ["work", None]},
        {"order": ["work", "work"]},
        {"order": ["bad\nname"]},
        {"order": ["work"], "extra": 1},
        {"order": ["work"], "api_key": _SECRET},
    ],
)
def test_accounts_reorder_invalid_request_does_not_mutate(account_api, body):
    from openprogram.auth.account_priority import get_account_priority, set_account_priority

    set_account_priority("openai", ["work", "other"])

    response = account_api.client.post(
        "/api/providers/openai/accounts/reorder", json=body
    )

    assert response.status_code == 400
    assert get_account_priority("openai") == ["work", "other"]


# ---------------------------------------------------------------------------
# accounts/enabled
# ---------------------------------------------------------------------------


def test_accounts_enabled_disables_one_account(account_api):
    from openprogram.auth.rotation import get_accounts_out_of_rotation

    _put_account(account_api.store)

    response = account_api.client.post(
        "/api/providers/openai/accounts/enabled",
        json={"id": "work", "enabled": False},
    )

    assert response.status_code == 200
    assert get_accounts_out_of_rotation("openai") == {"work"}


@pytest.mark.parametrize(
    ("body", "status"),
    [
        (None, 400),
        ({}, 400),
        ({"id": "work"}, 400),
        ({"enabled": False}, 400),
        ({"name": "work", "enabled": False}, 400),
        ({"id": "work", "enabled": "no"}, 400),
        ({"id": "", "enabled": False}, 400),
        ({"id": "work", "enabled": False, "extra": 1}, 400),
        ({"id": "work", "enabled": False, "api_key": _SECRET}, 400),
        ({"id": "missing", "enabled": False}, 404),
    ],
)
def test_accounts_enabled_invalid_request_does_not_mutate(account_api, body, status):
    from openprogram.auth.rotation import get_accounts_out_of_rotation

    _put_account(account_api.store)

    response = account_api.client.post(
        "/api/providers/openai/accounts/enabled", json=body
    )

    assert response.status_code == status
    assert get_accounts_out_of_rotation("openai") == set()


# ---------------------------------------------------------------------------
# accounts/add (login handoff — never a credential sink)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"name": "work", "extra": 1},
        {"name": "bad\nname"},
        {"name": 5},
        {"api_key": _SECRET},
        {"name": "work", "api_key": _SECRET},
    ],
)
def test_accounts_add_rejects_unknown_and_credential_fields(account_api, body):
    response = account_api.client.post(
        "/api/providers/openai/accounts/add", json=body
    )

    assert response.status_code == 400
    assert _account_state(account_api.store) == []


def test_account_manager_form_bodies_are_all_accepted(account_api):
    """The exact request bodies ``account-manager.tsx`` sends must pass.

    Tightening a schema against the shapes the only real caller produces is
    how a validation change turns into a dead button, so each body here is
    copied from a fetch call in that component.
    """
    _put_account(account_api.store)
    _put_account(account_api.store, name="second", value="sk-second-secret-1234")

    accepted = [
        ("/api/providers/openai/accounts/use", {"id": "work"}),
        ("/api/providers/openai/accounts/use", {"id": ""}),
        ("/api/providers/openai/accounts/enabled", {"id": "work", "enabled": True}),
        ("/api/providers/openai/accounts/enabled", {"id": "work", "enabled": False}),
        ("/api/providers/openai/accounts/reorder", {"order": ["second", "work"]}),
        # toggleRotation sends strategy only when the state carries one;
        # an absent strategy must not be a validation failure.
        ("/api/providers/openai/accounts/rotation", {"enabled": True}),
        (
            "/api/providers/openai/accounts/rotation",
            {"enabled": True, "strategy": "fill_first"},
        ),
        ("/api/providers/openai/accounts/rename", {"id": "second", "name": "renamed"}),
    ]
    for path, body in accepted:
        response = account_api.client.post(path, json=body)
        assert response.status_code == 200, (path, body, response.text)


def test_accounts_add_returns_login_methods(account_api):
    response = account_api.client.post(
        "/api/providers/openai/accounts/add", json={"name": "work"}
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "login"


# ---------------------------------------------------------------------------
# accounts/keys (a real credential sink — strict field set, no extras)
# ---------------------------------------------------------------------------


def test_accounts_keys_creates_the_account(account_api):
    response = account_api.client.post(
        "/api/providers/openai/accounts/keys",
        json={"api_key": _SECRET, "name": "work"},
    )

    assert response.status_code == 200
    assert _account_state(account_api.store) == [("work", _SECRET)]


@pytest.mark.parametrize(
    ("body", "status"),
    [
        (None, 400),
        ({}, 400),
        ({"name": "work"}, 400),
        ({"api_key": ""}, 400),
        ({"api_key": None}, 400),
        ({"api_key": "line\nbreak"}, 400),
        ({"api_key": _SECRET, "extra": 1}, 400),
        ({"api_key": _SECRET, "validate": "yes"}, 400),
        ({"api_key": _SECRET, "name": 5}, 400),
        ({"api_key": _SECRET, "name": "bad\nname"}, 400),
    ],
)
def test_accounts_keys_invalid_request_stores_nothing(account_api, body, status):
    response = account_api.client.post(
        "/api/providers/openai/accounts/keys", json=body
    )

    assert response.status_code == status
    assert _account_state(account_api.store) == []


def test_accounts_keys_duplicate_name_does_not_overwrite(account_api):
    _put_account(account_api.store)

    response = account_api.client.post(
        "/api/providers/openai/accounts/keys",
        json={"api_key": "sk-replacement-abcd", "name": "work"},
    )

    assert response.status_code == 409
    assert _account_state(account_api.store) == [("work", _SECRET)]


# ---------------------------------------------------------------------------
# accounts/{name}/retry
# ---------------------------------------------------------------------------


def test_account_retry_resolves_the_alias_pool(account_api, monkeypatch):
    """A legacy alias must reach the canonical pool, not 404 past it.

    ``retry`` used the raw path segment as the pool id, so an alias found no
    pool and the cooldown was never cleared.
    """
    from openprogram.auth import aliases

    _put_account(account_api.store)
    monkeypatch.setattr(
        aliases, "resolve", lambda pid: "openai" if pid == "legacy-alias" else pid
    )

    response = account_api.client.post(
        "/api/providers/legacy-alias/accounts/work/retry"
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_account_retry_missing_account_is_404(account_api):
    response = account_api.client.post(
        "/api/providers/openai/accounts/missing/retry"
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# provider config / toggles / probes reject credentials
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/providers/openai/config", {"api_key": _SECRET}),
        ("/api/providers/openai/config", {"base_url": "x", "token": _SECRET}),
        ("/api/providers/openai/config", {"unknown_field": 1}),
        ("/api/providers/openai/toggle", {"enabled": True, "api_key": _SECRET}),
        ("/api/providers/openai/toggle", {"enabled": "yes"}),
        ("/api/providers/openai/test", {"api_key": _SECRET}),
        ("/api/providers/openai/validate", {"api_key": _SECRET}),
        ("/api/providers/openai/validate", {"model": "x", "secret": "y"}),
        ("/api/providers/custom", {"base_url": "http://x", "api_key": _SECRET}),
        ("/api/providers/openai/models", {"id": "m", "api_key": _SECRET}),
        ("/api/providers/openai/configure/step/one", {"api_key": _SECRET}),
        ("/api/search-providers/default", {"provider": "tavily", "api_key": _SECRET}),
    ],
)
def test_provider_endpoints_reject_credential_bearing_bodies(
    account_api, path, body
):
    response = account_api.client.post(path, json=body)

    assert response.status_code == 400
    assert _SECRET not in response.text


def test_provider_config_stores_only_declared_fields(account_api):
    response = account_api.client.post(
        "/api/providers/openai/config", json={"base_url": "http://localhost:9"}
    )

    assert response.status_code == 200
    assert (
        account_api.provider_storage.get_provider_config("openai")["base_url"]
        == "http://localhost:9"
    )


def test_provider_config_accepts_the_shapes_the_web_form_sends(account_api):
    """Exactly the bodies ``api.setProviderConfig`` produces, including the
    ``base_url: null`` the UI sends to clear an override."""
    for body in (
        {"base_url": "http://localhost:9"},
        {"base_url": None},
        {"base_url": ""},
        {"use_responses_api": True},
        {"use_responses_api": False},
    ):
        response = account_api.client.post("/api/providers/openai/config", json=body)
        assert response.status_code == 200, body


def test_provider_config_rejects_a_non_string_base_url(account_api):
    response = account_api.client.post(
        "/api/providers/openai/config", json={"base_url": 5}
    )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# /api/settings is not a credential sink
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key", ["OPENAI_API_KEY", "TAVILY_API_KEY", "some_api_key_field"]
)
def test_settings_refuses_credential_keys(account_api, key):
    response = account_api.client.post(
        "/api/settings", json={"key": key, "value": _SECRET}
    )

    assert response.status_code == 400
    assert _SECRET not in response.text


@pytest.mark.parametrize(
    "body", [None, {}, {"value": 1}, {"key": "theme", "extra": 1}, {"key": ""}]
)
def test_settings_rejects_malformed_bodies(account_api, body):
    response = account_api.client.post("/api/settings", json=body)

    assert response.status_code == 400
