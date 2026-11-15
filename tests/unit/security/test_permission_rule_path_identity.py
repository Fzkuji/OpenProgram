"""One file, many spellings: a rule must see all of them, and only them.

Windows semantics run here through a fake ``os`` for the rule module
(``ntpath`` plus a small table standing in for the volume), so both
directions are checked without a Windows host: a spelling that names the
ruled file matches, and a neighbour that only looks similar does not.
"""
from __future__ import annotations

import ntpath
import os
import types

import pytest

from openprogram.runtime.permissions import rule
from openprogram.runtime.permissions.modes import PermissionRules
from openprogram.runtime.permissions.policy import match_rule
from openprogram.state.worktree import context as worktree_context


class _Volume:
    """``ntpath`` whose ``realpath`` answers like a Windows volume.

    ``names`` maps a case-folded path (a long name or an 8.3 alias) to the
    spelling stored on disk. Like CPython on Windows, a ``\\\\?\\`` prefix
    given by the caller is kept, and a missing tail is appended as typed.
    """

    def __init__(self, names: dict[str, str]):
        self._names = {key.casefold(): value for key, value in names.items()}

    def __getattr__(self, name):
        return getattr(ntpath, name)

    def realpath(self, path, *, strict=False):
        path = ntpath.normpath(path)
        prefix = ""
        for candidate in ("\\\\?\\UNC\\", "\\\\?\\", "\\\\.\\"):
            if path.startswith(candidate):
                prefix = candidate
                path = ("\\\\" if candidate.endswith("UNC\\") else "") + path[len(candidate):]
                break
        parts = path.split("\\")
        for size in range(len(parts), 0, -1):
            stored = self._names.get("\\".join(parts[:size]).casefold())
            if stored is not None:
                path = "\\".join([stored, *parts[size:]])
                break
        if prefix == "\\\\?\\UNC\\":
            return prefix + path[2:]
        # The device namespace resolves to the DOS path; \\?\ is kept.
        return (prefix if prefix == "\\\\?\\" else "") + path


@pytest.fixture
def windows(monkeypatch):
    volume = _Volume({
        "C:\\Users\\me\\proj": "C:\\Users\\me\\proj",
        "C:\\Users\\me\\proj\\sub": "C:\\Users\\me\\proj\\sub",
        "C:\\Users\\me\\proj\\.env": "C:\\Users\\me\\proj\\.env",
        "C:\\Users\\me\\project2": "C:\\Users\\me\\project2",
        "C:\\Program Files": "C:\\Program Files",
        "C:\\PROGRA~1": "C:\\Program Files",
        "\\\\srv\\share\\team": "\\\\srv\\share\\team",
    })
    monkeypatch.setattr(rule, "os", types.SimpleNamespace(path=volume, name="nt", sep="\\"))


def _verdict(rules: PermissionRules, path: str, tool: str = "read") -> str | None:
    return match_rule(rules, tool, {"path": path})[0]


@pytest.mark.parametrize("written", [
    r"C:\Users\me\proj\**",
    "c:/users/me/proj/**",
    r"c:\USERS\ME\PROJ\**",
    r"\\?\C:\Users\me\proj\**",
])
@pytest.mark.parametrize("called", [
    r"C:\Users\me\proj\sub\a.txt",
    "c:/users/me/proj/sub/a.txt",
    r"C:\Users\me\proj\sub\..\sub\a.txt",
    r"\\?\C:\Users\me\proj\sub\a.txt",
    r"\\.\C:\Users\me\proj\sub\a.txt",
    r"C:\Users\me\proj\sub\a.txt.",
    r"C:\Users\me\proj\sub\a.txt::$DATA",
])
def test_windows_rule_sees_every_spelling_of_its_folder(windows, written, called):
    assert _verdict(PermissionRules(allow=[f"read({written})"]), called) == "allow"
    assert _verdict(PermissionRules(deny=[f"read({written})"]), called) == "deny"


