"""Unit tests for auth.accounts — isolation boundaries + subprocess env."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from openprogram.auth.accounts import (
    DEFAULT_ACCOUNT_NAME,
    AccountManager,
)
from openprogram.auth.types import AuthConfigError


@pytest.fixture
def mgr(tmp_path: Path) -> AccountManager:
    return AccountManager(root=tmp_path / "profiles")


def test_default_profile_created_on_init(mgr, tmp_path):
    p = mgr.get_account(DEFAULT_ACCOUNT_NAME)
    assert p.name == DEFAULT_ACCOUNT_NAME
    assert (p.root / "metadata.json").exists()
    assert (p.root / "auth").is_dir()
    assert (p.root / "home").is_dir()


def test_create_profile_writes_metadata(mgr):
    p = mgr.create_account("work", display_name="Work account", description="work stuff")
    assert p.display_name == "Work account"
    assert p.description == "work stuff"
    assert p.created_at_ms > 0


def test_create_duplicate_fails(mgr):
    mgr.create_account("dup")
    with pytest.raises(AuthConfigError):
        mgr.create_account("dup")


def test_list_profiles_includes_default_and_new(mgr):
    mgr.create_account("work")
    names = [p.name for p in mgr.list_accounts()]
    assert DEFAULT_ACCOUNT_NAME in names
    assert "work" in names


def test_delete_profile_removes_tree(mgr):
    mgr.create_account("scratch")
    root = mgr.get_account("scratch").root
    assert root.exists()
    mgr.delete_account("scratch")
    assert not root.exists()
    with pytest.raises(AuthConfigError):
        mgr.get_account("scratch")


def test_cannot_delete_default(mgr):
    with pytest.raises(AuthConfigError):
        mgr.delete_account(DEFAULT_ACCOUNT_NAME)


def test_name_validation_blocks_traversal(mgr):
    for bad in ["../evil", "a/b", ".hidden", "", "x" * 100]:
        with pytest.raises(AuthConfigError):
            mgr.create_account(bad)


def test_subprocess_env_overrides_home(mgr):
    p = mgr.get_account(DEFAULT_ACCOUNT_NAME)
    env = mgr.subprocess_env(p, base_env={"PATH": "/usr/bin", "HOME": "/old/home"})
    assert env["HOME"] == str(p.home_dir)
    assert env["USERPROFILE"] == str(p.home_dir)
    assert env["XDG_CONFIG_HOME"] == str(p.home_dir / ".config")
    assert env["XDG_CACHE_HOME"] == str(p.home_dir / ".cache")
    assert env["GH_CONFIG_DIR"] == str(p.home_dir / ".config" / "gh")
    assert env["OPENPROGRAM_PROFILE"] == DEFAULT_ACCOUNT_NAME
    assert env["PATH"] == "/usr/bin"  # passthrough


def test_subprocess_env_does_not_mutate_os_environ(mgr, monkeypatch):
    monkeypatch.setenv("HOME", "/real/home")
    p = mgr.get_account(DEFAULT_ACCOUNT_NAME)
    env = mgr.subprocess_env(p)
    assert env["HOME"] != "/real/home"
    assert os.environ["HOME"] == "/real/home"


def test_subprocess_env_merges_dotenv(mgr):
    p = mgr.get_account(DEFAULT_ACCOUNT_NAME)
    mgr.set_env_var(p, "OPENAI_API_KEY", "sk-account-key")
    env = mgr.subprocess_env(p, base_env={})
    assert env["OPENAI_API_KEY"] == "sk-account-key"


def test_dotenv_roundtrip_preserves_values(mgr):
    p = mgr.get_account(DEFAULT_ACCOUNT_NAME)
    mgr.set_env_var(p, "A", "1")
    mgr.set_env_var(p, "B", "value with spaces")
    mgr.set_env_var(p, "C", 'has"quotes')
    text = p.env_file.read_text(encoding="utf-8")
    assert "A=1" in text
    env = mgr.subprocess_env(p, base_env={})
    assert env["A"] == "1"
    assert env["B"] == "value with spaces"
    assert env["C"] == 'has"quotes'


def test_unset_env_var_removes_key(mgr):
    p = mgr.get_account(DEFAULT_ACCOUNT_NAME)
    mgr.set_env_var(p, "FOO", "bar")
    mgr.unset_env_var(p, "FOO")
    env = mgr.subprocess_env(p, base_env={})
    assert "FOO" not in env


def test_env_key_validation_blocks_bad_chars(mgr):
    p = mgr.get_account(DEFAULT_ACCOUNT_NAME)
    for bad in ["", "K=V", "with\nnewline", "with\0null"]:
        with pytest.raises(AuthConfigError):
            mgr.set_env_var(p, bad, "x")


def test_dotenv_handles_quoted_values_with_escapes(mgr):
    p = mgr.get_account(DEFAULT_ACCOUNT_NAME)
    # Write values manually to exercise the parser on a realistic file.
    p.env_file.write_text(
        '# a comment\n'
        'SIMPLE=value\n'
        'SPACED="has spaces"\n'
        "SINGLE='single quotes'\n"
        "\n"
        "NOEQUALS\n",
        encoding="utf-8",
    )
    env = mgr.subprocess_env(p, base_env={})
    assert env["SIMPLE"] == "value"
    assert env["SPACED"] == "has spaces"
    assert env["SINGLE"] == "single quotes"
    assert "NOEQUALS" not in env


def test_subprocess_env_creates_home_dir_if_missing(mgr, tmp_path):
    p = mgr.create_account("fresh")
    # simulate external deletion
    import shutil
    shutil.rmtree(p.home_dir)
    env = mgr.subprocess_env(p)
    assert Path(env["HOME"]).exists()
