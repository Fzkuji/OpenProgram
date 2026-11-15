"""Account add / update / validate persist usable status, not auth-only 200."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.models.auth.store import AuthStore, set_store_for_testing
from openprogram.models.auth.types import Credential, CredentialData, CredentialPool
from openprogram.models.listing import credentials as cr
from openprogram.interfaces.server.routes.identity import accounts, providers


_SECRET = "sk-123456789abc4"


def _result(status, *, kind="openai_bearer", via="GET /models", detail=None, ok=None):
    ok = (status == cr.VALID) if ok is None else ok
    payload = {
        "status": status, "ok": ok, "kind": kind, "via": via, "detail": detail,
    }
    return SimpleNamespace(
        status=status, ok=ok, kind=kind, via=via, detail=detail,
        to_dict=lambda: dict(payload),
    )


@pytest.fixture
def account_api(tmp_path, monkeypatch):
    from openprogram.state import config as user_config
    from openprogram.models.auth.account import account_priority
    from openprogram.models.auth.account import account_selection
    from openprogram.models.auth import rotation

    monkeypatch.setattr(user_config, "get_config_path", lambda: tmp_path / "config.json")
    sidecar_root = tmp_path / "sidecars"
    for module in (account_priority, account_selection, rotation):
        monkeypatch.setattr(module, "default_root", lambda: sidecar_root)

    store = AuthStore(root=tmp_path / "store")
    set_store_for_testing(store)
    # A checked key reads its balance and lists its models; never over the network here.
    from openprogram.models.listing import key_check
    monkeypatch.setattr(key_check, "read_new_balance", lambda provider, account: None)
    monkeypatch.setattr(key_check, "suggested_models", lambda provider, rows=None: (0, []))

    app = FastAPI()
    providers.register(app)
    accounts.register(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, store=store, monkeypatch=monkeypatch)

    set_store_for_testing(None)


def _put(store, *, status="unknown", name="work"):
    store.put_pool(
        CredentialPool(
            provider_id="openai",
            account_id=name,
            credentials=[
                Credential(
                    provider_id="openai",
                    account_id=name,
                    kind="api_key",
                    payload=CredentialData(kind="api_key", auth_value=_SECRET),
                    status=status,
                )
            ],
        )
    )


def test_add_key_auth_only_200_does_not_persist_valid(account_api, monkeypatch):
    monkeypatch.setattr(
        cr, "validate_credential",
        lambda *a, **k: _result(cr.VALID, via="GET /models"),
    )
    r = account_api.client.post(
        "/api/providers/openai/accounts/keys",
        json={"api_key": _SECRET, "name": "work", "validate": True},
    )
    assert r.status_code == 200
    cred = account_api.store.find_pool("openai", "work").credentials[0]
    assert cred.status != "valid"


def test_add_key_no_balance_persists_billing_blocked(account_api, monkeypatch):
    monkeypatch.setattr(
        cr, "validate_credential",
        lambda *a, **k: _result(
            cr.VALID_NO_BALANCE, kind="openrouter_key", via="GET /key",
            detail="no credits",
        ),
    )
    r = account_api.client.post(
        "/api/providers/openrouter/accounts/keys",
        json={"api_key": _SECRET, "name": "work", "validate": True},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "billing_blocked"
    cred = account_api.store.find_pool("openrouter", "work").credentials[0]
    assert cred.status == "billing_blocked"


def test_add_key_rejects_only_invalid_credential(account_api, monkeypatch):
    monkeypatch.setattr(
        cr, "validate_credential",
        lambda *a, **k: _result(cr.INVALID_CREDENTIAL, via="GET /models"),
    )
    r = account_api.client.post(
        "/api/providers/openai/accounts/keys",
        json={"api_key": _SECRET, "name": "work", "validate": True},
    )
    assert r.status_code == 400
    assert account_api.store.find_pool("openai", "work") is None


def test_update_does_not_force_valid(account_api, monkeypatch):
    _put(account_api.store, status="unknown")
    monkeypatch.setattr(
        cr, "validate_credential",
        lambda *a, **k: _result(cr.VALID, via="GET /models"),
    )
    r = account_api.client.post(
        "/api/providers/openai/accounts/work/update",
        json={"api_key": "sk-replacement-abcd", "validate": True},
    )
    assert r.status_code == 200
    cred = account_api.store.find_pool("openai", "work").credentials[0]
    assert cred.status != "valid"
    assert cred.payload.auth_value == "sk-replacement-abcd"


def test_update_probe_exception_leaves_status(account_api, monkeypatch):
    _put(account_api.store, status="billing_blocked")

    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(cr, "validate_credential", boom)
    r = account_api.client.post(
        "/api/providers/openai/accounts/work/update",
        json={"api_key": "sk-replacement-abcd", "validate": True},
    )
    assert r.status_code == 200
    cred = account_api.store.find_pool("openai", "work").credentials[0]
    assert cred.status == "billing_blocked"


def test_on_mount_validate_does_not_revive_billing_blocked(account_api, monkeypatch):
    _put(account_api.store, status="billing_blocked")
    monkeypatch.setattr(
        cr, "validate_credential",
        lambda *a, **k: _result(cr.VALID, via="GET /models"),
    )
    r = account_api.client.post("/api/providers/openai/accounts/work/validate")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "billing_blocked"
    assert body["ok"] is False
    cred = account_api.store.find_pool("openai", "work").credentials[0]
    assert cred.status == "billing_blocked"


def test_explicit_ping_restores_valid(account_api, monkeypatch):
    _put(account_api.store, status="billing_blocked")

    def fake(*a, **k):
        if k.get("prove_usable"):
            return _result(
                cr.VALID, via="POST /chat/completions", kind="openai_bearer",
            )
        return _result(cr.VALID, via="GET /models")

    monkeypatch.setattr(cr, "validate_credential", fake)
    r = account_api.client.post(
        "/api/providers/openai/accounts/work/validate?ping=true"
    )
    assert r.status_code == 200
    assert r.json()["status"] == "valid"
    assert r.json()["ok"] is True
    cred = account_api.store.find_pool("openai", "work").credentials[0]
    assert cred.status == "valid"


def test_openrouter_key_remaining_persists_valid(account_api, monkeypatch):
    _put(account_api.store, status="billing_blocked")
    monkeypatch.setattr(
        cr, "validate_credential",
        lambda *a, **k: _result(cr.VALID, kind="openrouter_key", via="GET /key"),
    )
    r = account_api.client.post("/api/providers/openai/accounts/work/validate")
    assert r.json()["status"] == "valid"
    cred = account_api.store.find_pool("openai", "work").credentials[0]
    assert cred.status == "valid"


def _read_cfg(provider):
    from openprogram.models.providers.settings.storage import _read_providers_cfg
    return _read_providers_cfg().get(provider) or {}


def test_add_key_answers_one_line_and_turns_a_new_provider_on(account_api, monkeypatch):
    monkeypatch.setattr(
        cr, "validate_credential",
        lambda *a, **k: _result(cr.VALID, kind="openrouter_key", via="GET /key"),
    )
    r = account_api.client.post(
        "/api/providers/openrouter/accounts/keys",
        json={"api_key": _SECRET, "validate": True},
    )
    check = r.json()["check"]
    assert r.status_code == 200 and check["result"] == "works"
    assert check["auto_enabled"] is True and _read_cfg("openrouter").get("enabled") is True
    assert _SECRET not in r.text


def test_a_rejected_key_answers_its_check_and_is_not_kept(account_api, monkeypatch):
    monkeypatch.setattr(
        cr, "validate_credential",
        lambda *a, **k: _result(cr.INVALID_CREDENTIAL, via="GET /models"),
    )
    r = account_api.client.post(
        "/api/providers/openai/accounts/keys",
        json={"api_key": _SECRET, "validate": True},
    )
    assert r.status_code == 400
    assert r.json()["check"]["result"] == "rejected"
    assert r.json()["check"]["fix_url"] == "https://platform.openai.com/api-keys"
    assert not _read_cfg("openai").get("enabled")


def test_use_an_environment_key_without_sending_it(account_api, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", _SECRET)
    seen = {}

    def fake(provider, **k):
        seen["key"] = k.get("api_key")
        return _result(cr.VALID_NO_BALANCE, detail="no credits")

    monkeypatch.setattr(cr, "validate_credential", fake)
    r = account_api.client.post("/api/providers/deepseek/accounts/env", json={"env_var": "DEEPSEEK_API_KEY"})
    assert r.status_code == 200 and r.json()["check"]["result"] == "no_credit"
    assert seen["key"] == _SECRET and _SECRET not in r.text
    cred = account_api.store.find_pool("deepseek", "default").credentials[0]
    assert cred.payload.auth_value == _SECRET and cred.source == "env:DEEPSEEK_API_KEY"
    assert account_api.client.post("/api/providers/deepseek/accounts/env",
                                   json={"env_var": "OPENAI_API_KEY"}).status_code == 400
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert account_api.client.post("/api/providers/xai/accounts/env",
                                   json={"env_var": "XAI_API_KEY"}).status_code == 404


def test_validate_answers_the_same_check(account_api, monkeypatch):
    _put(account_api.store, status="unknown")
    monkeypatch.setattr(cr, "validate_credential",
                        lambda *a, **k: _result(cr.VALID_NO_BALANCE, detail="no credits"))
    body = account_api.client.post("/api/providers/openai/accounts/work/validate").json()
    assert body["check"]["result"] == "no_credit"
    assert body["check"]["fix_url"] == "https://platform.openai.com/settings/organization/billing/overview"
