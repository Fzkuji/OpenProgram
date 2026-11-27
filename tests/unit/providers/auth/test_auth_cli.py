"""The ``openprogram providers`` command tree: ``keys <provider> ...``, ``login``
(= ``keys add``), ``logout``, ``list``, ``status``, ``aliases``, ``doctor``.
They change the same list the Providers page does (models/listing/manage.py)."""
from __future__ import annotations

import argparse
import io
import json
import sys

import pytest

from openprogram.interfaces.cli.commands.providers import build_parser, dispatch
from openprogram.models.auth import selection
from openprogram.models.auth.credential_provider import CredentialProvider, set_credential_provider_for_testing
from openprogram.models.auth.store import AuthStore, set_store_for_testing
from openprogram.models.auth.types import Credential, CredentialData


@pytest.fixture
def isolated(tmp_path, monkeypatch, capsys):
    from openprogram.models import listing as _model_listing

    store = AuthStore(root=tmp_path / "store")
    set_store_for_testing(store)
    set_credential_provider_for_testing(CredentialProvider(store=store))
    selection.forget_bindings()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "fake_codex"))
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_model_listing, "fetch_models_remote", lambda _provider: {})
    monkeypatch.setattr("getpass.getpass", _no_prompt)
    yield store, capsys
    set_store_for_testing(None)
    set_credential_provider_for_testing(None)


def _no_prompt(*_a, **_k):
    raise AssertionError("must not prompt")


def _parse(argv):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    p_providers = sub.add_parser("providers")
    build_parser(p_providers.add_subparsers(dest="providers_cmd"))
    return parser.parse_args(["providers", *argv])


def _run(argv, stdin: str | None = None, monkeypatch=None) -> int:
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return dispatch(_parse(argv))


def _keys(store, provider="openai"):
    return [(c.label, c.payload.auth_value) for c in store.get(provider).credentials]


def _put(store, label, value, provider="openai"):
    return store.add_credential(Credential(
        provider_id=provider, kind="api_key", label=label,
        payload=CredentialData(kind="api_key", auth_value=value))).id


# ---- adding ---------------------------------------------------------------

def test_add_reads_the_key_from_stdin(isolated, monkeypatch):
    store, cap = isolated
    rc = _run(["keys", "openai", "add", "--api-key-stdin", "--no-check", "--name", "Work"],
              "sk-stdin-0000AAAA\n", monkeypatch)
    assert rc == 0
    assert _keys(store) == [("Work", "sk-stdin-0000AAAA")]
    out = cap.readouterr().out
    assert "Added Work to openai." in out and "sk-stdin" not in out
    assert store.get("openai").credentials[0].source == "cli"


def test_login_is_keys_add_and_resolves_an_alias(isolated, monkeypatch):
    store, _ = isolated
    assert _run(["login", "claude", "--api-key-stdin", "--no-check"], "sk-ant-api-key-0001", monkeypatch) == 0
    assert _keys(store, "anthropic") == [("", "sk-ant-api-key-0001")]
    assert not (store.base_dir() / "claude").exists()


def test_a_piped_key_is_read_without_flags(isolated, monkeypatch):
    store, _ = isolated
    assert _run(["keys", "openai", "add", "--no-check"], "sk-piped-0000CCCC\n", monkeypatch) == 0
    assert _keys(store) == [("", "sk-piped-0000CCCC")]


def test_an_empty_key_saves_nothing(isolated, monkeypatch):
    store, cap = isolated
    assert _run(["keys", "openai", "add", "--api-key-stdin"], "", monkeypatch) == 1
    assert "No key given" in cap.readouterr().err
    assert _keys(store) == []


def test_a_withdrawn_provider_cannot_be_added(isolated, monkeypatch):
    _, cap = isolated
    assert _run(["keys", "openai-codex", "add"], "", monkeypatch) == 1
    assert "cannot be added here" in cap.readouterr().err


def test_a_way_the_provider_does_not_offer_is_refused(isolated, monkeypatch):
    _, cap = isolated
    assert _run(["keys", "openai", "add", "--method", "import_from_cli"], "", monkeypatch) == 1
    assert "no way to connect" in cap.readouterr().err


