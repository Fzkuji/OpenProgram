"""Permission system tests — rule parsing/matching + decision precedence.

Design: docs/design/runtime/permission-model.md.
"""
from __future__ import annotations

from openprogram.functions.permission_rule import (
    parse_rule, rule_to_string, parse_command, pattern_matches, PermissionRuleValue,
)
from openprogram.agent.internals._approval import _match_rule
from openprogram.agent.session_config import (
    PermissionRules, _normalize_permission, VALID_PERMISSION,
)


# ── rule string parse / serialize ──

def test_parse_per_tool():
    assert parse_rule("bash") == PermissionRuleValue("bash", None)


def test_parse_per_pattern():
    assert parse_rule("bash(git:*)") == PermissionRuleValue("bash", "git:*")


def test_roundtrip_with_escaped_parens():
    for s in ("bash", "bash(git:*)", "read_file(/etc/**)", r"bash(echo \(hi\))"):
        assert rule_to_string(parse_rule(s)) == s


# ── pattern matching ──

def test_prefix_star():
    assert pattern_matches("git:*", "git status")
    assert not pattern_matches("git:*", "github")   # not a prefix boundary


def test_glob():
    assert pattern_matches("/etc/**", "/etc/passwd")


def test_exact():
    assert pattern_matches("git status", "git status")
    assert not pattern_matches("git status", "git log")


# ── parse_command ──

def test_parse_command_bash():
    assert parse_command("bash", {"command": "git status"}) == "git status"


def test_parse_command_write():
    assert parse_command("write_file", {"path": "/tmp/x"}) == "/tmp/x"


def test_parse_command_no_field():
    assert parse_command("web_search", {"query": "x"}) is None


# ── _match_rule precedence deny > ask > allow ──

def test_match_none_rules():
    assert _match_rule(None, "bash", {}) is None


def test_match_per_tool_deny():
    r = PermissionRules(deny=["bash"])
    assert _match_rule(r, "bash", {"command": "ls"}) == "deny"


def test_match_per_pattern():
    r = PermissionRules(deny=["bash(rm -rf:*)"], allow=["bash(git:*)"])
    assert _match_rule(r, "bash", {"command": "rm -rf /x"}) == "deny"
    assert _match_rule(r, "bash", {"command": "git status"}) == "allow"
    assert _match_rule(r, "bash", {"command": "ls"}) is None


def test_deny_beats_allow():
    # same tool in both — deny wins (scanned first)
    r = PermissionRules(deny=["bash"], allow=["bash"])
    assert _match_rule(r, "bash", {"command": "x"}) == "deny"


def test_ask_beats_allow():
    r = PermissionRules(ask=["write_file"], allow=["write_file"])
    assert _match_rule(r, "write_file", {"path": "/x"}) == "ask"


# ── permission mode normalize (camel modes) ──

def test_camel_modes_valid():
    assert _normalize_permission("acceptEdits") == "acceptEdits"
    assert _normalize_permission("ACCEPTEDITS") == "acceptEdits"
    assert _normalize_permission("dontask") == "dontAsk"


def test_all_six_modes_registered():
    assert VALID_PERMISSION == {"ask", "auto", "acceptEdits", "plan", "dontAsk", "bypass"}


def test_invalid_mode():
    assert _normalize_permission("bogus") is None
