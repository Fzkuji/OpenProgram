from __future__ import annotations

import importlib
import os
import re
import stat
import tempfile
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest


def _auth():
    return importlib.import_module("openprogram.mcp.server.auth")


def _write_private(path, value: str) -> None:
    path.write_text(value, encoding="ascii")
    path.chmod(0o600)


def _symlink_to_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable for this Windows account: {exc}")


def _foreign_writable_temp() -> Path:
    if not hasattr(os, "geteuid"):
        pytest.skip("foreign-owner POSIX test is not meaningful on Windows")
    for candidate in (Path("/private/tmp"), Path(tempfile.gettempdir()).resolve()):
        try:
            info = candidate.stat()
        except OSError:
            continue
        if info.st_uid != os.geteuid() and os.access(candidate, os.W_OK | os.X_OK):
            return candidate
    pytest.skip("no writable foreign-owned temporary directory")


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
    if os.name != "nt":
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
    _symlink_to_or_skip(target, destination)

    with pytest.raises(auth.MCPTokenError, match="already exists"):
        auth.create_token(target)

    assert target.is_symlink()
    assert destination.read_text(encoding="ascii") == "do-not-touch"


@pytest.mark.parametrize("use_default_path", [False, True])
def test_create_token_rejects_real_symlink_ancestor(
    tmp_path, monkeypatch, use_default_path
):
    auth = _auth()
    actual = tmp_path / "actual"
    actual.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    _symlink_to_or_skip(linked, actual, directory=True)
    if use_default_path:
        paths = importlib.import_module("openprogram.paths")
        monkeypatch.setattr(paths, "get_state_dir", lambda: linked)
        selected_path = None
    else:
        selected_path = linked / "mcp_server_token"

    with pytest.raises(auth.MCPTokenError, match="^could not create MCP server token$"):
        auth.create_token(selected_path)

    assert not (actual / "mcp_server_token").exists()


def test_create_token_rejects_wide_parent_directory(tmp_path):
    auth = _auth()
    parent = tmp_path / "wide"
    parent.mkdir(mode=0o700)
    parent.chmod(0o755)
    target = parent / "mcp_server_token"

    if os.name == "nt":
        token = auth.create_token(target)
        assert target.read_text(encoding="ascii") == token
        return

    with pytest.raises(auth.MCPTokenError, match="^could not create MCP server token$"):
        auth.create_token(target)

    assert not target.exists()


def test_create_token_rejects_foreign_parent_directory():
    auth = _auth()
    parent = _foreign_writable_temp()
    target = parent / f".openprogram-mcp-create-{uuid.uuid4().hex}"
    try:
        with pytest.raises(
            auth.MCPTokenError, match="^could not create MCP server token$"
        ):
            auth.create_token(target)
        assert not target.exists()
    finally:
        target.unlink(missing_ok=True)


@pytest.mark.skipif(os.name == "nt", reason="Windows does not mutate POSIX mode")
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX dir_fd swap contract")
def test_create_token_cleans_temp_through_parent_fd_after_post_write_swap(
    tmp_path, monkeypatch
):
    auth = _auth()
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o700)
    replacement = tmp_path / "replacement"
    replacement.mkdir(mode=0o700)
    moved = tmp_path / "moved-parent"
    target = parent / "mcp_server_token"
    generated = "S" * 43
    real_fsync = auth.os.fsync
    swapped = False

    def fsync_then_swap(fd):
        nonlocal swapped
        real_fsync(fd)
        if not swapped:
            parent.rename(moved)
            parent.symlink_to(replacement, target_is_directory=True)
            swapped = True

    monkeypatch.setattr(auth.secrets, "token_urlsafe", lambda size: generated)
    monkeypatch.setattr(auth.os, "fsync", fsync_then_swap)

    with pytest.raises(auth.MCPTokenError, match="^could not create MCP server token$"):
        auth.create_token(target)

    assert parent.is_symlink()
    for directory in (moved, replacement):
        files = [entry for entry in directory.iterdir() if entry.is_file()]
        secret_bearing = [
            entry for entry in files if generated.encode("ascii") in entry.read_bytes()
        ]
        assert secret_bearing == []
        assert files == []


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
    if os.name != "nt":
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
        _symlink_to_or_skip(target, source)

    if setup == "wrong-mode" and os.name == "nt":
        assert auth.authenticate_from_environment(
            {auth.MCP_TOKEN_ENV: "stored-token"}, path=target
        ) == "6f69975abe580db3"
        return

    with pytest.raises(auth.MCPTokenError, match=f"^{expected}$"):
        auth.authenticate_from_environment(
            {auth.MCP_TOKEN_ENV: "stored-token"}, path=target
        )


