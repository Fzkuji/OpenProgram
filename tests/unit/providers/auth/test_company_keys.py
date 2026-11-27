"""An API key is stored once, for its company, and every way of the company
that takes a key sees it; a way's state on it is the way's own."""
from __future__ import annotations

import json

import pytest

from openprogram.models.auth import AuthStore, Credential, CredentialData, set_store_for_testing
from openprogram.models.auth.company_keys import COMPANY_MARK, key_ways

MINIMAX = ["minimax", "minimax-cn", "minimax-coding-plan", "minimax-cn-coding-plan"]
KEY = "sk-cp-0123456789abcdef"


@pytest.fixture
def store(tmp_path, monkeypatch):
    for name in ("MINIMAX_API_KEY", "MINIMAX_CN_API_KEY", "TAVILY_API_KEY", "XIAOMI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    s = AuthStore(root=tmp_path)
    set_store_for_testing(s)
    yield s
    set_store_for_testing(None)


def _key(provider: str, value: str = KEY) -> Credential:
    return Credential(provider_id=provider, kind="api_key",
                      payload=CredentialData(kind="api_key", auth_value=value), source="web")


def test_a_key_added_on_one_way_is_a_row_of_every_way(store):
    row = store.add_credential(_key("minimax-cn-coding-plan"))
    assert key_ways("minimax") == MINIMAX
    for way in MINIMAX:
        found = store.get(way).find(row.id)
        assert found is not None and found.payload.auth_value == KEY
    assert set(store.providers()) >= set(MINIMAX)


def test_the_secret_is_stored_once_in_the_company_file(store, tmp_path):
    row = store.add_credential(_key("minimax"))
    store.update("minimax-cn", row.id, lambda c: setattr(c, "status", "invalid_key"))
    assert [k["value"] for k in store.company_keys("minimax")] == [KEY]
    for path in (tmp_path / "auth").glob("*/credentials.json"):
        assert KEY not in path.read_text()
    saved = json.loads((tmp_path / "auth" / "minimax-cn" / "credentials.json").read_text())
    assert saved["credentials"][0]["metadata"][COMPANY_MARK] == "minimax"


def test_a_way_marks_only_its_own_row(store):
    row = store.add_credential(_key("minimax"))
    store.update("minimax", row.id, lambda c: setattr(c, "status", "invalid_key"))
    assert store.get("minimax").find(row.id).status == "invalid_key"
    assert store.get("minimax-cn").find(row.id).status == "ready"


def test_the_same_key_again_is_one_key(store):
    a = store.add_credential(_key("minimax"))
    b = store.add_credential(_key("minimax-cn"))
    assert a.id == b.id and len(store.company_keys("minimax")) == 1


def test_a_rename_or_replace_is_the_keys(store):
    row = store.add_credential(_key("minimax"))
    store.update("minimax-cn", row.id, lambda c: setattr(c, "label", "China plan"))
    assert store.get("minimax-coding-plan").find(row.id).label == "China plan"
    store.update("minimax", row.id, lambda c: setattr(c.payload, "auth_value", "sk-cp-new-0123456789"))
    assert store.get("minimax-cn").find(row.id).payload.auth_value == "sk-cp-new-0123456789"


def test_removing_a_key_takes_it_off_every_way(store):
    row = store.add_credential(_key("minimax"))
    assert store.remove_credential("minimax-cn", row.id)
    assert all(store.get(w).find(row.id) is None for w in MINIMAX)
    assert store.company_keys("minimax") == []


def test_a_variable_is_a_row_of_every_way_that_reads_it(store, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", KEY)
    assert all(store.get(w).find("env:MINIMAX_API_KEY") is not None for w in MINIMAX)
    store.add_credential(_key("minimax"))
    # The variable holds a stored key: that key's row is the one listed.
    assert all(store.get(w).find("env:MINIMAX_API_KEY") is None for w in MINIMAX)


def test_a_key_with_its_own_address_stays_the_ways(store):
    cred = _key("minimax")
    cred.payload.base_url = "https://proxy.example/v1"
    row = store.add_credential(cred)
    assert store.company_keys("minimax") == []
    assert store.get("minimax-cn").find(row.id) is None


def test_keys_in_way_files_move_to_their_company(tmp_path, monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    for way in ("minimax", "minimax-cn"):
        folder = tmp_path / "auth" / way
        folder.mkdir(parents=True)
        cred = _key(way)
        cred.id = f"cred_{way}"
        (folder / "credentials.json").write_text(json.dumps(
            {"v": 4, "provider_id": way, "strategy": "in_order", "cursor": 0, "credentials": [cred.to_dict()]}))
    store = AuthStore(root=tmp_path)
    keys = store.company_keys("minimax")
    assert [k["value"] for k in keys] == [KEY]
    assert store.get("minimax").find(keys[0]["id"]) is not None
    assert store.get("minimax-cn").find(keys[0]["id"]) is not None
    assert KEY not in (tmp_path / "auth" / "minimax-cn" / "credentials.json").read_text()


def test_config_keys_move_to_the_store(tmp_path, monkeypatch):
    from openprogram.state import config as user_config
    monkeypatch.setattr(user_config, "get_config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr("openprogram.models.auth.key_move._state_dir", lambda: tmp_path)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    (tmp_path / "config.json").write_text(json.dumps(
        {"api_keys": {"TAVILY_API_KEY": "tvly-0123456789", "MINIMAX_API_KEY": KEY, "NOBODY_KEY": "x"},
         "tts": {"provider": "none"}}))
    store = AuthStore(root=tmp_path)
    set_store_for_testing(store)
    try:
        from openprogram.models.auth.services import service_key
        assert service_key("tavily") == "tvly-0123456789"
        assert service_key("minimax-search") == KEY
        assert store.get("minimax-cn-coding-plan").credentials[0].payload.auth_value == KEY
        cfg = json.loads((tmp_path / "config.json").read_text())
        assert cfg["api_keys"] == {"NOBODY_KEY": "x"} and cfg["tts"] == {"provider": "none"}
        import os
        assert "TAVILY_API_KEY" not in os.environ
    finally:
        set_store_for_testing(None)


def test_a_service_reads_its_companys_key_then_the_variable(store, monkeypatch):
    from openprogram.models.auth.services import service_key
    assert service_key("tavily") == ""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-env")
    assert service_key("tavily") == "tvly-env"
    store.add_company_key("tavily", "tvly-stored")
    assert service_key("tavily") == "tvly-stored"
    # A tool service is no provider: its keys list no way.
    assert "tavily" not in store.providers()


def test_an_unlisted_catalogue_entry_joins_its_company_by_name():
    from openprogram.models.auth.login.login_method_registry import _place
    placed, of = _place([
        {"id": "minimax", "label": "MiniMax", "env_var": "MINIMAX_API_KEY"},
        {"id": "minimax-cn-max-plan", "label": "MiniMax Max Plan (China)", "env_var": "MINIMAX_API_KEY"},
        {"id": "munito", "label": "Munito", "env_var": "MUNITO_API_KEY"},
        {"id": "munito-eu", "label": "Munito EU", "env_var": "MUNITO_API_KEY"},
        {"id": "inference", "label": "Inference", "env_var": "INFERENCE_API_KEY"},
        {"id": "inference-coop", "label": "Inference Coop", "env_var": "COOP_API_KEY"},
    ])
    assert of["minimax-cn-max-plan"][0].id == "minimax"
    assert of["munito-eu"][0].id == of["munito"][0].id == "munito"
    # A shared prefix alone is not a company: these read different keys.
    assert "inference" not in of and "inference-coop" not in of


def test_a_refused_key_points_at_the_ways_that_have_not_refused_it(store):
    from openprogram.models.auth.company_keys import ways_for_refused_keys
    row = store.add_credential(_key("minimax"))
    assert ways_for_refused_keys("minimax") == []  # nothing refused: the request stays
    store.update("minimax", row.id, lambda c: setattr(c, "status", "invalid_key"))
    store.update("minimax-coding-plan", row.id, lambda c: setattr(c, "status", "invalid_key"))
    store.update("minimax-cn-coding-plan", row.id, lambda c: setattr(c, "use_count", 3))
    # A way the key has answered on comes before one it has not been tried on.
    keys = frozenset({row.id})
    assert ways_for_refused_keys("minimax") == [("minimax-cn-coding-plan", keys), ("minimax-cn", keys)]
    assert ways_for_refused_keys("minimax", exclude=frozenset({"minimax-cn-coding-plan"})) == [("minimax-cn", keys)]


def test_a_limit_moves_nothing(store):
    from openprogram.models.auth.company_keys import ways_for_refused_keys
    row = store.add_credential(_key("minimax"))
    store.update("minimax", row.id, lambda c: setattr(c, "status", "quota"))
    assert ways_for_refused_keys("minimax") == []


def test_a_request_refused_on_its_way_is_sent_on_another(store, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from openprogram.models.providers.streaming import routing
    from openprogram.models.providers.utils.failures.errors import ErrorReason, LLMError

    row = store.add_credential(_key("minimax"))
    store.update("minimax", row.id, lambda c: setattr(c, "status", "invalid_key"))
    monkeypatch.setattr("openprogram.models.providers.catalog.models.get_model",
                        lambda way, mid: SimpleNamespace(provider=way, id=mid) if way == "minimax-cn" else None)
    monkeypatch.setattr("openprogram.models.providers.catalog.enabled_models.use_model_in_process",
                        lambda way, mid: __import__("openprogram.models.providers.catalog.models", fromlist=["x"]).get_model(way, mid) is not None)
    monkeypatch.setattr(routing, "_said", set())
    sent: list = []

    async def send(model, keys):
        sent.append((model.provider, keys))
        if model.provider == "minimax":
            err = LLMError(message="no key", reason=ErrorReason.AUTHENTICATION, retryable=False,
                           provider="minimax", model=model.id)
            err.failure = {"cause": "credentials_unavailable", "pinned": None}
            raise err
        yield {"type": "start"}
        yield {"type": "done"}

    async def run():
        from openprogram.models.providers.types import AssistantMessage
        monkeypatch.setattr(AssistantMessage, "empty", classmethod(lambda cls, model: None), raising=False)
        monkeypatch.setattr("openprogram.models.providers.types.EventStart",
                            lambda partial: {"type": "start"})
        return [e async for e in routing.routed(SimpleNamespace(provider="minimax", id="MiniMax-M2"), send)]

    events = asyncio.run(run())
    kinds = [e["type"] if isinstance(e, dict) else e.type for e in events]
    # The request moved along its key and may use no other credential there.
    assert sent == [("minimax", None), ("minimax-cn", frozenset({row.id}))]
    # One start, the notice that names both ways, then the reply.
    assert kinds == ["start", "retry", "done"]
    assert "refused on MiniMax" in events[1].error


def test_another_failure_is_the_requests_own(store):
    import asyncio
    from types import SimpleNamespace
    from openprogram.models.providers.streaming import routing

    async def send(model, keys):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    async def run():
        return [e async for e in routing.routed(SimpleNamespace(provider="minimax", id="m"), send)]

    with pytest.raises(RuntimeError):
        asyncio.run(run())


def test_a_tool_reads_the_first_key_some_way_has_not_refused(store):
    from openprogram.models.auth.services import save_key, service_key
    first = store.add_credential(_key("minimax", "sk-cp-first-0123456789"))
    store.add_credential(_key("minimax", "sk-cp-second-0123456789"))
    assert service_key("minimax-search") == "sk-cp-first-0123456789"
    for way in MINIMAX:
        store.update(way, first.id, lambda c: setattr(c, "status", "invalid_key"))
    assert service_key("minimax-search") == "sk-cp-second-0123456789"
    # A key saved for the tool goes first; the others stay.
    save_key("MINIMAX_API_KEY", "sk-cp-third-0123456789")
    assert service_key("minimax-search") == "sk-cp-third-0123456789"
    assert len(store.company_keys("minimax")) == 3


def test_a_tool_services_key_is_replaced(store):
    from openprogram.models.auth.services import save_key
    save_key("TAVILY_API_KEY", "tvly-one-0123456789")
    save_key("TAVILY_API_KEY", "tvly-two-0123456789")
    assert [k["value"] for k in store.company_keys("tavily")] == ["tvly-two-0123456789"]


def test_the_persons_placement_comes_before_the_name():
    from openprogram.models.auth.login.login_method_registry import _place
    entries = [
        {"id": "minimax", "label": "MiniMax", "env_var": "MINIMAX_API_KEY"},
        {"id": "minimax-relay", "label": "MiniMax Relay", "env_var": "RELAY_API_KEY"},
        {"id": "acme", "label": "Acme", "env_var": "ACME_API_KEY"},
        {"id": "roadrunner", "label": "Roadrunner", "env_var": "RR_API_KEY"},
    ]
    _, of = _place(entries)
    assert of["minimax-relay"][0].id == "minimax" and "roadrunner" not in of
    _, of = _place(entries, {"minimax-relay": "minimax-relay", "roadrunner": "acme"})
    # By itself; and two unlisted entries made one company, named after the one pointed at.
    assert "minimax-relay" not in of
    assert of["roadrunner"][0].id == of["acme"][0].id == "acme"


def test_a_moved_request_uses_only_the_key_it_follows(store):
    from types import SimpleNamespace
    from openprogram.models.providers.streaming.credential_failover import _Run
    first = store.add_credential(_key("minimax", "sk-cp-own-0123456789"))
    other = store.add_credential(_key("minimax", "sk-cp-followed-0123456789"))
    opts = SimpleNamespace(session_id=None, only_credentials=frozenset({other.id}), credential=None)
    run = _Run("minimax-cn", SimpleNamespace(id="m", provider="minimax-cn"), "m", opts, lambda o: None)
    assert run.first.id == other.id and first.id in run.tried


def test_a_services_setting_is_kept_with_the_settings(tmp_path, monkeypatch):
    from openprogram.state import config as user_config
    monkeypatch.setattr(user_config, "get_config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr("openprogram.models.auth.key_move._state_dir", lambda: tmp_path)
    monkeypatch.delenv("GOOGLE_PSE_CX", raising=False)
    (tmp_path / "config.json").write_text(json.dumps({"api_keys": {"GOOGLE_PSE_CX": "engine-0123"}}))
    store = AuthStore(root=tmp_path)
    set_store_for_testing(store)
    try:
        from openprogram.models.auth.services import remove_key, save_key, setting
        cfg = json.loads((tmp_path / "config.json").read_text())
        assert "api_keys" not in cfg and cfg["search"]["google_pse_cx"] == "engine-0123"
        assert setting("GOOGLE_PSE_CX") == "engine-0123"
        save_key("GOOGLE_PSE_CX", "engine-4567")
        assert setting("GOOGLE_PSE_CX") == "engine-4567"
        assert remove_key("GOOGLE_PSE_CX") and setting("GOOGLE_PSE_CX") == ""
        monkeypatch.setenv("GOOGLE_PSE_CX", "engine-env")
        assert setting("GOOGLE_PSE_CX") == "engine-env"
    finally:
        set_store_for_testing(None)
