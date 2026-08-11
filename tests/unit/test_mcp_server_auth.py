from __future__ import annotations

import importlib
import os
import re
import stat
import traceback
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest


def _auth():
    return importlib.import_module("openprogram.mcp_server.auth")


def _write_private(path, value: str) -> None:
    path.write_text(value, encoding="ascii")
    path.chmod(0o600)


def test_token_path_uses_independent_state_file(tmp_path, monkeypatch):
    paths = importlib.import_module("openprogram.paths")
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)

    assert _auth().token_path() == tmp_path / "mcp_server_token"


def test_create_token_creates_parent_private_file_and_fsyncs(tmp_path, monkeypatch):
    auth = _auth()
    target = tmp_path / "new" / "profile" / "mcp_server_token"
    fsynced = []
    real_fsync = auth.os.fsync

    def recording_fsync(fd):
        fsynced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(auth.os, "fsync", recording_fsync)
    token = auth.create_token(target)

    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
    assert target.read_text(encoding="ascii") == token
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert fsynced
    assert not list(target.parent.glob(".mcp_server_token.*.tmp"))


def test_create_token_requests_32_bytes_and_sanitizes_generator_failure(
    tmp_path, monkeypatch
):
    auth = _auth()
    requested = []

    def fail_generation(size):
        requested.append(size)
        raise OSError("generator included secret-material")

    monkeypatch.setattr(auth.secrets, "token_urlsafe", fail_generation)

    with pytest.raises(auth.MCPTokenError, match="^could not create MCP server token$"):
        auth.create_token(tmp_path / "mcp_server_token")

    assert requested == [32]


def test_create_token_never_uses_replace(tmp_path, monkeypatch):
    auth = _auth()

    def fail_replace(*_args, **_kwargs):
        raise AssertionError("replace must not be used for token publication")

    monkeypatch.setattr(auth.os, "replace", fail_replace)
    target = tmp_path / "mcp_server_token"

    token = auth.create_token(target)

    assert target.read_text(encoding="ascii") == token


def test_create_token_refuses_existing_file_without_overwrite(tmp_path):
    auth = _auth()
    target = tmp_path / "mcp_server_token"
    _write_private(target, "existing-private-token")

    with pytest.raises(auth.MCPTokenError, match="already exists") as caught:
        auth.create_token(target)

    assert target.read_text(encoding="ascii") == "existing-private-token"
    assert "existing-private-token" not in str(caught.value)
    assert not list(tmp_path.glob(".mcp_server_token.*.tmp"))


def test_create_token_refuses_existing_symlink_without_touching_target(tmp_path):
    auth = _auth()
    destination = tmp_path / "destination"
    destination.write_text("do-not-touch", encoding="ascii")
    target = tmp_path / "mcp_server_token"
    target.symlink_to(destination)

    with pytest.raises(auth.MCPTokenError, match="already exists"):
        auth.create_token(target)

    assert target.is_symlink()
    assert destination.read_text(encoding="ascii") == "do-not-touch"


def test_create_token_removes_its_published_inode_if_final_verification_fails(
    tmp_path, monkeypatch
):
    auth = _auth()
    target = tmp_path / "mcp_server_token"

    def fail_permissions(_fd, _mode):
        raise OSError("permission update failed")

    monkeypatch.setattr(auth.os, "fchmod", fail_permissions)

    with pytest.raises(auth.MCPTokenError, match="^could not create MCP server token$"):
        auth.create_token(target)

    assert not target.exists()
    assert not list(tmp_path.glob(".mcp_server_token.*.tmp"))


def test_real_concurrent_creators_publish_exactly_one_token(tmp_path):
    auth = _auth()
    target = tmp_path / "mcp_server_token"
    workers = 12
    barrier = Barrier(workers)

    def create():
        barrier.wait()
        try:
            return "created", auth.create_token(target)
        except auth.MCPTokenError as exc:
            return "refused", str(exc)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(lambda _index: create(), range(workers)))

    created = [value for status, value in outcomes if status == "created"]
    refused = [value for status, value in outcomes if status == "refused"]
    assert len(created) == 1
    assert refused == ["MCP server token already exists"] * (workers - 1)
    assert target.read_text(encoding="ascii") == created[0]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".mcp_server_token.*.tmp"))


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("missing", "MCP server token file is unavailable or invalid"),
        ("directory", "MCP server token file is unavailable or invalid"),
        ("wrong-mode", "MCP server token file is unavailable or invalid"),
        ("symlink", "MCP server token file is unavailable or invalid"),
    ],
)
def test_authentication_rejects_unsafe_or_missing_paths(tmp_path, setup, expected):
    auth = _auth()
    target = tmp_path / "mcp_server_token"
    if setup == "directory":
        target.mkdir()
    elif setup == "wrong-mode":
        target.write_text("stored-token", encoding="ascii")
        target.chmod(0o644)
    elif setup == "symlink":
        source = tmp_path / "source"
        _write_private(source, "stored-token")
        target.symlink_to(source)

    with pytest.raises(auth.MCPTokenError, match=f"^{expected}$"):
        auth.authenticate_from_environment(
            {auth.MCP_TOKEN_ENV: "stored-token"}, path=target
        )


