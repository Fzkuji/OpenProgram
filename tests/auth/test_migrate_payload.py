import json
from openprogram.auth._migrate_payload import migrate_payload_dict, migrate_store


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
    carries an unknown/future schema version must NOT be touched — the
    migrator only upgrades genuine old-format payloads. Otherwise it would
    silently mask corruption that Credential.from_dict is meant to reject."""
    auth = tmp_path / "auth" / "openai"
    auth.mkdir(parents=True)
    cred_file = auth / "default.json"
    cred_file.write_text(json.dumps({
        "v": 2, "provider_id": "openai", "profile_id": "default",
        "kind": "api_key", "credential_id": "cred_1",
        "credentials": [{
            "v": 99, "provider_id": "openai", "profile_id": "default",
            "kind": "api_key", "credential_id": "cred_1",
            # already new-format payload, no __type__
            "payload": {"kind": "api_key", "auth_value": "k",
                        "base_url": "", "headers": {}, "data": {}},
        }],
    }))
    n = migrate_store(root=tmp_path)
    assert n == 0, "must not rewrite a file with no old-format payload"
    got = json.loads(cred_file.read_text())
    assert got["credentials"][0]["v"] == 99, "corrupt version must be left intact"
