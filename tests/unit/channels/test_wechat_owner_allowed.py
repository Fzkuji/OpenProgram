"""The WeChat user who scans a bot's QR code can talk to it right away."""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.interfaces.channels import _access
from openprogram.interfaces.channels import accounts as _accounts
from openprogram.interfaces.channels.implementations import wechat


@pytest.fixture(autouse=True)
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    return state


CREDS = {
    "bot_token": "token",
    "ilink_bot_id": "bot@im.bot",
    "baseurl": "https://example.invalid",
    "ilink_user_id": "owner@im.wechat",
}


def test_the_scanning_owner_is_allowed_and_others_still_pair(monkeypatch) -> None:
    _accounts.create("wechat", "baba")
    monkeypatch.setattr(wechat, "_qr_login", lambda: dict(CREDS))
    assert wechat.login_account("baba") == CREDS
    assert _accounts.load_credentials("wechat", "baba")["bot_token"] == "token"
    assert _access.decide_inbound_sender("wechat", "baba", "owner@im.wechat").allowed
    stranger = _access.decide_inbound_sender("wechat", "baba", "someone@im.wechat")
    assert not stranger.allowed


def test_a_login_without_an_owner_id_allows_no_one(monkeypatch) -> None:
    _accounts.create("wechat", "baba")
    monkeypatch.setattr(wechat, "_qr_login", lambda: {**CREDS, "ilink_user_id": ""})
    wechat.login_account("baba")
    assert not _access.decide_inbound_sender("wechat", "baba", "owner@im.wechat").allowed