def test_authentication_rejects_file_not_owned_by_current_user(tmp_path, monkeypatch):
    auth = _auth()
    target = tmp_path / "mcp_server_token"
    _write_private(target, "stored-token")
    real_uid = os.geteuid()
    monkeypatch.setattr(auth.os, "geteuid", lambda: real_uid + 1)

    with pytest.raises(
        auth.MCPTokenError,
        match="^MCP server token file is unavailable or invalid$",
    ):
        auth.authenticate_from_environment(
            {auth.MCP_TOKEN_ENV: "stored-token"}, path=target
        )


def test_authentication_sanitizes_unreadable_file_failure(tmp_path, monkeypatch):
    auth = _auth()
    target = tmp_path / "mcp_server_token"
    _write_private(target, "stored-token")
    real_open = auth.os.open

    def denied(path, *args, **kwargs):
        if os.fspath(path) == os.fspath(target):
            raise PermissionError("filesystem included stored-token")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(auth.os, "open", denied)
    with pytest.raises(
        auth.MCPTokenError,
        match="^MCP server token file is unavailable or invalid$",
    ) as caught:
        auth.authenticate_from_environment(
            {auth.MCP_TOKEN_ENV: "stored-token"}, path=target
        )

    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.tb)
    )
    assert "stored-token" not in str(caught.value)
    assert "stored-token" not in repr(caught.value)
    assert "stored-token" not in rendered


def test_authentication_requires_environment_without_mutating_it(tmp_path):
    auth = _auth()
    target = tmp_path / "mcp_server_token"
    _write_private(target, "stored-token")
    environ = {"UNRELATED": "kept"}

    with pytest.raises(
        auth.MCPTokenError,
        match="^OPENPROGRAM_MCP_TOKEN is required$",
    ):
        auth.authenticate_from_environment(environ, path=target)

    assert environ == {"UNRELATED": "kept"}


def test_authentication_uses_compare_digest_and_returns_stable_fingerprint(
    tmp_path, monkeypatch
):
    auth = _auth()
    target = tmp_path / "mcp_server_token"
    _write_private(target, "stored-token")
    environ = {auth.MCP_TOKEN_ENV: "presented-token", "OTHER": "unchanged"}
    compared = []

    def accept(left, right):
        compared.append((left, right))
        return True

    monkeypatch.setattr(auth.hmac, "compare_digest", accept)

    client_id = auth.authenticate_from_environment(environ, path=target)

    assert client_id == "6f69975abe580db3"
    assert compared == [("stored-token", "presented-token")]
    assert environ == {
        auth.MCP_TOKEN_ENV: "presented-token",
        "OTHER": "unchanged",
    }


def test_authentication_mismatch_is_stable_and_never_exposes_tokens(tmp_path, caplog):
    auth = _auth()
    target = tmp_path / "mcp_server_token"
    stored = "stored-secret-material"
    presented = "presented-secret-material"
    _write_private(target, stored)

    with pytest.raises(
        auth.MCPTokenError,
        match="^MCP server authentication failed$",
    ) as caught:
        auth.authenticate_from_environment({auth.MCP_TOKEN_ENV: presented}, path=target)

    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.tb)
    )
    for secret in (stored, presented):
        assert secret not in str(caught.value)
        assert secret not in repr(caught.value)
        assert secret not in rendered
        assert secret not in caplog.text


def test_authentication_non_ascii_mismatch_is_the_same_sanitized_failure(tmp_path):
    auth = _auth()
    target = tmp_path / "mcp_server_token"
    _write_private(target, "stored-token")

    with pytest.raises(
        auth.MCPTokenError,
        match="^MCP server authentication failed$",
    ):
        auth.authenticate_from_environment(
            {auth.MCP_TOKEN_ENV: "presented-secret-凭证"}, path=target
        )
