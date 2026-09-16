from __future__ import annotations

from scripts.docs_site.contributors import is_listed_contributor, render_svg


def test_omits_bots_and_ai_coding_accounts() -> None:
    assert is_listed_contributor({"login": "Fzkuji", "type": "User"})
    assert is_listed_contributor({"login": "Qi202", "type": "User"})
    assert is_listed_contributor({"login": "basil-k-aji-dev", "type": "User"})
    assert not is_listed_contributor({"login": "claude", "type": "User"})
    assert not is_listed_contributor({"login": "Claude", "type": "User"})
    assert not is_listed_contributor({"login": "dependabot[bot]", "type": "Bot"})
    assert not is_listed_contributor({"login": "renovate-bot", "type": "User"})
    assert not is_listed_contributor({"login": "", "type": "User"})


def test_svg_lists_people_and_skips_empty_sheet_placeholder_when_populated() -> None:
    svg = render_svg(
        [{"login": "Fzkuji"}, {"login": "Qi202"}],
        [None, None],
    )
    assert 'aria-label="OpenProgram contributors"' in svg
    assert ">Fzkuji</text>" in svg
    assert ">Qi202</text>" in svg
    assert "claude" not in svg
    assert "graphs/contributors" not in svg
    long = render_svg([{"login": "binyangzhu000-sudo"}], [None])
    assert "binyangzhu00" in long
    assert 'r="16"' in long
