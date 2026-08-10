from __future__ import annotations

import os
import stat
import subprocess

import pytest

from openprogram.commands.commit_message import (
    PR_FOOTER,
    append_pr_footer,
    apply_trailers,
    co_author_trailer,
    gh_pr_create_argv,
    pr_body,
)


# co_author_trailer


def test_trailer_format_is_exact():
    assert co_author_trailer(enabled=True) == (
        "Co-Authored-By: OpenProgram <noreply@openprogram.dev>"
    )


def test_trailer_uses_model_display_name_when_known():
    assert co_author_trailer("Claude Opus 5", enabled=True) == (
        "Co-Authored-By: Claude Opus 5 <noreply@openprogram.dev>"
    )


def test_blank_model_falls_back_to_generic_identity():
    assert co_author_trailer("   ", enabled=True) == (
        "Co-Authored-By: OpenProgram <noreply@openprogram.dev>"
    )


def test_toggle_off_returns_none_and_leaves_message_untouched():
    assert co_author_trailer(enabled=False) is None
    assert apply_trailers("Fix the thing", co_author=None) == "Fix the thing"


def test_toggle_reads_git_co_author_config(monkeypatch: pytest.MonkeyPatch):
    import openprogram.setup as setup

    monkeypatch.setattr(setup, "_read_config", lambda: {"git": {"co_author": False}})
    assert co_author_trailer() is None
    monkeypatch.setattr(setup, "_read_config", lambda: {})
    assert co_author_trailer() is not None


# apply_trailers


TRAILER = "Co-Authored-By: OpenProgram <noreply@openprogram.dev>"


def test_blank_line_inserted_before_trailer_when_body_is_prose():
    out = apply_trailers("Add thing\n\nBecause reasons.", co_author=TRAILER)
    assert out == "Add thing\n\nBecause reasons.\n\n" + TRAILER


def test_no_blank_line_when_message_already_ends_in_a_trailer_block():
    msg = "Add thing\n\nBecause reasons.\n\nSigned-off-by: Ada <ada@example.com>"
    out = apply_trailers(msg, co_author=TRAILER)
    assert out == msg + "\n" + TRAILER


def test_apply_trailers_is_idempotent():
    once = apply_trailers("Add thing", co_author=TRAILER)
    assert apply_trailers(once, co_author=TRAILER) == once


def test_trailing_whitespace_is_normalised():
    out = apply_trailers("Add thing\n\n\n   \n", co_author=TRAILER)
    assert out == "Add thing\n\n" + TRAILER


def test_subject_only_message_gets_a_blank_line():
    assert apply_trailers("Add thing", co_author=TRAILER) == "Add thing\n\n" + TRAILER


def test_empty_message_becomes_the_trailer_alone():
    assert apply_trailers("", co_author=TRAILER) == TRAILER


# pr_body


def test_pr_body_has_the_sections_and_one_footer():
    body = pr_body("Why.", changes=["a — b"], testing=["pytest"])
    assert "## Summary" in body
    assert "## What changed" in body
    assert "## Testing" in body
    assert body.endswith(PR_FOOTER)
    assert body.count(PR_FOOTER) == 1


def test_pr_body_omits_empty_sections():
    body = pr_body("Why.")
    assert "## What changed" not in body
    assert "## Testing" not in body
    assert body.endswith(PR_FOOTER)


def test_pr_footer_is_idempotent():
    once = pr_body("Why.")
    assert append_pr_footer(once) == once
    assert append_pr_footer(once + "\n\n") == once


# gh argv


def test_gh_pr_create_argv_is_exact():
    assert gh_pr_create_argv(
        base="main", head="topic", title="Add thing", body_file="/tmp/b.md",
    ) == [
        "gh", "pr", "create",
        "--base", "main",
        "--head", "topic",
        "--title", "Add thing",
        "--body-file", "/tmp/b.md",
    ]
    assert gh_pr_create_argv(
        base="main", head="topic", title="t", body_file="/tmp/b.md", draft=True,
    )[-1] == "--draft"


def test_gh_pr_create_argv_runs_against_a_fake_gh(tmp_path, monkeypatch):
    recorded = tmp_path / "argv.txt"
    fake = tmp_path / "gh"
    fake.write_text(
        '#!/bin/sh\nfor a in "$@"; do echo "$a" >> "%s"; done\n'
        'echo https://example.invalid/pr/1\n' % recorded,
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    body = tmp_path / "body.md"
    body.write_text(pr_body("Why."), encoding="utf-8")
    argv = gh_pr_create_argv(
        base="main", head="topic", title="Add thing", body_file=str(body),
    )
    result = subprocess.run(argv, capture_output=True, text=True, check=True)

    assert result.stdout.strip() == "https://example.invalid/pr/1"
    assert recorded.read_text(encoding="utf-8").split("\n")[:-1] == argv[1:]