@pytest.mark.parametrize("called", [
    r"C:\Users\me\project2\a.txt",
    r"C:\Users\me\proj\..\project2\a.txt",
    r"C:\Users\me\proj2\sub\a.txt",
    r"D:\Users\me\proj\sub\a.txt",
    r"\\srv\share\team\Users\me\proj\a.txt",
    r"C:Users\me\proj\a.txt",
])
def test_windows_rule_leaves_a_neighbouring_folder_alone(windows, called):
    for written in (r"C:\Users\me\proj\**", "c:/users/me/proj/**"):
        assert _verdict(PermissionRules(allow=[f"read({written})"]), called) is None
        assert _verdict(PermissionRules(deny=[f"read({written})"]), called) is None


def test_windows_allow_keeps_case_where_the_volume_has_no_answer(windows):
    # A file that is not there yet has no stored spelling to compare with.
    rules = PermissionRules(allow=[r"write(C:\Users\me\proj\notes.txt)"])
    assert _verdict(rules, r"C:\Users\me\proj\notes.txt", "write") == "allow"
    assert _verdict(rules, r"C:\Users\me\proj\NOTES.txt", "write") is None
    deny = PermissionRules(deny=[r"write(C:\Users\me\proj\notes.txt)"])
    assert _verdict(deny, r"C:\Users\me\proj\NOTES.txt", "write") == "deny"


def test_windows_short_name_and_unc_spellings(windows):
    deny = PermissionRules(deny=[r"read(C:\Program Files\app\**)"])
    assert _verdict(deny, r"C:\PROGRA~1\app\key.pem") == "deny"
    assert _verdict(PermissionRules(deny=[r"read(C:\PROGRA~1\app\**)"]),
                    r"C:\Program Files\app\key.pem") == "deny"
    share = PermissionRules(deny=[r"read(\\srv\share\team\**)"])
    assert _verdict(share, r"\\?\UNC\srv\share\team\plan.txt") == "deny"
    assert _verdict(share, "//SRV/Share/team/plan.txt") == "deny"
    assert _verdict(share, r"\\srv\share\other\plan.txt") is None


def test_windows_deny_covers_trailing_dot_and_streams_of_a_file(windows):
    deny = PermissionRules(deny=[r"write(C:\Users\me\proj\.env)"])
    for called in (r"C:\Users\me\proj\.env.", r"C:\Users\me\proj\.env ",
                   r"C:\Users\me\proj\.env::$DATA", r"C:\Users\me\proj\.env:hidden",
                   r"\\?\C:\Users\me\proj\.env"):
        assert _verdict(deny, called, "write") == "deny", called
    assert _verdict(deny, r"C:\Users\me\proj\.envrc", "write") is None


def test_windows_literal_path_keeps_its_trailing_dot(windows):
    # \\?\ turns Win32 trimming off: "notes.txt." is another file there.
    allow = PermissionRules(allow=[r"write(C:\Users\me\proj\notes.txt)"])
    assert _verdict(allow, r"\\?\C:\Users\me\proj\notes.txt.", "write") is None
    assert _verdict(allow, r"C:\Users\me\proj\notes.txt:hidden", "write") is None


def test_relative_path_meets_a_rule_for_the_absolute_folder(tmp_path, monkeypatch):
    root = tmp_path / "work"
    (root / "secret").mkdir(parents=True)
    (root / "secret" / "a.txt").write_text("x", encoding="utf-8")
    (root / "open").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    token = worktree_context.set_worktree(str(root))
    try:
        deny = PermissionRules(deny=[f"read({os.path.realpath(root)}{os.sep}secret{os.sep}**)"])
        assert _verdict(deny, os.path.join("secret", "a.txt")) == "deny"
        assert _verdict(deny, os.path.join("open", "..", "secret", "a.txt")) == "deny"
        assert _verdict(deny, os.path.join("open", "a.txt")) is None
    finally:
        worktree_context.reset_worktree(token)