@pytest.mark.parametrize("use_default_path", [False, True])
def test_authentication_rejects_real_symlink_ancestor(
    tmp_path, monkeypatch, use_default_path
):
    auth = _auth()
    actual = tmp_path / "actual"
    actual.mkdir(mode=0o700)
    _write_private(actual / "mcp_server_token", "stored-token")
    linked = tmp_path / "linked"
    _symlink_to_or_skip(linked, actual, directory=True)
    if use_default_path:
        paths = importlib.import_module("openprogram.paths")
        monkeypatch.setattr(paths, "get_state_dir", lambda: linked)
        selected_path = None
    else:
        selected_path = linked / "mcp_server_token"

    with pytest.raises(
        auth.MCPTokenError,
        match="^MCP server token file is unavailable or invalid$",
    ):
        auth.authenticate_from_environment(
            {auth.MCP_TOKEN_ENV: "stored-token"}, path=selected_path
        )


def test_authentication_rejects_wide_parent_directory(tmp_path):
    auth = _auth()
    parent = tmp_path / "wide"
    parent.mkdir(mode=0o700)
    target = parent / "mcp_server_token"
    _write_private(target, "stored-token")
    parent.chmod(0o755)

    if os.name == "nt":
        assert auth.authenticate_from_environment(
            {auth.MCP_TOKEN_ENV: "stored-token"}, path=target
        ) == "6f69975abe580db3"
        return

    with pytest.raises(
        auth.MCPTokenError,
        match="^MCP server token file is unavailable or invalid$",
    ):
        auth.authenticate_from_environment(
            {auth.MCP_TOKEN_ENV: "stored-token"}, path=target
        )


def test_authentication_rejects_foreign_parent_directory():
    auth = _auth()
    parent = _foreign_writable_temp()
    target = parent / f".openprogram-mcp-auth-{uuid.uuid4().hex}"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, b"stored-token")
    finally:
        os.close(descriptor)
    try:
        with pytest.raises(
            auth.MCPTokenError,
            match="^MCP server token file is unavailable or invalid$",
        ):
            auth.authenticate_from_environment(
                {auth.MCP_TOKEN_ENV: "stored-token"}, path=target
            )
    finally:
        target.unlink(missing_ok=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink swap contract")
def test_authentication_revalidates_parent_path_after_read(tmp_path, monkeypatch):
    auth = _auth()
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o700)
    target = parent / "mcp_server_token"
    _write_private(target, "stored-token")
    replacement = tmp_path / "replacement"
    replacement.mkdir(mode=0o700)
    _write_private(replacement / "mcp_server_token", "other-token")
    moved = tmp_path / "moved"
    real_read = auth.os.read
    swapped = False

    def swap_parent_then_read(fd, size):
        nonlocal swapped
        if not swapped:
            parent.rename(moved)
            parent.symlink_to(replacement, target_is_directory=True)
            swapped = True
        return real_read(fd, size)

    monkeypatch.setattr(auth.os, "read", swap_parent_then_read)

    with pytest.raises(
        auth.MCPTokenError,
        match="^MCP server token file is unavailable or invalid$",
    ):
        auth.authenticate_from_environment(
            {auth.MCP_TOKEN_ENV: "stored-token"}, path=target
        )


