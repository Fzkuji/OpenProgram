import json
from openprogram.auth._migrate_payload import migrate_payload_dict, migrate_store
from openprogram.auth.store import AuthStore
from openprogram.auth.types import CREDENTIAL_SCHEMA_VERSION


def test_migrate_api_key():
    out = migrate_payload_dict({"api_key": "sk-x", "__type__": "ApiKeyPayload"})
    assert out == {"kind": "api_key", "auth_value": "sk-x",
                   "base_url": "", "headers": {}, "data": {}}


def test_migrate_oauth_moves_extras_into_data():
    out = migrate_payload_dict({
        "access_token": "at", "refresh_token": "rt", "expires_at_ms": 9,
        "scope": ["a"], "client_id": "c", "token_endpoint": "t",
        "id_token": "id", "extra": {"email": "e"}, "__type__": "OAuthPayload",
    })
    assert out["kind"] == "oauth"
    assert out["auth_value"] == "at"
    assert out["data"]["refresh_token"] == "rt"
    assert out["data"]["expires_at_ms"] == 9
    assert out["data"]["extra"] == {"email": "e"}
    assert "access_token" not in out


def test_migrate_cli_delegated_empty_auth_value():
    out = migrate_payload_dict({
        "store_path": "/p", "access_key_path": ["access_token"],
        "refresh_key_path": ["refresh_token"], "expires_key_path": ["expiry_date"],
        "__type__": "CliDelegatedPayload",
    })
    assert out["kind"] == "cli_delegated"
    assert out["auth_value"] == ""
    assert out["data"]["store_path"] == "/p"
    assert out["data"]["access_key_path"] == ["access_token"]


def test_migrate_idempotent_on_new_structure():
    new = {"kind": "api_key", "auth_value": "k", "base_url": "",
           "headers": {}, "data": {}}
    assert migrate_payload_dict(new) == new


def test_migrate_store_rewrites_files_and_skips_admin(tmp_path):
    auth = tmp_path / "auth"
    (auth / "openai").mkdir(parents=True)
    cred_file = auth / "openai" / "default.json"
    cred_file.write_text(json.dumps({
        "v": 1, "provider_id": "openai", "profile_id": "default",
        "kind": "api_key", "credential_id": "cred_1",
        "payload": {"api_key": "sk-x", "__type__": "ApiKeyPayload"},
        "credentials": [{
            "v": 1, "provider_id": "openai", "profile_id": "default",
            "kind": "api_key", "credential_id": "cred_1",
            "payload": {"api_key": "sk-x", "__type__": "ApiKeyPayload"},
        }],
    }))
    (auth / "_rotation.json").write_text(json.dumps({"enabled": {}}))

    n = migrate_store(root=tmp_path)
    assert n == 1
    got = json.loads(cred_file.read_text())
    assert got["credentials"][0]["payload"]["kind"] == "api_key"
    assert "__type__" not in got["credentials"][0]["payload"]
    # admin file untouched
    assert json.loads((auth / "_rotation.json").read_text()) == {"enabled": {}}


def test_migrate_store_leaves_corrupt_version_alone(tmp_path):
    """A credential whose payload is already new-format (no __type__) but
    carries an unknown/future schema version must NOT have its payload
    rewritten — the migrator only upgrades genuine old-format payloads.
    Otherwise it would silently mask corruption that Credential.from_dict
    is meant to reject."""
    auth = tmp_path / "auth" / "openai"
    auth.mkdir(parents=True)
    cred_file = auth / "default.json"
    cred_file.write_text(json.dumps({
        "v": 3, "provider_id": "openai", "account_id": "default",
        "kind": "api_key", "credential_id": "cred_1",
        "credentials": [{
            "v": 99, "provider_id": "openai", "account_id": "default",
            "kind": "api_key", "credential_id": "cred_1",
            # already new-format payload, no __type__
            "payload": {"kind": "api_key", "auth_value": "k",
                        "base_url": "", "headers": {}, "data": {}},
        }],
    }))
    n = migrate_store(root=tmp_path)
    assert n == 0, "must not rewrite an already-current file"
    got = json.loads(cred_file.read_text())
    assert got["credentials"][0]["v"] == 99, "corrupt version must be left intact"


# ---- v2 → v3 on-disk renames ----------------------------------------------

def _v2_pool(kind="api_key", payload_kind="api_key"):
    """A pool document exactly as a v2 install left it on disk: flat
    payload (no __type__), old ``profile_id`` field, old kind value."""
    return {
        "provider_id": "openai", "profile_id": "work",
        "strategy": "fill_first",
        "credentials": [{
            "v": 2, "provider_id": "openai", "profile_id": "work",
            "kind": kind, "credential_id": "cred_1",
            "payload": {"kind": payload_kind, "auth_value": "sk-x",
                        "base_url": "", "headers": {}, "data": {}},
        }],
    }


def test_migrate_renames_external_process_kind(tmp_path):
    """Old stores say kind="external_process"; the current runtime only
    understands "credential_process". Both the credential and its nested
    payload carry the kind, so both must be rewritten."""
    auth = tmp_path / "auth" / "openai"
    auth.mkdir(parents=True)
    f = auth / "work.json"
    f.write_text(json.dumps(_v2_pool("external_process", "external_process")))

    assert migrate_store(root=tmp_path) == 1
    got = json.loads(f.read_text())
    assert got["credentials"][0]["kind"] == "credential_process"
    assert got["credentials"][0]["payload"]["kind"] == "credential_process"
    assert got["credentials"][0]["v"] == CREDENTIAL_SCHEMA_VERSION


def test_migrate_renames_profile_id_to_account_id(tmp_path):
    """``profile_id`` became ``account_id`` on the pool and on every
    credential. The old key must be gone, not merely shadowed."""
    auth = tmp_path / "auth" / "openai"
    auth.mkdir(parents=True)
    f = auth / "work.json"
    f.write_text(json.dumps(_v2_pool()))

    assert migrate_store(root=tmp_path) == 1
    got = json.loads(f.read_text())
    assert got["account_id"] == "work"
    assert "profile_id" not in got
    assert got["credentials"][0]["account_id"] == "work"
    assert "profile_id" not in got["credentials"][0]


def test_migrated_v2_store_loads_through_the_store(tmp_path):
    """The point of the migration: a v2 store on disk must come back as
    usable objects, with the renamed kind and account reaching the caller."""
    auth = tmp_path / "auth" / "openai"
    auth.mkdir(parents=True)
    (auth / "work.json").write_text(
        json.dumps(_v2_pool("external_process", "external_process")))

    store = AuthStore(root=tmp_path)
    pool = store.find_pool("openai", "work")
    assert pool is not None, "v2 pool must survive the migration"
    assert pool.account_id == "work"
    assert pool.credentials[0].kind == "credential_process"
    assert pool.credentials[0].payload.kind == "credential_process"


def test_migrate_is_idempotent_on_current_shape(tmp_path):
    """Re-running against an already-migrated store rewrites nothing."""
    auth = tmp_path / "auth" / "openai"
    auth.mkdir(parents=True)
    f = auth / "work.json"
    f.write_text(json.dumps(_v2_pool("external_process", "external_process")))

    assert migrate_store(root=tmp_path) == 1
    first = f.read_text()
    assert migrate_store(root=tmp_path) == 0
    assert f.read_text() == first
