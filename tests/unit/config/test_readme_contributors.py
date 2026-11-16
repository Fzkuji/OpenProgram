from __future__ import annotations

import hashlib

from scripts.publishing import retired_accounts
from scripts.publishing.update_readme_contributors import (
    is_listed_contributor,
    render_block,
    replace_block,
)


def test_omits_bots_and_ai_coding_accounts() -> None:
    assert is_listed_contributor({"login": "FuzzyKuji", "type": "User"})
    assert is_listed_contributor({"login": "Qi202", "type": "User"})
    assert is_listed_contributor({"login": "basil-k-aji-dev", "type": "User"})
    assert not is_listed_contributor({"login": "claude", "type": "User"})
    assert not is_listed_contributor({"login": "Claude", "type": "User"})
    assert not is_listed_contributor({"login": "dependabot[bot]", "type": "Bot"})
    assert not is_listed_contributor({"login": "renovate-bot", "type": "User"})
    assert not is_listed_contributor({"login": "", "type": "User"})


def test_readme_block_uses_github_pngs_and_omits_claude() -> None:
    block = render_block(["FuzzyKuji", "Qi202"])
    assert 'src="https://github.com/FuzzyKuji.png?size=48"' in block
    assert 'src="https://github.com/Qi202.png?size=48"' in block
    assert 'width="24"' in block
    assert "contrib.rocks" not in block
    assert "claude" not in block
    text = "before\n<!-- contributors-avatars -->\nold\n<!-- /contributors-avatars -->\nafter\n"
    updated = replace_block(text, block)
    assert updated.startswith("before\n")
    assert updated.endswith("after\n")
    assert "old" not in updated
    assert "FuzzyKuji.png" in updated


def test_never_lists_a_retired_account(monkeypatch) -> None:
    # A stand-in login takes a retired account's place, so this file names none.
    stand_in = "retired-example"
    digest = hashlib.sha256(stand_in.encode()).hexdigest()
    monkeypatch.setattr(retired_accounts, "RETIRED", retired_accounts.RETIRED | {digest})
    monkeypatch.setattr(retired_accounts, "_seen", {})
    assert not is_listed_contributor({"login": stand_in, "type": "User"})
    assert not is_listed_contributor({"login": "Retired-Example", "type": "User"})
    assert is_listed_contributor({"login": "FuzzyKuji", "type": "User"})
