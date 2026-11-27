"""What the carry-over now writes, and the repair of lists an earlier one
wrote (openprogram/models/auth/carry_over.py): no stored key is ever lost
or emptied (a variable holding the same key is shown once at read time), a
spent balance is not a refused key, a sign-in names its account, folders
are private and a read leaves no folder behind."""
from __future__ import annotations

import base64
import json
import os
import stat
import time
from pathlib import Path

import pytest

from openprogram.models.auth import carry_over as co
from openprogram.models.auth.store import AuthStore

ENV_VALUE = "sk-openai-env-value-0001"


def _write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"e30.{body}.sig"


def _old(cid: str, value: str, **extra) -> dict:
    return {"credential_id": cid, "kind": "api_key", "source": "web",
            "payload": {"kind": "api_key", "auth_value": value}, **extra}


def _rows(auth: Path, provider: str) -> list[dict]:
    return json.loads((auth / provider / "credentials.json").read_text())["credentials"]


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.parametrize("variable", [None, ENV_VALUE, "sk-openai-other-0009"])
def test_an_environment_key_with_a_stored_copy_stays_a_stored_key(tmp_path, monkeypatch, variable):
    """Whatever the starting process's environment holds (nothing, the same
    key, another key), the stored copy is kept: the result does not depend
    on which process carried the list over."""
    if variable is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", variable)
    auth = tmp_path / "auth"
    _write(auth / "openai" / "default.json", {"provider_id": "openai", "account_id": "default", "credentials": [
        _old("env", ENV_VALUE, source="env", status="needs_reauth", last_error="401: Key rejected"),
        _old("k1", ENV_VALUE),
        _old("k2", "sk-openai-own-key-0002")]})
    co.carry_over(auth, state_root=tmp_path)
    rows = _rows(auth, "openai")
    assert len(rows) == 3 and rows[1]["id"] == "k1" and rows[2]["id"] == "k2"
    carried = rows[0]
    assert carried["id"] not in ("env", "env:OPENAI_API_KEY") and carried["source"] == "cli"
    assert carried["payload"]["auth_value"] == ENV_VALUE and not carried["read_only"]
    assert carried["status"] == "invalid_key", "it is the same key A, so its own refusal stays"


def test_an_environment_key_of_a_provider_with_no_variable_is_kept(tmp_path, monkeypatch):
    """ark and minimax-cp read no variable: their old env rows stay keys."""
    monkeypatch.setattr("openprogram.models.auth.login.login_method_registry.env_vars", lambda provider: [])
    auth = tmp_path / "auth"
    _write(auth / "ark" / "default.json", {"provider_id": "ark", "account_id": "default", "credentials": [
        _old("env", "ark-key-value-0001", source="env")]})
    report = co.carry_over(auth, state_root=tmp_path)
    (row,) = _rows(auth, "ark")
    assert row["payload"]["auth_value"] == "ark-key-value-0001"
    assert report.before == {"ark": 1} and report.after == {"ark": 1}


def test_an_environment_row_with_no_stored_value_is_left_out_and_logged(tmp_path, caplog):
    auth = tmp_path / "auth"
    _write(auth / "openai" / "default.json", {"provider_id": "openai", "account_id": "default", "credentials": [
        _old("env", "", source="env"), _old("k2", "sk-openai-own-key-0002")]})
    with caplog.at_level("INFO", logger=co.__name__):
        report = co.carry_over(auth, state_root=tmp_path)
    assert [r["id"] for r in _rows(auth, "openai")] == ["k2"]
    assert report.before == {"openai": 2}, "before counts the old file's rows"
    assert any("openai row env" in r.getMessage() and "left out" in r.getMessage() for r in caplog.records)


def test_a_stored_key_a_variable_also_holds_is_listed_once_and_outlives_it(tmp_path, monkeypatch):
    from openprogram.models.auth import Credential, CredentialData

    monkeypatch.setenv("OPENAI_API_KEY", ENV_VALUE)
    store = AuthStore(root=tmp_path)
    kept = store.add_credential(Credential(provider_id="openai", kind="api_key", source="cli",
                                           payload=CredentialData(kind="api_key", auth_value=ENV_VALUE)))
    assert [c.id for c in store.get("openai").credentials] == [kept.id]
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-other-0009")
    assert sorted(c.id for c in store.get("openai").credentials) == sorted([kept.id, "env:OPENAI_API_KEY"])
    monkeypatch.delenv("OPENAI_API_KEY")
    assert [c.id for c in store.get("openai").credentials] == [kept.id]