def test_a_rejected_key_is_not_kept(isolated, monkeypatch):
    from types import SimpleNamespace
    from openprogram.models.listing import credentials as cr
    store, cap = isolated
    monkeypatch.setattr(cr, "validate_credential", lambda *a, **k: SimpleNamespace(
        status=cr.INVALID_CREDENTIAL, ok=False, kind="openai_bearer", via="GET /models", detail="no",
        to_dict=lambda: {"status": cr.INVALID_CREDENTIAL}))
    assert _run(["keys", "openai", "add", "--api-key", "sk-bad-0000DDDD"]) == 1
    assert "Not saved" in cap.readouterr().err
    assert _keys(store) == []


# ---- the list -------------------------------------------------------------

def test_list_marks_the_one_in_use(isolated):
    store, cap = isolated
    _put(store, "Work", "sk-work-00001111")
    _put(store, "", "sk-spare-0002222")
    assert _run(["keys", "openai", "list"]) == 0
    out = cap.readouterr().out
    assert "openai — in-order" in out
    assert " * 1. Work" in out and "   2. Key ····2222" in out
    assert "sk-work" not in out


def test_list_json_is_the_web_shape(isolated):
    from openprogram.models.listing.manage import provider_credentials
    store, cap = isolated
    _put(store, "Work", "sk-work-00001111")
    assert _run(["keys", "openai", "list", "--json"]) == 0
    assert json.loads(cap.readouterr().out) == json.loads(json.dumps(provider_credentials("openai")))


def test_every_provider_list_and_empty_hint(isolated):
    store, cap = isolated
    assert _run(["list"]) == 0
    assert "providers keys <provider> add" in cap.readouterr().out
    _put(store, "A", "sk-aaaa-00001111")
    _put(store, "D", "sk-dddd-00002222", provider="deepseek")
    assert _run(["list", "--json"]) == 0
    assert sorted(p["provider"] for p in json.loads(cap.readouterr().out)) == ["deepseek", "openai"]


# ---- changing it ----------------------------------------------------------

def test_name_id_or_number_picks_the_key(isolated):
    from openprogram.interfaces.cli.commands.providers.keys import resolve_name
    store, _ = isolated
    a = _put(store, "Work", "sk-work-00001111")
    b = _put(store, "Spare", "sk-spare-0002222")
    assert resolve_name("openai", "work").id == a
    assert resolve_name("openai", b).id == b
    assert resolve_name("openai", "2").id == b
    assert resolve_name("openai", "3") is None


def test_rename_move_disable_and_strategy(isolated):
    store, _ = isolated
    _put(store, "Work", "sk-work-00001111")
    _put(store, "Spare", "sk-spare-0002222")
    assert _run(["keys", "openai", "rename", "Work", "Office"]) == 0
    assert _run(["keys", "openai", "move", "Spare", "--up"]) == 0
    assert [c.label for c in store.get("openai").credentials] == ["Spare", "Office"]
    assert _run(["keys", "openai", "move", "1", "--to", "2"]) == 0
    assert [c.label for c in store.get("openai").credentials] == ["Office", "Spare"]
    assert _run(["keys", "openai", "disable", "Office"]) == 0
    assert store.get("openai").credentials[0].enabled is False
    assert _run(["keys", "openai", "enable", "Office"]) == 0
    assert store.get("openai").credentials[0].enabled is True
    assert _run(["keys", "openai", "strategy", "least-used"]) == 0
    assert store.get("openai").strategy == "least_used"


def test_replace_keeps_name_and_place(isolated, monkeypatch):
    store, _ = isolated
    cid = _put(store, "Work", "sk-work-00001111")
    _put(store, "Spare", "sk-spare-0002222")
    monkeypatch.setattr("openprogram.models.listing.manage._probe_or_none", lambda *a, **k: None)
    assert _run(["keys", "openai", "replace", "Work", "--api-key-stdin"], "sk-new-00003333", monkeypatch) == 0
    first = store.get("openai").credentials[0]
    assert (first.id, first.label, first.payload.auth_value) == (cid, "Work", "sk-new-00003333")


