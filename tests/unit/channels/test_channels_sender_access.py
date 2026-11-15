"""``sender_access``: what an approved chat sender may do.

``owner`` (the default) treats the sender as the local owner — tools and
approvals as in the web UI; ``paired`` limits the turn to replies and
memory.
"""
from __future__ import annotations

import pytest

from openprogram.runtime.permissions.authority import (
    AuthorityError,
    channel_owner_authority,
    owner_principal_id,
)
from openprogram.state.authority import has_capability, normalize_authority
from openprogram.runtime.permissions.tool_authority import decide_tool_authority
from openprogram.interfaces.channels import accounts


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)


def test_owner_access_is_the_local_owner_with_the_speaker_named() -> None:
    auth = normalize_authority(
        channel_owner_authority("wechat", "a1", "u@im.wechat", "Me"))
    assert auth["principal_id"] == owner_principal_id()
    assert (auth["authority_tier"], auth["speaker_kind"], auth["interaction"]) == (
        "owner", "owner", "interactive")
    assert (auth["speaker_id"], auth["speaker_display"]) == ("u@im.wechat", "Me")
    assert has_capability(auth, "approval.request")
    for tool in ("bash", "write", "send_file", "chat_send"):
        assert decide_tool_authority(auth, tool).allowed, tool


def test_owner_access_still_needs_platform_ids() -> None:
    with pytest.raises(AuthorityError):
        channel_owner_authority("wechat", "a1", "", "Me")


def test_sender_access_defaults_to_owner_and_takes_paired() -> None:
    assert accounts.sender_access("wechat", "a1") == "owner"
    accounts.set_setting("wechat", "a1", "sender_access", "paired")
    assert accounts.sender_access("wechat", "a1") == "paired"
    with pytest.raises(ValueError):
        accounts.set_setting("wechat", "a1", "sender_access", "admin")
