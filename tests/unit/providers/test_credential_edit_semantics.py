"""Behavior tests for omit/replace/delete secret editing and true deletion."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# --- masks and sentinels are never stored as real values -------------------


def test_merge_secret_map_rejects_a_bare_mask_written_back() -> None:
    from fastapi import HTTPException

    from openprogram.webui.routes._credential_secrets import mask_credential
    from openprogram.webui.routes.mcp import _merge_secret_map

    real = "sk-abcdefghijklmnop"
    masked = mask_credential(real)

    with pytest.raises(HTTPException) as caught:
        _merge_secret_map({"API_KEY": real}, {"API_KEY": masked})

    assert caught.value.status_code == 400


def test_merge_secret_map_rejects_short_value_mask() -> None:
    from fastapi import HTTPException

    from openprogram.webui.routes.mcp import _merge_secret_map

    with pytest.raises(HTTPException) as caught:
        _merge_secret_map({"API_KEY": "short"}, {"API_KEY": "•" * 8})

    assert caught.value.status_code == 400


def test_merge_secret_map_rejects_redacted_sentinels() -> None:
    from fastapi import HTTPException

    from openprogram.webui.routes.mcp import _merge_secret_map

    for sentinel in ("REDACTED", "<redacted>", "[redacted]", "***REDACTED***"):
        with pytest.raises(HTTPException) as caught:
            _merge_secret_map({"API_KEY": "real"}, {"API_KEY": sentinel})
        assert caught.value.status_code == 400, sentinel


def test_merge_secret_map_keeps_omit_replace_delete_semantics() -> None:
    from openprogram.webui.routes.mcp import _merge_secret_map

    stored = {"KEEP": "a", "SWAP": "b", "DROP": "c"}

    merged = _merge_secret_map(stored, {"SWAP": "new", "DROP": ""})

    assert merged == {"KEEP": "a", "SWAP": "new"}


def test_merge_auth_rejects_masked_token_and_client_secret() -> None:
    from fastapi import HTTPException

    from openprogram.webui.routes._credential_secrets import mask_credential
    from openprogram.webui.routes.mcp import _merge_auth

    token = "tok-abcdefghijklmnop"
    secret = "cs-abcdefghijklmnop"
    stored = {"kind": "oauth", "token": token, "client_secret": secret}

    with pytest.raises(HTTPException) as caught:
        _merge_auth(
            stored,
            {
                "kind": "oauth",
                "token": mask_credential(token),
                "client_secret": mask_credential(secret),
            },
        )

    assert caught.value.status_code == 400


def test_merge_auth_still_replaces_and_deletes_real_values() -> None:
    from openprogram.webui.routes.mcp import _merge_auth

    stored = {"kind": "oauth", "token": "old", "client_secret": "keep"}

    replaced = _merge_auth(stored, {"kind": "oauth", "token": "brand-new"})
    assert replaced["token"] == "brand-new"
    assert replaced["client_secret"] == "keep"

    deleted = _merge_auth(stored, {"kind": "oauth", "token": ""})
    assert "token" not in deleted


# --- no raw secret editor, no reveal ---------------------------------------


def test_mcp_cli_has_no_raw_file_editor() -> None:
    """The product must not hand the raw secret file to $EDITOR."""
    from openprogram.cli.commands import mcp as mcp_cmd

    assert not hasattr(mcp_cmd, "_cmd_mcp_edit")


def test_mcp_edit_verb_reports_the_structured_alternative(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from openprogram.cli.commands.mcp import _cmd_mcp_edit_removed

    code = _cmd_mcp_edit_removed()

    error = capsys.readouterr().err
    assert code == 1
    # Points at the structured replacements, not at the raw secret file.
    assert "openprogram mcp add" in error
    assert "mcp_servers.json" not in error


def test_legacy_account_key_reveal_returns_stable_deprecation() -> None:
    from openprogram.webui.routes.accounts import reveal_deprecation_response

    payload, status = reveal_deprecation_response()

    assert status == 410
    assert payload == {"error": "credential reveal is no longer supported"}
    assert "value" not in payload


# --- deletion truthfulness --------------------------------------------------


def test_channel_delete_reports_failure_when_files_survive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    from openprogram.channels import accounts

    folder = tmp_path / "channels" / "slack" / "accounts" / "acct"
    folder.mkdir(parents=True)
    (folder / "credentials.json").write_text('{"token": "xoxb-real"}')
    monkeypatch.setattr(accounts, "account_dir", lambda c, a: folder)

    def failing_rmtree(*args: object, **kwargs: object) -> None:
        return None  # pretend success, leave the tree in place

    monkeypatch.setattr(shutil, "rmtree", failing_rmtree)

    with pytest.raises(OSError):
        accounts.delete("slack", "acct")


def test_channel_delete_succeeds_and_verifies_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.channels import accounts

    folder = tmp_path / "channels" / "slack" / "accounts" / "acct"
    folder.mkdir(parents=True)
    (folder / "credentials.json").write_text('{"token": "xoxb-real"}')
    monkeypatch.setattr(accounts, "account_dir", lambda c, a: folder)

    accounts.delete("slack", "acct")

    assert not folder.exists()


def test_channel_delete_is_idempotent_when_already_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.channels import accounts

    folder = tmp_path / "channels" / "slack" / "accounts" / "gone"
    monkeypatch.setattr(accounts, "account_dir", lambda c, a: folder)

    accounts.delete("slack", "gone")  # absence is the goal, not an error


def test_deleting_a_credential_clears_the_runtime_token_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deleted credential must not keep answering from process memory."""
    from openprogram.auth.methods import credential_process
    from openprogram.auth.store import AuthStore
    from openprogram.auth.types import Credential, CredentialData

    store = AuthStore(root=tmp_path / "auth")
    store.add_credential(
        Credential(
            provider_id="openai", account_id="default", kind="api_key",
            payload=CredentialData(kind="api_key", auth_value="sk-live"),
        )
    )
    credential_process._token_cache[("openai/default", ("helper",), "raw", (), None)] = (
        float("inf"),
        "cached-secret",
    )

    store.delete_pool("openai", "default")

    assert credential_process._token_cache == {}


def test_removing_one_credential_clears_the_runtime_token_cache(
    tmp_path: Path,
) -> None:
    from openprogram.auth.methods import credential_process
    from openprogram.auth.store import AuthStore
    from openprogram.auth.types import Credential, CredentialData

    store = AuthStore(root=tmp_path / "auth")
    cred = Credential(
        provider_id="openai", account_id="default", kind="api_key",
        payload=CredentialData(kind="api_key", auth_value="sk-live"),
    )
    store.add_credential(cred)
    credential_process._token_cache[("openai/default", ("helper",), "raw", (), None)] = (
        float("inf"),
        "cached-secret",
    )

    store.remove_credential("openai", "default", cred.credential_id)

    assert credential_process._token_cache == {}


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
def test_channel_delete_surfaces_the_underlying_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused removal raises rather than reporting a phantom success."""
    import shutil

    from openprogram.channels import accounts

    folder = tmp_path / "accounts" / "acct"
    folder.mkdir(parents=True)
    (folder / "credentials.json").write_text('{"token": "xoxb-real"}')
    monkeypatch.setattr(accounts, "account_dir", lambda c, a: folder)

    def denied(path: object, ignore_errors: bool = False, **kwargs: object) -> None:
        if ignore_errors:
            return  # the swallowing call reports nothing, as it always did
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(shutil, "rmtree", denied)

    with pytest.raises(PermissionError):
        accounts.delete("slack", "acct")
    assert (folder / "credentials.json").exists()
