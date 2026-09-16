from __future__ import annotations

from scripts.update_readme_contributors import (
    is_listed_contributor,
    render_block,
    replace_block,
)


def test_omits_bots_and_ai_coding_accounts() -> None:
    assert is_listed_contributor({"login": "Fzkuji", "type": "User"})
    assert is_listed_contributor({"login": "Qi202", "type": "User"})
    assert is_listed_contributor({"login": "basil-k-aji-dev", "type": "User"})
    assert not is_listed_contributor({"login": "claude", "type": "User"})
    assert not is_listed_contributor({"login": "Claude", "type": "User"})
    assert not is_listed_contributor({"login": "dependabot[bot]", "type": "Bot"})
    assert not is_listed_contributor({"login": "renovate-bot", "type": "User"})
    assert not is_listed_contributor({"login": "", "type": "User"})


def test_readme_block_uses_github_pngs_and_omits_claude() -> None:
    block = render_block(["Fzkuji", "Qi202"])
    assert 'src="https://github.com/Fzkuji.png?size=48"' in block
    assert 'src="https://github.com/Qi202.png?size=48"' in block
    assert 'width="24"' in block
    assert "contrib.rocks" not in block
    assert "claude" not in block
    text = "before\n<!-- contributors-avatars -->\nold\n<!-- /contributors-avatars -->\nafter\n"
    updated = replace_block(text, block)
    assert updated.startswith("before\n")
    assert updated.endswith("after\n")
    assert "old" not in updated
    assert "Fzkuji.png" in updated