def test_remove_needs_yes_without_a_terminal(isolated, monkeypatch):
    store, cap = isolated
    _put(store, "Work", "sk-work-00001111")
    assert _run(["keys", "openai", "remove", "Work"], "", monkeypatch) == 1
    assert "--yes" in cap.readouterr().err
    assert _run(["keys", "openai", "remove", "Work", "--yes"]) == 0
    assert _keys(store) == []
    assert _run(["keys", "openai", "remove", "Work", "--yes"]) == 1


def test_an_environment_key_is_removed_by_unsetting_it(isolated, monkeypatch):
    store, cap = isolated
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-00004444")
    assert _run(["keys", "openai", "remove", "1", "--yes"]) == 1
    assert "Unset OPENAI_API_KEY" in cap.readouterr().err
    assert [c.id for c in store.get("openai").credentials] == ["env:OPENAI_API_KEY"]


def test_logout_removes_every_stored_key(isolated):
    store, cap = isolated
    _put(store, "A", "sk-aaaa-00001111")
    _put(store, "B", "sk-bbbb-00002222")
    assert _run(["logout", "openai", "--yes"]) == 0
    assert _keys(store) == []
    assert _run(["logout", "openai", "--yes"]) == 0


def test_status_of_a_provider_resolves_an_alias(isolated):
    store, cap = isolated
    _put(store, "Mine", "sk-ant-api-0005555", provider="anthropic")
    assert _run(["status", "claude"]) == 0
    assert "anthropic" in cap.readouterr().out


# ---- other verbs ----------------------------------------------------------

def test_aliases(isolated):
    _, cap = isolated
    assert _run(["aliases", "--json"]) == 0
    body = json.loads(cap.readouterr().out)
    assert body["bedrock"] == "amazon-bedrock" and body["claude"] == "anthropic"


def test_doctor_on_an_empty_store(isolated):
    _, cap = isolated
    assert _run(["doctor", "--json"]) == 0
    body = json.loads(cap.readouterr().out)
    assert body["pools_checked"] == 0
    assert "no_pools" in {f["code"] for f in body["findings"]}


def test_the_removed_verbs_are_gone(isolated):
    for argv in (["use", "openai", "work"], ["migrate"], ["accounts"]):
        with pytest.raises(SystemExit):
            _parse(argv)


def test_bare_providers_command_lists(isolated, monkeypatch):
    from openprogram.interfaces.cli import application

    _, cap = isolated
    monkeypatch.setattr(sys, "argv", ["openprogram", "providers"])
    with pytest.raises(SystemExit) as exited:
        application.main()
    assert exited.value.code in (0, None)
    assert "No keys or sign-ins yet" in cap.readouterr().out


def test_a_sign_in_lists_its_account_and_plan_once(isolated):
    store, cap = isolated
    store.add_credential(Credential(provider_id="anthropic", kind="import", source="import",
                                    identity={"email": "a@example.com", "plan": "prolite"},
                                    payload=CredentialData(kind="import", auth_value="", data={})))
    assert _run(["keys", "anthropic", "list"]) == 0
    out = cap.readouterr().out
    assert "a@example.com (Pro Lite)" in out
    assert "import" in out and out.count("import") == 1, "the source is not repeated after the kind"


def test_the_verdict_says_why_a_key_was_not_confirmed():
    from openprogram.interfaces.cli.commands.providers.keys import _verdict
    assert _verdict({"result": "works", "detail": "ok"}) == "works"
    assert _verdict({"result": "unverified", "detail": "HTTP 500."}) == "not confirmed (HTTP 500.)"
    assert _verdict({"result": "not_configured"}) == "the provider is not set up here, so the key was not checked"
    assert _verdict({"result": "works", "detail": "Key accepted; the test message failed: HTTP 400."}).endswith(
        "(Key accepted; the test message failed: HTTP 400.)")