def test_adding_a_key_a_variable_holds_stores_it(tmp_path, monkeypatch):
    from openprogram.models.listing import manage
    from openprogram.models.auth.store import set_store_for_testing

    monkeypatch.setenv("OPENAI_API_KEY", ENV_VALUE)
    store = AuthStore(root=tmp_path)
    set_store_for_testing(store)
    try:
        out = manage.add_api_key("openai", ENV_VALUE, validate=False)
    finally:
        set_store_for_testing(None)
    keys = [c.payload.auth_value for c in store.get("openai").credentials if c.source != "env"]
    assert out["ok"] and keys == [ENV_VALUE]
    assert [c.id for c in store.get("openai").credentials] == [out["credential"]["id"]], "listed once"


def test_an_old_error_without_a_code_keeps_its_sentence_as_the_message(tmp_path):
    auth = tmp_path / "auth"
    _write(auth / "yuanheng" / "default.json", {"provider_id": "yuanheng", "account_id": "default", "credentials": [
        _old("y1", "sk-yh-0001", status="needs_reauth", updated_at_ms=5,
             last_error="Key rejected (HTTP 401). Re-check the key or re-login.")]})
    co.carry_over(auth, state_root=tmp_path)
    error = _rows(auth, "yuanheng")[0]["last_error"]
    assert error == {"code": "invalid_key", "message": "Key rejected (HTTP 401). Re-check the key or re-login.",
                     "at": 5}


def test_a_spent_balance_with_no_time_is_carried_ready_and_402_counts_only_as_a_status(tmp_path):
    auth = tmp_path / "auth"
    _write(auth / "opencode" / "default.json", {"provider_id": "opencode", "account_id": "default", "credentials": [
        _old("o1", "sk-oc-0001", status="needs_reauth", last_error="401: CreditsError: insufficient balance"),
        _old("o2", "sk-oc-0002", status="needs_reauth", updated_at_ms=int(time.time() * 1000),
             last_error="401: key sk-402-abc is not valid")]})
    co.carry_over(auth, state_root=tmp_path)
    first, second = _rows(auth, "opencode")
    assert (first["status"], first["cooldown_until_ms"]) == ("ready", 0)
    assert second["status"] == "invalid_key"
    assert co._is_billing("HTTP 402 Payment Required") and co._is_billing("402: payment required")
    assert not co._is_billing("key sk-402-abc refused")


def test_a_sign_in_takes_its_plan_from_its_own_id_token(tmp_path):
    auth = tmp_path / "auth"
    token = _jwt({"email": "a@example.com", "https://api.openai.com/auth": {"chatgpt_plan_type": "prolite"}})
    _write(auth / "qwen" / "default.json", {"provider_id": "qwen", "account_id": "default", "credentials": [
        {"credential_id": "o1", "kind": "oauth", "source": "web", "metadata": {"email": "a@example.com"},
         "payload": {"kind": "oauth", "auth_value": "AT", "data": {"id_token": token}}}]})
    co.carry_over(auth, state_root=tmp_path)
    assert _rows(auth, "qwen")[0]["identity"] == {"email": "a@example.com", "plan": "prolite"}


def test_a_spent_balance_answered_with_401_carries_over_as_out_of_quota(tmp_path):
    auth = tmp_path / "auth"
    now_ms = int(time.time() * 1000)
    _write(auth / "opencode" / "default.json", {"provider_id": "opencode", "account_id": "default", "credentials": [
        _old("o1", "sk-oc-0001", status="needs_reauth", updated_at_ms=now_ms,
             last_error="401: CreditsError: insufficient balance")]})
    co.carry_over(auth, state_root=tmp_path)
    row = _rows(auth, "opencode")[0]
    assert row["status"] == "quota"
    assert row["cooldown_until_ms"] > now_ms


def test_an_imported_sign_in_names_its_account_from_the_id_token(tmp_path):
    store = tmp_path / "qwen" / "oauth_creds.json"
    _write(store, {"tokens": {"id_token": _jwt({"email": "a@example.com",
                                                "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"}})}})
    auth = tmp_path / "auth"
    _write(auth / "qwen" / "default.json", {"provider_id": "qwen", "account_id": "default", "credentials": [
        {"credential_id": "c1", "kind": "cli_delegated", "source": "qwen_cli", "read_only": True,
         "payload": {"kind": "cli_delegated", "data": {"store_path": str(store)}}}]})
    co.carry_over(auth, state_root=tmp_path)
    assert _rows(auth, "qwen")[0]["identity"] == {"email": "a@example.com", "plan": "plus"}


def _carried(tmp_path: Path, rows: list[dict], provider: str = "openai") -> Path:
    """A list an earlier carry-over wrote, with its finished backup."""
    auth = tmp_path / "auth"
    _write(auth / provider / "credentials.json", {"provider_id": provider, "strategy": "in_order", "credentials": rows})
    (auth / co.BACKUP_DIR).mkdir(parents=True)
    (auth / co.BACKUP_DIR / ".complete").write_text("x")
    return auth


