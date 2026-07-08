from openprogram.auth.types import Credential, CredentialData
from openprogram.auth.resolver import resolve_connection, ResolvedConnection


def _cred(payload):
    return Credential(provider_id="p", profile_id="default",
                      kind=payload.kind, payload=payload)


def test_api_key_with_base_url():
    conn = resolve_connection(_cred(CredentialData(
        kind="api_key", auth_value="sk-x", base_url="https://bailian/v1")))
    assert conn == ResolvedConnection(
        kind="api_key", auth_value="sk-x",
        base_url="https://bailian/v1", headers={})


def test_api_key_no_base_url_yields_none_base():
    conn = resolve_connection(_cred(CredentialData(kind="api_key", auth_value="k")))
    assert conn.base_url is None  # empty "" → None so wire falls back to catalog


def test_oauth_uses_access_token_as_auth_value():
    conn = resolve_connection(_cred(CredentialData(
        kind="oauth", auth_value="at", data={"refresh_token": "rt"})))
    assert conn.kind == "oauth"
    assert conn.auth_value == "at"


def test_external_process_returns_none():
    conn = resolve_connection(_cred(CredentialData(kind="external_process")))
    assert conn is None
