"""Unit tests for auth.sources — external credential adoption.

Every source gets:
  * happy-path import returns a well-formed Credential
  * missing-file returns []
  * corrupt-file returns []
  * removal_steps() yields at least one step with the right metadata

Integration with the AuthStore is exercised elsewhere; these tests keep
to the source-level contract.
"""
from __future__ import annotations

import json
from pathlib import Path


from openprogram.models.auth.sources import (
    GhCliSource,
    QwenCliSource,
)
from openprogram.models.auth.sources.gh_cli import _parse_hosts_yml


# ---- QwenCliSource --------------------------------------------------------

def test_qwen_source_imports_fields(tmp_path: Path):
    path = tmp_path / "oauth_creds.json"
    path.write_text(json.dumps({
        "access_token": "A", "refresh_token": "R",
        "expiry_date": 1712345678901,
        "resource_url": "portal.qwen.ai",
        "token_type": "Bearer",
    }))
    src = QwenCliSource(override_path=str(path))
    [cred] = src.try_import(tmp_path)
    assert cred.kind == "import"
    assert cred.payload.data["expires_key_path"] == ["expiry_date"]
    assert cred.metadata["resource_url"] == "portal.qwen.ai"
    assert cred.read_only is True


def test_qwen_source_corrupt_returns_empty(tmp_path):
    path = tmp_path / "oauth_creds.json"
    path.write_text("oops")
    src = QwenCliSource(override_path=str(path))
    assert src.try_import(tmp_path) == []


# ---- GhCliSource ----------------------------------------------------------

GH_HOSTS_YML = """\
github.com:
    user: alice
    oauth_token: gho_abc123
    git_protocol: ssh
    users:
        alice:
            oauth_token: gho_abc123
github.enterprise.example:
    user: bob
    oauth_token: gho_zzz999
    git_protocol: https
"""


def test_gh_source_parses_two_hosts(tmp_path: Path):
    path = tmp_path / "hosts.yml"
    path.write_text(GH_HOSTS_YML)
    src = GhCliSource(override_path=str(path))
    creds = src.try_import(tmp_path)
    assert len(creds) == 2
    by_host = {c.metadata["host"]: c for c in creds}
    assert by_host["github.com"].payload.auth_value == "gho_abc123"
    assert by_host["github.com"].label == "alice"
    assert by_host["github.enterprise.example"].payload.auth_value == "gho_zzz999"
    assert by_host["github.enterprise.example"].identity["account_id"] == "github.enterprise.example/bob"


def test_gh_source_filter_by_host(tmp_path: Path):
    path = tmp_path / "hosts.yml"
    path.write_text(GH_HOSTS_YML)
    src = GhCliSource(override_path=str(path), hosts=["github.com"])
    [cred] = src.try_import(tmp_path)
    assert cred.metadata["host"] == "github.com"


def test_gh_source_missing_file(tmp_path):
    src = GhCliSource(override_path=str(tmp_path / "nope.yml"))
    assert src.try_import(tmp_path) == []


def test_gh_source_removal_has_both_instructional_and_executable(tmp_path: Path):
    path = tmp_path / "hosts.yml"
    path.write_text(GH_HOSTS_YML)
    src = GhCliSource(override_path=str(path))
    [cred] = src.try_import(tmp_path, )[:1]
    steps = src.removal_steps(cred)
    assert len(steps) == 2
    kinds = {s.kind for s in steps}
    assert kinds == {"external_cli", "file"}
    assert any(s.executable for s in steps)
    assert any(not s.executable for s in steps)


def test_gh_source_removal_names_the_file_the_token_came_from(tmp_path: Path, monkeypatch):
    # gh keeps hosts.yml under %AppData%\GitHub CLI on Windows, so the step
    # cannot say ~/.config/gh there.
    monkeypatch.delenv("GH_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("AppData", str(tmp_path / "Roaming"))
    monkeypatch.setattr("openprogram.models.auth.sources.gh_cli.sys.platform", "win32")
    path = tmp_path / "Roaming" / "GitHub CLI" / "hosts.yml"
    path.parent.mkdir(parents=True)
    path.write_text(GH_HOSTS_YML, encoding="utf-8")
    src = GhCliSource()
    cred = src.try_import(tmp_path)[0]
    text = src.removal_steps(cred)[0].description
    assert str(path) in text and ".config" not in text


def test_parse_hosts_yml_quoted_value():
    text = "github.com:\n    user: 'alice'\n    oauth_token: \"gho_X\"\n"
    parsed = _parse_hosts_yml(text)
    assert parsed["github.com"]["user"] == "alice"
    assert parsed["github.com"]["oauth_token"] == "gho_X"


def test_parse_hosts_yml_ignores_comments_and_blank():
    text = """\
# top-level comment
github.com:
    # inline comment
    oauth_token: gho_x
    user: alice

"""
    parsed = _parse_hosts_yml(text)
    assert parsed == {"github.com": {"oauth_token": "gho_x", "user": "alice"}}
