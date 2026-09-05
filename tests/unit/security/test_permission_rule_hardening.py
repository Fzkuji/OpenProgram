"""Focused permission-rule matching: path deny, apply_patch, tool names."""
from __future__ import annotations

import os

import pytest

from openprogram.agent.permissions.policy import _match_rule
from openprogram.agent.session_config import PermissionRules, SessionRunConfig
from openprogram.programs.permission_rule import (
    load_merged_rules,
    parse_command,
    pattern_matches,
)
from openprogram.store.project import project_store


def test_path_allow_does_not_match_resolution_env():
    rules = PermissionRules(allow=["bash(git:*)"])
    assert _match_rule(
        rules, "bash", {"command": "PATH=/evil git status"}
    ) is None
    assert _match_rule(
        rules, "bash", {"command": "env PATH=/evil git status"}
    ) is None


def test_path_deny_still_matches_resolution_env():
    rules = PermissionRules(deny=["bash(rm:*)"])
    assert _match_rule(
        rules, "bash", {"command": "PATH=/evil rm -rf /"}
    ) == "deny"


def test_deny_hits_case_variant_path():
    assert pattern_matches("/etc/**", "/Etc/passwd", allow=False)
    assert not pattern_matches("/etc/**", "/Etc/passwd", allow=True)
    rules = PermissionRules(deny=["read(/etc/**)"])
    assert _match_rule(rules, "read", {"path": "/Etc/passwd"}) == "deny"


def test_deny_hits_symlink_to_denied_path(tmp_path):
    target = tmp_path / "secret"
    target.write_text("x")
    link = tmp_path / "visible"
    link.symlink_to(target)
    rules = PermissionRules(deny=[f"read({target.parent}/**)"])
    assert _match_rule(rules, "read", {"path": str(link)}) == "deny"
    assert parse_command("read", {"path": str(link)}) == os.path.realpath(target)


def test_apply_patch_deny_matches_target_paths():
    patch = (
        "*** Begin Patch\n"
        "*** Add File: /tmp/safe.txt\n"
        "+ok\n"
        "*** Update File: /Etc/passwd\n"
        "@@\n-a\n+b\n"
        "*** End Patch"
    )
    rules = PermissionRules(deny=["apply_patch(/etc/**)"])
    assert _match_rule(rules, "apply_patch", {"patch": patch}) == "deny"
    parsed = parse_command("apply_patch", {"patch": patch})
    assert parsed is not None
    parts = parsed.split("\x1e")
    assert len(parts) == 2
    assert any("safe.txt" in part for part in parts)


def test_list_jobs_is_not_a_path_tool():
    args = {"path": "/etc/passwd"}
    assert parse_command("list_jobs", args) == '{"path":"/etc/passwd"}'
    assert _match_rule(
        PermissionRules(deny=["list_jobs(/etc/**)"]), "list_jobs", args
    ) is None
    assert _match_rule(
        PermissionRules(deny=["list(/etc/**)"]), "list", args
    ) == "deny"


def _isolate_optional_layers(monkeypatch):
    monkeypatch.setattr(
        "openprogram.agent.session_config.load_session_run_config",
        lambda _sid: SessionRunConfig(),
    )
    try:
        from openprogram.webui import _setup
        monkeypatch.setattr(_setup, "_read_config", lambda: {})
    except Exception:
        pass


def test_load_merged_rules_raises_on_malformed_project_rules(
    monkeypatch, caplog,
):
    _isolate_optional_layers(monkeypatch)
    monkeypatch.setattr(project_store, "project_for_session", lambda _sid: type("P", (), {"id": "p"})())
    monkeypatch.setattr(
        project_store, "load_project_settings",
        lambda _pid: {"permission_rules": "not-a-dict"},
    )
    with caplog.at_level("WARNING"):
        with pytest.raises(ValueError, match="permission_rules"):
            load_merged_rules("s")
    assert "project permission_rules unusable" in caplog.text


def test_load_merged_rules_skips_missing_project(monkeypatch):
    _isolate_optional_layers(monkeypatch)
    monkeypatch.setattr(project_store, "project_for_session", lambda _sid: None)
    rules = load_merged_rules("s")
    assert rules.deny == []
    assert rules.allow == []
