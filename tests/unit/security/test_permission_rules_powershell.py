"""Allow rules read a PowerShell command only where a POSIX reading is safe.

Prefix rules tokenize a command as POSIX shell. PowerShell gives ``{}``
(script blocks, hash tables) and ``,`` (arrays) meanings that reading does
not see, so where commands run in PowerShell an allow rule never matches a
command that uses them; deny and ask rules still do. "Always allow" saves
the command's exact text, which matches that text only.
"""
from __future__ import annotations

import pytest

from openprogram.host._platform import command_shell
from openprogram.host._platform.command_shell import ShellProfile
from openprogram.runtime.permissions.rule import exact_rule_for_call, parse_command, pattern_matches

POWERSHELL = ShellProfile("powershell51", "powershell.exe", "path", edition="5.1")
BASH = ShellProfile("bash", "/bin/bash", "system")


def use_shell(monkeypatch, profile):
    monkeypatch.setattr(command_shell, "resolve_shell", lambda *a, **kw: profile)


def allowed(pattern: str, command: str) -> bool:
    return pattern_matches(pattern, parse_command("bash", {"command": command}), allow=True)


def denied(pattern: str, command: str) -> bool:
    return pattern_matches(pattern, parse_command("bash", {"command": command}), allow=False)


@pytest.mark.parametrize("command", [
    "git status{Remove-Item -Recurse -Force C:\\work}",
    "git log --format=x,y",
    "git status @{a=1}",
    "git status }",
])
def test_powershell_command_with_unread_syntax_is_not_auto_allowed(monkeypatch, command):
    use_shell(monkeypatch, POWERSHELL)
    assert allowed("git:*", command) is False
    assert allowed("git *", command) is False


@pytest.mark.parametrize("command", [
    "git log --format=%h,%s",
    "git status @{a=1}",
])
def test_always_allow_keeps_its_exact_rule_for_that_very_command(monkeypatch, command):
    # The approval card offers "Always allow" for this command; the rule it
    # saves is the command's own text and matches nothing else.
    use_shell(monkeypatch, POWERSHELL)
    rule = exact_rule_for_call("bash", {"command": command})
    assert rule is not None and rule.pattern == command
    assert allowed(rule.pattern, command) is True
    assert allowed(rule.pattern, command + "; Remove-Item x") is False
    assert allowed(rule.pattern, command + " x") is False
    assert allowed(rule.pattern, "'" + command + "'") is False


def test_an_exact_rule_does_not_match_a_differently_quoted_command(monkeypatch):
    # POSIX reads both as the same words; PowerShell does not ('a,b' is one
    # string, a,b an array), so only the identical text is allowed.
    use_shell(monkeypatch, POWERSHELL)
    rule = exact_rule_for_call("bash", {"command": "git log 'a,b'"})
    assert allowed(rule.pattern, "git log 'a,b'") is True
    assert allowed(rule.pattern, "git log a,b") is False


@pytest.mark.parametrize("command", ["& (Remove-Item x)", "git status; Remove-Item x", "git status | Out-Null"])
def test_powershell_operators_stay_unmatched(monkeypatch, command):
    use_shell(monkeypatch, POWERSHELL)
    assert allowed("git:*", command) is False


def test_a_plain_powershell_command_is_still_allowed(monkeypatch):
    use_shell(monkeypatch, POWERSHELL)
    assert allowed("git:*", "git status --short") is True
    assert exact_rule_for_call("bash", {"command": "git status --short"}) is not None


def test_deny_rules_still_match_in_powershell(monkeypatch):
    use_shell(monkeypatch, POWERSHELL)
    assert denied("Remove-Item:*", "Remove-Item a,b") is True
    assert denied("git push*", "git push origin a,b") is True


def test_a_sandboxed_command_that_may_leave_to_powershell_is_read_as_powershell(monkeypatch):
    use_shell(monkeypatch, ShellProfile("wsl-bash", "/bin/bash", "sandbox", sandboxed=True, host=POWERSHELL))
    assert allowed("git:*", "git log --format=x,y") is False


def test_bash_keeps_reading_braces_and_commas(monkeypatch):
    use_shell(monkeypatch, BASH)
    assert allowed("git:*", "git log --format=x,y") is True
    assert allowed("git:*", "git status{a}") is True


# -- deny rules written for bash also read PowerShell's delete, move and copy

