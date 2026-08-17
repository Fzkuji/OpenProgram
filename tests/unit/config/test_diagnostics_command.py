"""``openprogram diagnostics`` — bundle contents and redaction.

The redaction assertions are the point of this file. Each fake secret
below is grepped for across *every* file in the produced zip, because a
bundle is something users post into public bug trackers: a leak here is
a leak in the worst possible place.
"""
from __future__ import annotations

import json
import zipfile

import pytest

from openprogram import paths

# Fake credentials planted in config. Shapes chosen to cover both
# redaction layers: key-name matching (opaque values under a sensitive
# key) and value-shape matching (recognisable prefixes anywhere).
FAKE_SECRETS = {
    "anthropic_api_key": "sk-ant-api03-FAKEFAKEFAKEFAKEFAKEFAKE1234567890",
    "openai_api_key": "sk-proj-ZZZZFAKEZZZZFAKEZZZZFAKE0987654321",
    "github_token": "ghp_FAKEfakeFAKEfake0123456789ABCDEF",
    "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
    "google_api_key": "AIzaSyFAKEfakeFAKEfake0123456789ABCDEF",
    "slack_bot_token": "xoxb-FAKETOKEN-notreal-notreal",
    "session_password": "hunter2hunter2hunter2",
    "opaque_secret": "Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MA==",
}


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    """Point every state path at a temp dir with a planted config."""
    home = tmp_path / "home"
    (home / ".openprogram").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("OPENPROGRAM_PROFILE", raising=False)
    monkeypatch.setattr(paths, "_migration_checked", True)
    monkeypatch.setattr(paths, "_root_mode_checked", set())

    # The auth store is a process-wide singleton that binds its root at
    # first construction, so without this it keeps pointing at the real
    # ~/.openprogram no matter what HOME says.
    from openprogram.auth.store import AuthStore, set_store_for_testing
    set_store_for_testing(AuthStore(root=home / ".openprogram"))
    yield home
    set_store_for_testing(None)


def _write_config(home, extra=None):
    config = {
        "default_workdir": "/tmp/project",
        "providers": {
            "anthropic": {"api_key": FAKE_SECRETS["anthropic_api_key"]},
            "openai": {"openai_api_key": FAKE_SECRETS["openai_api_key"]},
        },
        "integrations": {
            "github_token": FAKE_SECRETS["github_token"],
            "aws_access_key_id": FAKE_SECRETS["aws_access_key_id"],
            "google_api_key": FAKE_SECRETS["google_api_key"],
            "slack_bot_token": FAKE_SECRETS["slack_bot_token"],
        },
        "session_password": FAKE_SECRETS["session_password"],
        "nested": [{"opaque_secret": FAKE_SECRETS["opaque_secret"]}],
    }
    if extra:
        config.update(extra)
    (home / ".openprogram" / "config.json").write_text(
        json.dumps(config), encoding="utf-8")


def _bundle_texts(zip_path):
    """Every file in the zip decoded as text, keyed by archive name."""
    with zipfile.ZipFile(zip_path) as zf:
        return {n: zf.read(n).decode("utf-8", "replace") for n in zf.namelist()}


def test_no_secret_survives_into_the_bundle(isolated_home, tmp_path):
    """No planted credential appears verbatim anywhere in the zip."""
    from openprogram.cli.commands.diagnostics import build_bundle

    _write_config(isolated_home)
    # A log line carrying a secret with no dict key to match on.
    logs = isolated_home / ".openprogram" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (isolated_home / ".openprogram" / "worker.log").write_text(
        "GET /v1/messages\n"
        f"Authorization: Bearer {FAKE_SECRETS['anthropic_api_key']}\n"
        f"resolved token={FAKE_SECRETS['opaque_secret']}\n",
        encoding="utf-8",
    )
    (logs / "runtime.log").write_text(
        f"probe failed for key {FAKE_SECRETS['github_token']}\n", encoding="utf-8")

    out = tmp_path / "bundle.zip"
    build_bundle(out)
    texts = _bundle_texts(out)

    for label, secret in FAKE_SECRETS.items():
        for name, body in texts.items():
            assert secret not in body, f"{label} leaked verbatim into {name}"


def test_credential_files_are_not_collected(isolated_home, tmp_path):
    """The auth store is reported by name and count, never by content."""
    from openprogram.cli.commands.diagnostics import build_bundle

    _write_config(isolated_home)
    auth = isolated_home / ".openprogram" / "auth" / "anthropic"
    auth.mkdir(parents=True)
    payload = "sk-ant-oauth-CREDENTIALFILECONTENTS999"
    (auth / "default.json").write_text(
        json.dumps({"access_token": payload}), encoding="utf-8")

    out = tmp_path / "bundle.zip"
    build_bundle(out)
    texts = _bundle_texts(out)

    for name, body in texts.items():
        assert payload not in body, f"credential file content leaked into {name}"

    report = json.loads(texts["credentials.json"])
    assert report["providers"] == {"anthropic": 1}


def test_bundle_has_expected_files_and_manifest(isolated_home, tmp_path):
    from openprogram.cli.commands.diagnostics import build_bundle

    _write_config(isolated_home)
    out = tmp_path / "bundle.zip"
    names = build_bundle(out)

    for expected in ("version.json", "config.json", "credentials.json",
                     "environment.json", "manifest.json"):
        assert expected in names

    texts = _bundle_texts(out)
    manifest = json.loads(texts["manifest.json"])
    listed = {f["name"] for f in manifest["files"]}
    # Manifest describes every file except itself.
    assert listed == set(names) - {"manifest.json"}
    assert all("source" in f for f in manifest["files"])

    # Non-secret config survives, so the snapshot is still useful.
    assert json.loads(texts["config.json"])["default_workdir"] == "/tmp/project"


def test_redact_text_leaves_ordinary_text_alone():
    from openprogram.cli.commands.diagnostics import redact_text

    line = "Traceback: FileNotFoundError: /Users/me/projects/app/main.py line 42"
    assert redact_text(line) == line


def test_command_prints_contents_and_review_warning(isolated_home, tmp_path, capsys):
    from openprogram.cli.commands.diagnostics import _cmd_diagnostics

    _write_config(isolated_home)
    out = tmp_path / "named.zip"
    assert _cmd_diagnostics(str(out)) == 0

    printed = capsys.readouterr().out
    assert "manifest.json" in printed
    assert "Review the contents yourself before sharing" in printed
    assert out.exists()


def test_parser_accepts_diagnostics_command():
    from openprogram.cli import build_parser

    args = build_parser().parse_args(["diagnostics", "--output", "/tmp/x.zip"])
    assert (args.command, args.output) == ("diagnostics", "/tmp/x.zip")
