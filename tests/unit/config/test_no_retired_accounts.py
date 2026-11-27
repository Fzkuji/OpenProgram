"""No tracked file names one of the project's retired GitHub accounts.

The names live only as hashes in ``scripts/publishing/retired_accounts.py``, so neither
this test nor the scanner is itself a mention. ``publish-github.sh`` runs the
same scanner over the commit it publishes.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.publishing import retired_accounts

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.slow
def test_no_tracked_file_names_a_retired_account() -> None:
    hits = retired_accounts.scan(retired_accounts.checkout_files(ROOT))
    assert not hits, "these lines name a retired account; remove the mention:\n" + "\n".join(hits)


@pytest.fixture
def stand_in(monkeypatch: pytest.MonkeyPatch) -> str:
    """A made-up login treated as retired, so the checks run without the real names."""
    login = "retired-example"
    digests = {hashlib.sha256(value.encode()).hexdigest() for value in (login, "991122")}
    monkeypatch.setattr(retired_accounts, "RETIRED", retired_accounts.RETIRED | digests)
    monkeypatch.setattr(retired_accounts, "_seen", {})
    monkeypatch.setattr(retired_accounts, "_clean", set())
    return login


@pytest.mark.parametrize(
    "text",
    [
        "© 2026 Retired-Example.",
        "see https://github.com/retired-example/Some-Repo",
        "https://github.com/retired-example.png?size=48",
        "991122+retired-example@users.noreply.github.com",
        "uddg=https%3A%2F%2Fgithub.com%2Fretired-example%2Frepo",
        '{"author": "RETIRED-EXAMPLE"}',
        "作者：retired-example。",
        "uddg=https%253A%252F%252Fgithub.com%252Fretired-example",
        "<a>&#114;etired-example</a>",
        '"\\u0072etired-example"',
    ],
)
def test_a_mention_is_found_however_it_is_written(stand_in: str, text: str) -> None:
    assert retired_accounts.names_retired_account(text)


@pytest.mark.parametrize("text", ["retired-examples", "Fzkuji/OpenProgram", "9911223", "a991122b"])
def test_a_longer_word_that_contains_one_is_not_a_mention(stand_in: str, text: str) -> None:
    assert not retired_accounts.names_retired_account(text)


def test_the_main_login_is_found_inside_a_longer_word(monkeypatch: pytest.MonkeyPatch) -> None:
    stem = "stemlogin"
    monkeypatch.setattr(
        retired_accounts, "STEMS",
        retired_accounts.STEMS | {(len(stem), hashlib.sha256(stem.encode()).hexdigest())},
    )
    monkeypatch.setattr(retired_accounts, "_seen", {})
    monkeypatch.setattr(retired_accounts, "_clean", set())
    assert retired_accounts.names_retired_account("by StemLoginNeo, 2026")
    assert retired_accounts.names_retired_account("/Users/xstemlogin2/Projects")
    assert not retired_accounts.names_retired_account("stem login")


def test_scan_names_the_line_and_a_path_that_is_itself_a_mention(stand_in: str) -> None:
    hits = retired_accounts.scan([
        ("README.md", b"first\nby retired-example\n"),
        ("docs/retired-example/notes.md", b"clean\n"),
        ("image.png", b"\x89PNG\0retired-example"),
        ("link", None),
    ])
    assert hits == ["README.md:2", "docs/retired-example/notes.md"]


def test_the_scanner_does_not_name_the_accounts_it_looks_for() -> None:
    source = (ROOT / "scripts/publishing/retired_accounts.py").read_text(encoding="utf-8")
    assert not retired_accounts.names_retired_account(source)
    assert len(retired_accounts.RETIRED) == 3