from openprogram.runtime.permissions import powershell_words  # noqa: E402
from openprogram.runtime.permissions.modes import PermissionRules  # noqa: E402
from openprogram.runtime.permissions.policy import match_rule  # noqa: E402


def decide(command: str, *, deny=(), ask=(), allow=()):
    rules = PermissionRules(allow=list(allow), deny=list(deny), ask=list(ask))
    return match_rule(rules, "bash", {"command": command})


@pytest.mark.parametrize("command", [
    "Remove-Item -Recurse -Force .",
    "Remove-Item -Force -Recurse .",
    "ri -r -fo .",
    "Microsoft.PowerShell.Management\\Remove-Item -LiteralPath . -Recurse -Force",
    "git status; del -Recurse -Force .",
])
def test_a_bash_delete_rule_stops_the_powershell_delete(monkeypatch, command):
    use_shell(monkeypatch, POWERSHELL)
    assert decide(command, deny=["bash(rm -rf *)"]) == ("deny", "bash(rm -rf *)")


@pytest.mark.parametrize("command", ["ri x", "del x", "erase x", "rd x", "Remove-Item -Path x"])
def test_every_delete_alias_reads_as_rm(monkeypatch, command):
    use_shell(monkeypatch, POWERSHELL)
    assert decide(command, deny=["bash(rm:*)"]) == ("deny", "bash(rm:*)")


def test_move_and_copy_read_as_mv_and_cp(monkeypatch):
    use_shell(monkeypatch, POWERSHELL)
    assert decide("Move-Item -Path a -Destination b", deny=["bash(mv a b)"])[0] == "deny"
    assert decide("Copy-Item -Recurse a b", deny=["bash(cp -r *)"])[0] == "deny"


def test_an_unreadable_delete_is_asked_about_when_a_delete_rule_exists(monkeypatch):
    use_shell(monkeypatch, POWERSHELL)
    assert powershell_words.lower("& (gcm Remove-Item) x") is None
    assert decide("& (gcm Remove-Item) x", deny=["bash(rm -rf *)"]) == ("ask", "bash(rm -rf *)")
    assert decide("iex $cmd", deny=["bash(rm:*)"]) == ("ask", "bash(rm:*)")


def test_an_unreadable_command_is_left_alone_without_a_delete_rule(monkeypatch):
    use_shell(monkeypatch, POWERSHELL)
    assert decide("& (gcm Remove-Item) x", deny=["bash(git push*)"]) == (None, None)
    assert decide("Write-Output $env:PATH", deny=["bash(rm -rf *)"]) == (None, None)


def test_a_real_deny_still_wins_over_the_ask(monkeypatch):
    use_shell(monkeypatch, POWERSHELL)
    verdict = decide("& (gcm Remove-Item) x", deny=["bash(rm -rf *)", "bash"])
    assert verdict == ("deny", "bash")


def test_allow_rules_do_not_read_the_lowered_command(monkeypatch):
    use_shell(monkeypatch, POWERSHELL)
    assert allowed("rm:*", "Remove-Item x") is False
    rule = exact_rule_for_call("bash", {"command": "Remove-Item -Recurse x"})
    assert rule.pattern == "Remove-Item -Recurse x"


def test_bash_hosts_read_commands_as_before(monkeypatch):
    use_shell(monkeypatch, BASH)
    assert decide("Remove-Item -Recurse -Force .", deny=["bash(rm -rf *)"]) == (None, None)
    assert decide("& (gcm Remove-Item) x", deny=["bash(rm -rf *)"]) == (None, None)


@pytest.mark.parametrize("script", [
    "Remove-Item $path", "Remove-Item (Get-Item x)", "Get-ChildItem | % { Remove-Item $_ }",
    "Remove-Item a,b", "Remove-Item x > log.txt", "Remove-Item “x”", "Remove-Item `x",
    ". ./clean.ps1", "Remove-Item \"$HOME\\x\"", "Remove-Item @args",
])
def test_lowering_refuses_what_it_cannot_read_for_certain(script):
    assert powershell_words.lower(script) is None


def test_lowering_reads_literal_strings_and_pipelines():
    assert powershell_words.lower("Remove-Item 'it''s here' -Force | Out-Null; git status") == [
        ["Remove-Item", "it's here", "-Force"], ["Out-Null"], ["git", "status"]]
    assert powershell_words.posix_readings('del "a b" -Recurse') == [["rm", "-r", "a b"]]
