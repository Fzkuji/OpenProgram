from openprogram.auth.types import (
    Credential, CredentialData, CREDENTIAL_SCHEMA_VERSION,
)


def test_credential_data_defaults():
    d = CredentialData(kind="api_key", auth_value="sk-x")
    assert d.kind == "api_key"
    assert d.auth_value == "sk-x"
    assert d.base_url == ""
    assert d.headers == {}
    assert d.data == {}


def test_credential_roundtrip_api_key():
    cred = Credential(
        provider_id="openai", profile_id="default", kind="api_key",
        payload=CredentialData(kind="api_key", auth_value="sk-x",
                               base_url="https://ep/v1"),
    )
    back = Credential.from_dict(cred.to_dict())
    assert isinstance(back.payload, CredentialData)
    assert back.payload.kind == "api_key"
    assert back.payload.auth_value == "sk-x"
    assert back.payload.base_url == "https://ep/v1"


def test_credential_roundtrip_oauth_data():
    cred = Credential(
        provider_id="openai-codex", profile_id="default", kind="oauth",
        payload=CredentialData(
            kind="oauth", auth_value="at",
            data={"refresh_token": "rt", "expires_at_ms": 123,
                  "client_id": "cid", "token_endpoint": "https://t"},
        ),
    )
    back = Credential.from_dict(cred.to_dict())
    assert back.payload.data["refresh_token"] == "rt"
    assert back.payload.data["expires_at_ms"] == 123


def test_payload_dict_has_no_type_discriminator():
    from openprogram.auth.types import _payload_to_dict
    d = _payload_to_dict(CredentialData(kind="api_key", auth_value="k"))
    assert "__type__" not in d
    assert d["kind"] == "api_key"
