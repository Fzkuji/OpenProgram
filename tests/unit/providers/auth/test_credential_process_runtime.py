"""The credential_process helper actually runs on the resolver's path.

Every test here uses a throwaway helper script in ``tmp_path``. Nothing
reads the real keychain, the user's home, or any configured provider.
"""
from __future__ import annotations

import stat
import textwrap

import pytest

from openprogram.auth.methods.credential_process import (
    clear_token_cache,
    token_for_payload,
)
from openprogram.auth.resolver import resolve_connection
from openprogram.auth.types import (
    AuthCredentialProcessError,
    Credential,
    CredentialData,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_token_cache()
    yield
    clear_token_cache()


def _script(tmp_path, name: str, body: str) -> str:
    """Write an executable sh helper and return its path."""
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _cred(data: dict, *, account: str = "default") -> Credential:
    return Credential(
        provider_id="helperco", account_id=account,
        kind="credential_process",
        payload=CredentialData(kind="credential_process", data=data),
    )


# ---- happy paths ----------------------------------------------------------

def test_json_parse_walks_key_path(tmp_path):
    helper = _script(tmp_path, "h.sh", """
        echo '{"creds": {"token": "sk-from-json"}}'
    """)
    conn = resolve_connection(_cred({
        "command": [helper],
        "parses": "json",
        "json_key_path": ["creds", "token"],
    }))
    assert conn is not None
    assert conn.auth_value == "sk-from-json"
    assert conn.kind == "credential_process"


def test_text_parse_strips_output(tmp_path):
    helper = _script(tmp_path, "h.sh", """
        echo '  sk-raw-text  '
    """)
    conn = resolve_connection(_cred({"command": [helper], "parses": "text"}))
    assert conn.auth_value == "sk-raw-text"


def test_json_without_key_path_requires_string_root(tmp_path):
    helper = _script(tmp_path, "h.sh", """
        echo '"sk-bare-string"'
    """)
    conn = resolve_connection(_cred({"command": [helper], "parses": "json"}))
    assert conn.auth_value == "sk-bare-string"


# ---- cache window ---------------------------------------------------------

def _counting_helper(tmp_path, counter, *, token="sk-1"):
    return _script(tmp_path, "count.sh", f"""
        echo x >> '{counter.as_posix()}'
        echo '{token}'
    """)


def _forks(counter) -> int:
    return len(counter.read_text().splitlines()) if counter.exists() else 0


def test_cache_window_forks_helper_once(tmp_path):
    counter = tmp_path / "runs"
    helper = _counting_helper(tmp_path, counter)
    data = {"command": [helper], "parses": "text", "cache_seconds": 300}
    for _ in range(5):
        assert resolve_connection(_cred(data)).auth_value == "sk-1"
    assert _forks(counter) == 1


def test_cache_seconds_zero_disables_caching(tmp_path):
    counter = tmp_path / "runs"
    helper = _counting_helper(tmp_path, counter)
    data = {"command": [helper], "parses": "text", "cache_seconds": 0}
    resolve_connection(_cred(data))
    resolve_connection(_cred(data))
    assert _forks(counter) == 2


def test_cache_is_scoped_per_profile(tmp_path):
    counter = tmp_path / "runs"
    helper = _counting_helper(tmp_path, counter)
    data = {"command": [helper], "parses": "text", "cache_seconds": 300}
    resolve_connection(_cred(data, account="work"))
    resolve_connection(_cred(data, account="personal"))
    assert _forks(counter) == 2


def test_cache_expires(tmp_path, monkeypatch):
    counter = tmp_path / "runs"
    helper = _counting_helper(tmp_path, counter)
    data = {"command": [helper], "parses": "text", "cache_seconds": 1}

    clock = {"t": 1000.0}
    monkeypatch.setattr(
        "openprogram.auth.methods.credential_process.time.monotonic",
        lambda: clock["t"],
    )
    resolve_connection(_cred(data))
    clock["t"] += 0.5
    resolve_connection(_cred(data))
    assert _forks(counter) == 1
    clock["t"] += 1.0          # past the 1 s window
    resolve_connection(_cred(data))
    assert _forks(counter) == 2


# ---- failure surfaces, never falls back ----------------------------------

def test_nonzero_exit_raises_with_stderr(tmp_path):
    helper = _script(tmp_path, "bad.sh", """
        echo 'corporate vpn is down' >&2
        exit 3
    """)
    with pytest.raises(AuthCredentialProcessError) as e:
        resolve_connection(_cred({"command": [helper], "parses": "text"}))
    assert "exited 3" in str(e.value)
    assert "corporate vpn is down" in str(e.value)


def test_stderr_secrets_are_redacted_in_error_and_logs(tmp_path, caplog):
    """A failing helper often echoes the very token it was fetching."""
    import logging

    secret = "ghp_" + "A1b2C3d4E5f6G7h8"
    helper = _script(tmp_path, "leaky.sh", f"""
        echo 'request failed: Authorization: Bearer sk-live-abcdefghij0123456789' >&2
        echo 'api_key={secret}' >&2
        exit 4
    """)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(AuthCredentialProcessError) as e:
            resolve_connection(_cred({"command": [helper], "parses": "text"}))

    message = str(e.value)
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for leaked in (secret, "sk-live-abcdefghij0123456789"):
        assert leaked not in message
        assert leaked not in logged
    assert "exited 4" in message


def test_missing_executable_raises(tmp_path):
    with pytest.raises(AuthCredentialProcessError):
        resolve_connection(_cred({"command": [str(tmp_path / "nope")]}))


def test_timeout_raises(tmp_path):
    helper = _script(tmp_path, "slow.sh", """
        sleep 5
    """)
    with pytest.raises(AuthCredentialProcessError) as e:
        resolve_connection(_cred({
            "command": [helper], "parses": "text", "timeout_seconds": 0.3,
        }))
    assert "timed out" in str(e.value)


def test_bad_json_raises(tmp_path):
    helper = _script(tmp_path, "h.sh", """
        echo 'not json at all'
    """)
    with pytest.raises(AuthCredentialProcessError) as e:
        resolve_connection(_cred({"command": [helper], "parses": "json"}))
    assert "not valid JSON" in str(e.value)


def test_missing_json_key_path_raises(tmp_path):
    helper = _script(tmp_path, "h.sh", """
        echo '{"other": "x"}'
    """)
    with pytest.raises(AuthCredentialProcessError):
        resolve_connection(_cred({
            "command": [helper], "parses": "json", "json_key_path": ["token"],
        }))


def test_failure_is_not_cached(tmp_path):
    counter = tmp_path / "runs"
    helper = _script(tmp_path, "bad.sh", f"""
        echo x >> '{counter.as_posix()}'
        exit 1
    """)
    data = {"command": [helper], "parses": "text", "cache_seconds": 300}
    for _ in range(2):
        with pytest.raises(AuthCredentialProcessError):
            resolve_connection(_cred(data))
    assert _forks(counter) == 2


def test_resolve_api_key_sync_does_not_swallow_helper_failure(tmp_path, monkeypatch):
    """The whole point of the fix: the funnel must not fall back silently."""
    from openprogram.auth import resolver as resolver_mod

    helper = _script(tmp_path, "bad.sh", "exit 9")
    cred = _cred({"command": [helper], "parses": "text"})

    class FakeManager:
        def acquire_sync(self, provider_id, account_id=None):
            return cred

    monkeypatch.setattr(resolver_mod, "get_credential_provider", lambda: FakeManager())
    monkeypatch.setattr(resolver_mod, "get_active_account", lambda p: "default")
    monkeypatch.setattr(resolver_mod, "get_credential_override", lambda p: None)

    with pytest.raises(AuthCredentialProcessError):
        resolver_mod.resolve_api_key_sync("helperco")


# ---- env / cwd passthrough ------------------------------------------------

def test_env_and_cwd_reach_the_helper(tmp_path):
    workdir = tmp_path / "wd"
    workdir.mkdir()
    helper = _script(tmp_path, "h.sh", """
        printf '%s' "$MY_TOKEN"
    """)
    conn = resolve_connection(_cred({
        "command": [helper], "parses": "text",
        "env": {"MY_TOKEN": "sk-from-env"},
        "cwd": str(workdir),
    }))
    assert conn.auth_value == "sk-from-env"


def test_token_for_payload_runs_inside_a_running_loop(tmp_path):
    """Resolver is sync but is called from async front-ends too."""
    import asyncio

    helper = _script(tmp_path, "h.sh", "echo sk-loop")

    async def main():
        return token_for_payload({"command": [helper], "parses": "text"})

    assert asyncio.run(main()) == "sk-loop"
