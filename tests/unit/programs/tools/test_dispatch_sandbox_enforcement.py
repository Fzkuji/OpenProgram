"""Dispatch-layer sandbox / URL fail-closed on @function."""
from __future__ import annotations

import asyncio

import pytest

from openprogram import sandbox
from openprogram.programs import _runtime as R
from openprogram.programs._runtime import function, reset_registry, restore_registry, snapshot_registry
from openprogram.sandbox import SandboxPolicy, policy_to_dict


@pytest.fixture(autouse=True)
def _isolate_registry():
    saved = snapshot_registry()
    R._cache.clear()
    reset_registry()
    yield
    restore_registry(saved)
    R._cache.clear()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _text(result) -> str:
    return "".join(block.text for block in result.content)


def _install_policy(*, deny_read=(), writable_roots=()):
    sandbox.install_policy_snapshot({
        "enabled": True,
        "policy": policy_to_dict(SandboxPolicy(
            writable_roots=tuple(str(p) for p in writable_roots),
            deny_read=tuple(str(p) for p in deny_read),
            deny_write=(),
        )),
    })


def test_declared_read_path_blocks_and_skips_body(tmp_path):
    secret = tmp_path / "secret"
    secret.mkdir()
    target = secret / "id_rsa"
    target.write_text("k")
    work = tmp_path / "work"
    work.mkdir()
    _install_policy(deny_read=(str(secret) + "/**",), writable_roots=(work,))

    called = []

    @function(name="probe_read", path_params={"path": "read"})
    def probe_read(path: str) -> str:
        called.append(path)
        return "ran"

    result = _run(probe_read.execute("c", {"path": str(target)}, None, None))
    assert result.is_error is True
    assert _text(result).startswith("Error: sandbox policy:")
    assert called == []


def test_declared_read_path_list_blocks(tmp_path):
    secret = tmp_path / "secret"
    secret.mkdir()
    target = secret / "id_rsa"
    target.write_text("k")
    work = tmp_path / "work"
    work.mkdir()
    _install_policy(deny_read=(str(secret) + "/**",), writable_roots=(work,))

    called = []

    @function(name="probe_paths", path_params={"image_paths": "read"})
    def probe_paths(image_paths: list[str]) -> str:
        called.append(image_paths)
        return "ran"

    result = _run(probe_paths.execute(
        "c", {"image_paths": [str(target)]}, None, None,
    ))
    assert result.is_error is True
    assert called == []


def test_declared_url_blocks_file_scheme():
    called = []

    @function(name="probe_url", url_params=["url"])
    def probe_url(url: str) -> str:
        called.append(url)
        return "ran"

    result = _run(probe_url.execute(
        "c", {"url": "file:///etc/passwd"}, None, None,
    ))
    assert result.is_error is True
    assert "SCHEME_FORBIDDEN" in _text(result)
    assert called == []


def test_undeclared_path_param_falls_back_to_write(tmp_path):
    work = tmp_path / "work"
    outside = tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    target = outside / "x.txt"
    target.write_text("x")
    _install_policy(writable_roots=(work,))

    called = []

    @function(name="custom_helper")
    def custom_helper(path: str) -> str:
        called.append(path)
        return "ran"

    result = _run(custom_helper.execute("c", {"path": str(target)}, None, None))
    assert result.is_error is True
    assert _text(result).startswith("Error: sandbox policy:")
    assert called == []


def test_relative_write_anchors_to_worktree(tmp_path):
    from openprogram.worktree.context import clear_worktree, set_worktree

    work = tmp_path / "wt"
    work.mkdir()
    _install_policy(writable_roots=(work,))
    token = set_worktree(str(work))
    called = []
    try:
        @function(name="probe_write", path_params={"file_path": "write"})
        def probe_write(file_path: str) -> str:
            called.append(file_path)
            return "ran"

        result = _run(probe_write.execute(
            "c", {"file_path": "inside.txt"}, None, None,
        ))
        assert result.is_error is False
        assert called == ["inside.txt"]
    finally:
        try:
            from openprogram.worktree.context import reset_worktree
            reset_worktree(token)
        except Exception:
            clear_worktree()


def test_exempt_tool_is_not_blocked(tmp_path):
    work = tmp_path / "work"
    outside = tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    target = outside / "x.txt"
    target.write_text("x")
    _install_policy(writable_roots=(work,))

    called = []

    @function(name="bash_like", path_params={}, url_params=[])
    def bash_like(path: str) -> str:
        called.append(path)
        return "ran"

    result = _run(bash_like.execute("c", {"path": str(target)}, None, None))
    assert result.is_error is False
    assert _text(result) == "ran"
    assert called == [str(target)]