def _row(rid: str, value: str = "", **extra) -> dict:
    return {"id": rid, "provider_id": "openai", "kind": "api_key", "label": "", "source": "web", "identity": {},
            "enabled": True, "status": "ready", "cooldown_until_ms": 0, "blocked_models": {}, "last_error": None,
            "read_only": False, "payload": {"kind": "api_key", "auth_value": value}, **extra}


@pytest.mark.parametrize("variable", [None, ENV_VALUE])
def test_repair_keeps_every_stored_key_and_its_last_good_copy(tmp_path, monkeypatch, variable):
    """The rows an earlier carry-over stored under id ``env`` (ark,
    minimax-cp, opencode-go on real data) keep their key and get an id of
    their own; a stored copy of a set variable stays too."""
    if variable is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", variable)
    auth = _carried(tmp_path, [_row("env", ENV_VALUE, source="import"), _row("dup", ENV_VALUE),
                               _row("own", "sk-openai-own-key-0002")])
    bak = auth / "openai" / "credentials.json.bak"
    bak.write_text((auth / "openai" / "credentials.json").read_text())
    assert co.repair(auth, state_root=tmp_path) == ["openai"]
    rows = _rows(auth, "openai")
    assert [r["payload"]["auth_value"] for r in rows] == [ENV_VALUE, ENV_VALUE, "sk-openai-own-key-0002"]
    assert rows[0]["id"].startswith("cred") and rows[0]["source"] == "cli"
    assert bak.is_file(), "nothing left the list, so its last good copy stays"
    assert (auth / co.BACKUP_DIR / co.REPAIR_MARK).is_file()
    assert co.repair(auth, state_root=tmp_path) == [], "it runs once"


def test_repair_keeps_an_environment_row_whose_provider_has_no_variable(tmp_path, monkeypatch):
    monkeypatch.setattr("openprogram.models.auth.login.login_method_registry.env_vars", lambda provider: [])
    auth = _carried(tmp_path, [_row("env", "ark-key-value-0001", source="import")], provider="ark")
    co.repair(auth, state_root=tmp_path)
    (row,) = _rows(auth, "ark")
    assert row["payload"]["auth_value"] == "ark-key-value-0001" and row["id"] != "env"


def test_repair_finds_a_spent_balance_in_the_last_good_copy(tmp_path):
    """A later key check wrote "Key rejected" over OpenCode's CreditsError."""
    now_ms = int(time.time() * 1000)
    refused = {"code": "invalid_key", "message": "Key rejected (HTTP 401)", "at": now_ms}
    spent = {"code": "401", "message": "CreditsError: insufficient balance", "at": now_ms}
    auth = _carried(tmp_path, [_row("o1", "sk-oc-0001", status="invalid_key", last_error=refused)],
                    provider="opencode")
    _write(auth / "opencode" / "credentials.json.bak", {"credentials": [
        _row("o1", "sk-oc-0001", status="invalid_key", last_error=spent)]})
    co.repair(auth, state_root=tmp_path)
    row = _rows(auth, "opencode")[0]
    assert row["status"] == "quota" and row["last_error"]["message"].startswith("CreditsError")


def test_repair_turns_a_sentence_carried_as_a_code_into_the_message(tmp_path):
    sentence = "Key rejected (HTTP 401). Re-check the key or re-login."
    auth = _carried(tmp_path, [_row("y1", "sk-yh-0001", status="invalid_key",
                                    last_error={"code": sentence, "message": sentence, "at": 1})])
    co.repair(auth, state_root=tmp_path)
    assert _rows(auth, "openai")[0]["last_error"]["code"] == "invalid_key"


def test_repair_marks_itself_done_when_folders_cannot_be_tightened(tmp_path, monkeypatch):
    auth = _carried(tmp_path, [_row("own", "sk-own-0002")])

    def refuse(path, mode):
        raise PermissionError(path)
    monkeypatch.setattr(co.os, "chmod", refuse)
    co.repair(auth, state_root=tmp_path)
    assert (auth / co.BACKUP_DIR / co.REPAIR_MARK).is_file()


