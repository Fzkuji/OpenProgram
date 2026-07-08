import json
from openprogram.auth.store import AuthStore


def test_store_migrates_old_payload_on_first_load(tmp_path):
    auth = tmp_path / "auth" / "openai"
    auth.mkdir(parents=True)
    (auth / "default.json").write_text(json.dumps({
        "v": 1, "provider_id": "openai", "profile_id": "default",
        "kind": "api_key", "credential_id": "cred_1",
        "credentials": [{
            "v": 1, "provider_id": "openai", "profile_id": "default",
            "kind": "api_key", "credential_id": "cred_1",
            "payload": {"api_key": "sk-old", "__type__": "ApiKeyPayload"},
        }],
    }))
    store = AuthStore(root=tmp_path)
    pool = store.find_pool("openai", "default")
    assert pool is not None
    cred = pool.credentials[0]
    assert cred.payload.kind == "api_key"
    assert cred.payload.auth_value == "sk-old"