def test_allow_rule_written_through_a_link_names_the_real_folder(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "a.txt").write_text("x", encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    allow = PermissionRules(allow=[f"read({link}{os.sep}**)"])
    assert _verdict(allow, str(link / "a.txt")) == "allow"
    assert _verdict(allow, str(real / "a.txt")) == "allow"
    assert _verdict(allow, str(tmp_path / "other" / "a.txt")) is None


def test_typed_unc_rule_keeps_both_leading_backslashes():
    typed = r"read(\\srv\share\team\**)"
    assert rule.parse_rule(typed).pattern == r"\\srv\share\team\**"
    for pattern in (r"\\srv\share\team\**", r"C:\Users\(x)\a.txt", r"\\?\C:\x"):
        stored = rule.rule_to_string(rule.PermissionRuleValue("read", pattern))
        assert rule.parse_rule(stored).pattern == pattern


@pytest.mark.parametrize("written, called", [
    ("**/.env", r"C:\Users\me\proj\.env"),
    ("**/.env", "C:/Users/me/proj/.env"),
    ("**/.env", ".env"),
    ("**/secrets/**", r"C:\Users\me\proj\secrets\k.pem"),
    ("*/.ssh/*", r"C:\Users\me\.ssh\id_rsa"),
    (r"**\.env", r"C:\Users\me\proj\.env"),
])
def test_windows_rule_that_starts_with_a_wildcard_takes_either_slash(windows, written, called):
    token = worktree_context.set_worktree("C:\\Users\\me\\proj")
    try:
        for behavior in ("deny", "ask", "allow"):
            rules = PermissionRules(**{behavior: [f"read({written})"]})
            if behavior == "allow" and called == ".env":
                continue  # an allow rule is compared with the full path only
            assert _verdict(rules, called) == behavior, behavior
    finally:
        worktree_context.reset_worktree(token)


@pytest.mark.parametrize("written, called", [
    ("**/.env", r"C:\Users\me\proj\.envrc"),
    ("**/.env", r"C:\Users\me\proj\x.env"),
    ("**/secrets/**", r"C:\Users\me\proj\secrets.txt"),
    ("*/.ssh/*", r"C:\Users\me\ssh\id_rsa"),
])
def test_windows_wildcard_rule_leaves_a_similar_name_alone(windows, written, called):
    for behavior in ("deny", "ask", "allow"):
        assert _verdict(PermissionRules(**{behavior: [f"read({written})"]}), called) is None


def test_windows_deny_on_a_share_typed_with_forward_slashes(windows):
    # The grammar reads the doubled backslash as one; the share is still covered.
    for behavior in ("deny", "ask"):
        rules = PermissionRules(**{behavior: [r"read(\\srv/share/team/**)"]})
        assert _verdict(rules, r"\\srv\share\team\plan.txt") == behavior
        assert _verdict(rules, "//srv/share/team/plan.txt") == behavior
        assert _verdict(rules, r"\\srv\share\other\plan.txt") is None
        assert _verdict(rules, r"\\other\share\team\plan.txt") is None
        assert _verdict(rules, r"C:\Users\me\proj\sub\a.txt") is None
    # Stored rules still read back as written, so the pair is not kept at parse.
    stored = rule.rule_to_string(rule.PermissionRuleValue("read", r"\srv/share/**"))
    assert rule.parse_rule(stored).pattern == r"\srv/share/**"


def test_allow_rule_is_compared_with_the_full_path_only(tmp_path):
    root = tmp_path / "work"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.txt").write_text("x", encoding="utf-8")
    token = worktree_context.set_worktree(str(root))
    try:
        allow = PermissionRules(allow=[f"read({os.path.realpath(root)}{os.sep}**)"])
        assert _verdict(allow, str(root / "sub" / "a.txt")) == "allow"
        assert _verdict(allow, os.path.join("sub", "a.txt")) is None
    finally:
        worktree_context.reset_worktree(token)