def test_authentication_revalidates_token_path_inode_after_read(tmp_path, monkeypatch):
    auth = _auth()
    target = tmp_path / "mcp_server_token"
    _write_private(target, "stored-token")
    moved = tmp_path / "moved-token"
    real_read = auth.os.read
    swapped = False

    def replace_token_then_read(fd, size):
        nonlocal swapped
        if not swapped:
            target.rename(moved)
            _write_private(target, "other-token")
            swapped = True
        return real_read(fd, size)

    monkeypatch.setattr(auth.os, "read", replace_token_then_read)

    with pytest.raises(
        auth.MCPTokenError,
        match="^MCP server token file is unavailable or invalid$",
    ):
        auth.authenticate_from_environment(
            {auth.MCP_TOKEN_ENV: "stored-token"}, path=target
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX uid contract")
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
    secret = "-".join(("stored", "token"))
    _write_private(target, secret)
    real_open = auth.os.open

    def denied(path, *args, **kwargs):
        if os.fspath(path) == os.fspath(target):
            raise PermissionError(f"filesystem included {secret}")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(auth.os, "open", denied)
    with pytest.raises(
        auth.MCPTokenError,
        match="^MCP server token file is unavailable or invalid$",
    ) as caught:
        auth.authenticate_from_environment({auth.MCP_TOKEN_ENV: secret}, path=target)

    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.tb)
    )
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert secret not in rendered


def test_authentication_reads_to_eof_across_forced_short_reads(tmp_path, monkeypatch):
    auth = _auth()
    target = tmp_path / "mcp_server_token"
    _write_private(target, "stored-token")
    real_read = auth.os.read

    def short_read(fd, size):
        return real_read(fd, min(size, 3))

    monkeypatch.setattr(auth.os, "read", short_read)

    assert (
        auth.authenticate_from_environment(
            {auth.MCP_TOKEN_ENV: "stored-token"}, path=target
        )
        == "6f69975abe580db3"
    )


def test_authentication_never_accepts_a_short_read_prefix(tmp_path, monkeypatch):
    auth = _auth()
    target = tmp_path / "mcp_server_token"
    _write_private(target, "stored-token")
    real_read = auth.os.read

    def short_read(fd, size):
        return real_read(fd, min(size, 3))

    monkeypatch.setattr(auth.os, "read", short_read)

    with pytest.raises(
        auth.MCPTokenError,
        match="^MCP server authentication failed$",
    ):
        auth.authenticate_from_environment({auth.MCP_TOKEN_ENV: "sto"}, path=target)


def test_authentication_rejects_actual_oversize_before_reading(tmp_path, monkeypatch):
    auth = _auth()
    target = tmp_path / "mcp_server_token"
    marker = "oversized-secret-marker"
    _write_private(target, (marker * 200)[:4097])
    read_calls = []

    def forbidden_read(fd, size):
        read_calls.append((fd, size))
        raise AssertionError("oversized token content must not be read")

    monkeypatch.setattr(auth.os, "read", forbidden_read)

    with pytest.raises(
        auth.MCPTokenError,
        match="^MCP server token file is unavailable or invalid$",
    ) as caught:
        auth.authenticate_from_environment(
            {auth.MCP_TOKEN_ENV: "presented-secret"}, path=target
        )

    assert read_calls == []
    assert marker not in str(caught.value)
    assert marker not in repr(caught.value)


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


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("create", "could not create MCP server token"),
        ("authenticate", "MCP server token file is unavailable or invalid"),
    ],
)
def test_default_path_resolution_failure_is_sanitized(monkeypatch, operation, expected):
    auth = _auth()
    leaked = "secret-path-fragment"

    def fail_path():
        raise OSError(f"cannot resolve /private/{leaked}")

    monkeypatch.setattr(auth, "token_path", fail_path)

    with pytest.raises(auth.MCPTokenError, match=f"^{expected}$") as caught:
        if operation == "create":
            auth.create_token()
        else:
            auth.authenticate_from_environment({auth.MCP_TOKEN_ENV: "presented"})

    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.tb)
    )
    assert leaked not in str(caught.value)
    assert leaked not in repr(caught.value)
    assert leaked not in rendered