def test_a_lock_another_process_holds_is_not_removed(tmp_path):
    import fcntl

    from openprogram.state.private_files import _private_file_lock, drop_unused_lock

    lock = tmp_path / "p" / "credentials.json.lock"
    lock.parent.mkdir()
    lock.write_text("")
    fd = os.open(lock, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert not drop_unused_lock(lock, tmp_path / "p" / "credentials.json")
        assert lock.exists()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert drop_unused_lock(lock, tmp_path / "p" / "credentials.json")
    with _private_file_lock(tmp_path / "p" / "credentials.json", root=tmp_path):
        assert _mode(lock) == 0o600, "a lock file is owner-only"


def test_repair_frees_a_balance_carried_as_a_refused_key(tmp_path):
    now_ms = int(time.time() * 1000)
    auth = _carried(tmp_path, [_row("o1", "sk-oc-0001", status="invalid_key",
                                    last_error={"code": "401", "message": "CreditsError: insufficient balance", "at": now_ms})],
                    provider="opencode")
    co.repair(auth, state_root=tmp_path)
    row = _rows(auth, "opencode")[0]
    assert row["status"] == "quota" and row["cooldown_until_ms"] > now_ms


def test_repair_leaves_a_list_that_needs_nothing_untouched(tmp_path):
    auth = _carried(tmp_path, [_row("own", "sk-own-0002")])
    before = (auth / "openai" / "credentials.json").read_bytes()
    assert co.repair(auth, state_root=tmp_path) == []
    assert (auth / "openai" / "credentials.json").read_bytes() == before


def test_repair_makes_folders_private_and_drops_lock_only_ones(tmp_path):
    auth = _carried(tmp_path, [_row("own", "sk-own-0002")])
    (auth / "stale-provider").mkdir()
    (auth / "stale-provider" / "credentials.json.lock").write_text("")
    os.chmod(auth / "openai", 0o755)
    co.repair(auth, state_root=tmp_path)
    assert not (auth / "stale-provider").exists()
    assert _mode(auth) == 0o700 and _mode(auth / "openai") == 0o700


def test_reading_a_provider_with_no_list_creates_nothing(tmp_path):
    store = AuthStore(root=tmp_path)
    assert store.get("nobody").credentials == []
    assert not (store.base_dir() / "nobody").exists()


def test_a_new_list_lives_in_private_folders(tmp_path):
    from openprogram.models.auth import Credential, CredentialData

    store = AuthStore(root=tmp_path)
    store.add_credential(Credential(provider_id="openai", kind="api_key", source="cli",
                                    payload=CredentialData(kind="api_key", auth_value="sk-new-0001")))
    assert _mode(store.base_dir()) == 0o700
    assert _mode(store.base_dir() / "openai") == 0o700


def test_a_row_is_named_by_its_label_email_variable_tail_or_sign_in():
    from openprogram.models.auth import Credential, CredentialData

    def name(**fields):
        payload = fields.pop("payload", CredentialData(kind="api_key", auth_value="sk-abcd-0000wxyz"))
        return Credential(provider_id=fields.pop("provider", "openai"), kind=fields.pop("kind", "api_key"),
                          payload=payload, **fields).display_label()

    assert name(label="Work") == "Work"
    assert name(kind="oauth", identity={"email": "a@example.com"},
                payload=CredentialData(kind="oauth", auth_value="A", data={})) == "a@example.com"
    assert name(source="env", metadata={"env_var": "OPENAI_API_KEY"},
                payload=CredentialData(kind="api_key", auth_value="")) == "$OPENAI_API_KEY"
    assert name() == "Key ····wxyz"
    assert name(provider="claude-code", kind="oauth",
                payload=CredentialData(kind="oauth", auth_value="A", data={})) == "Claude Code sign-in"


def test_an_id_token_names_the_account_and_plan(tmp_path):
    from openprogram.models.auth.account.account_labels import file_identity, token_identity

    token = _jwt({"email": "a@example.com", "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"}})
    assert token_identity(token) == {"email": "a@example.com", "plan": "pro"}
    assert token_identity("not-a-token") == {}
    _write(tmp_path / "auth.json", {"id_token": token})
    assert file_identity(tmp_path / "auth.json") == {"email": "a@example.com", "plan": "pro"}
    assert file_identity(tmp_path / "missing.json") == {}


def test_a_sign_in_is_not_held_to_a_base_address_and_a_missing_file_says_so(tmp_path):
    from openprogram.models.auth import Credential, CredentialData
    from openprogram.models.listing.manage import credential_row

    def imported(path):
        return Credential(provider_id="google-gemini-cli", kind="import", source="import", read_only=True,
                          payload=CredentialData(kind="import", data={"store_path": str(path)}))

    assert credential_row(imported(tmp_path / "gone.json"), set_up=False)["status"] == "no_file"
    _write(tmp_path / "oauth_creds.json", {})
    assert credential_row(imported(tmp_path / "oauth_creds.json"), set_up=False)["status"] == "ready"
    key = Credential(provider_id="custom", kind="api_key", source="cli",
                     payload=CredentialData(kind="api_key", auth_value="sk-custom-0001"))
    assert credential_row(key, set_up=False)["status"] == "not_set_up"
