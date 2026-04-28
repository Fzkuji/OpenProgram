"""``openprogram channels setup`` end-to-end wizard.

The wizard chains together account creation, channel-specific login,
agent binding, and (optionally) worker spawn. We test the
orchestration with the channel-specific login functions monkey-
patched, since the real flow needs a phone (WeChat), a BotFather
token (Telegram), etc.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from openprogram.channels import setup as ch_setup


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``get_state_dir`` so accounts/bindings persist under tmp."""
    monkeypatch.setattr("openprogram.paths.get_state_dir",
                        lambda: tmp_path)
    return tmp_path


@pytest.fixture
def stub_agents(monkeypatch: pytest.MonkeyPatch):
    """Pretend there's a 'main' and a 'research' agent registered."""
    class _Spec:
        def __init__(self, _id: str) -> None:
            self.id = _id

    monkeypatch.setattr(
        "openprogram.agents.manager.list_all",
        lambda: [_Spec("main"), _Spec("research")],
        raising=False,
    )


def _stub_prompts(monkeypatch: pytest.MonkeyPatch, *,
                  channel="wechat", account="default",
                  agent="main", routing_label=None,
                  start_worker=False):
    """Replace the questionary helpers with deterministic stubs.

    Each call to ``_choose_one`` returns the next queued answer; ditto
    ``_text``, ``_confirm``. Tests that need a different sequence
    pass their own.
    """
    choose_answers = [channel]                 # 1. channel
    if account is not None:
        choose_answers.append(account)         # 2. account_id
    choose_answers.append(agent)               # 3. agent
    if routing_label is None:
        # default: catch-all at index 0
        routing_label_resolver = lambda choices: choices[0]
    else:
        routing_label_resolver = lambda choices: next(
            (c for c in choices if routing_label in c), choices[0])

    text_answers = []
    confirm_answers = [start_worker]

    state = {"choose": iter(choose_answers),
             "text": iter(text_answers),
             "confirm": iter(confirm_answers)}

    def _choose_stub(prompt, choices, default=None):
        try:
            ans = next(state["choose"])
        except StopIteration:
            ans = routing_label_resolver(choices)
            return ans
        # Channel-account picker: the wizard prefixes existing accounts
        # with " (existing)". Match plain id forms too.
        for c in choices:
            if ans == c or c.startswith(f"{ans} (") or c.startswith(f"{ans}"):
                return c
        # Routing rule prompt — ans isn't in choices, fall through to
        # routing resolver.
        return routing_label_resolver(choices)

    def _text_stub(prompt, default=""):
        try:
            return next(state["text"])
        except StopIteration:
            return default

    def _confirm_stub(prompt, default=True):
        try:
            return next(state["confirm"])
        except StopIteration:
            return default

    monkeypatch.setattr(ch_setup, "_choose_one", _choose_stub)
    monkeypatch.setattr(ch_setup, "_text", _text_stub)
    monkeypatch.setattr(ch_setup, "_confirm", _confirm_stub)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_setup_wechat_full_flow(state_dir, stub_agents,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """First-time wechat setup: no existing account, login fakes the
    QR scan, catch-all binding, don't start the worker."""
    _stub_prompts(monkeypatch,
                   channel="wechat", account="default",
                   agent="main", start_worker=False)

    # Stub the wechat login to skip the actual QR network call
    def _fake_login(account_id):
        from openprogram.channels import accounts as a
        a.save_credentials("wechat", account_id, {
            "bot_token": "fake-token",
            "ilink_bot_id": "bot-1234",
            "baseurl": "https://x",
            "ilink_user_id": "user-1234",
        })
        return {"bot_token": "fake-token", "ilink_bot_id": "bot-1234"}
    monkeypatch.setattr("openprogram.channels.wechat.login_account",
                        _fake_login)

    rc = ch_setup.run()
    assert rc == 0

    # Account file landed
    from openprogram.channels import accounts
    assert accounts.is_configured("wechat", "default")

    # Catch-all binding added
    from openprogram.channels import bindings
    rules = bindings.list_all()
    assert len(rules) == 1
    assert rules[0]["agent_id"] == "main"
    assert rules[0]["match"] == {"channel": "wechat", "account_id": "default"}


def test_setup_skips_login_when_already_configured(
    state_dir, stub_agents, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-running setup on an account that already has credentials
    should NOT prompt for QR again — just walk the user through
    routing + worker steps."""
    from openprogram.channels import accounts
    accounts.create("wechat", "default")
    accounts.save_credentials("wechat", "default", {
        "bot_token": "preexisting", "ilink_bot_id": "bot-9",
    })

    _stub_prompts(monkeypatch, channel="wechat", account="default",
                   agent="main", start_worker=False)

    login_calls = []
    monkeypatch.setattr(
        "openprogram.channels.wechat.login_account",
        lambda account_id: login_calls.append(account_id) or {"bot_token": "x"},
    )

    rc = ch_setup.run()
    assert rc == 0
    assert login_calls == [], "login should be skipped when configured"


def test_setup_telegram_token_path(state_dir, stub_agents,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Telegram setup: paste a BotFather token instead of QR.
    No existing telegram account → wizard asks for an account name
    first (we accept the default), then for the bot token."""
    _stub_prompts(monkeypatch, channel="telegram", account="default",
                   agent="main", start_worker=False)
    # Two _text calls: account name (defaults to 'default'), then
    # the bot token.
    text_answers = iter(["default", "1234:abcde-token"])

    def _text_stub(prompt, default=""):
        try:
            return next(text_answers)
        except StopIteration:
            return default
    monkeypatch.setattr(ch_setup, "_text", _text_stub)

    rc = ch_setup.run()
    assert rc == 0

    from openprogram.channels import accounts
    creds = accounts.load_credentials("telegram", "default")
    assert creds["bot_token"] == "1234:abcde-token"


def test_setup_aborts_when_no_agents(state_dir,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """If the user has no agents configured, the wizard should print
    a helpful message and exit non-zero rather than attempt to bind
    to a phantom agent."""
    monkeypatch.setattr("openprogram.agents.manager.list_all",
                        lambda: [], raising=False)
    _stub_prompts(monkeypatch, channel="wechat", account="default",
                   agent=None)

    monkeypatch.setattr(
        "openprogram.channels.wechat.login_account",
        lambda account_id: {"bot_token": "fake", "ilink_bot_id": "bot"},
    )

    rc = ch_setup.run()
    assert rc == 1


def test_setup_skip_binding_keeps_clean_state(
    state_dir, stub_agents, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Choosing 'skip' on the routing prompt should still finish setup
    successfully and leave bindings empty so the user can /attach
    later from the TUI."""
    _stub_prompts(monkeypatch, channel="wechat", account="default",
                   agent="main", routing_label="Skip",
                   start_worker=False)

    monkeypatch.setattr(
        "openprogram.channels.wechat.login_account",
        lambda account_id: {"bot_token": "fake", "ilink_bot_id": "bot"},
    )

    rc = ch_setup.run()
    assert rc == 0

    from openprogram.channels import bindings
    assert bindings.list_all() == []


def test_setup_keyboard_interrupt_returns_aborted(
    state_dir, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl-C anywhere in the wizard should land at the top-level
    handler and exit cleanly with code 1."""
    def _raises(*a, **kw):
        raise KeyboardInterrupt()
    monkeypatch.setattr(ch_setup, "_choose_one", _raises)

    rc = ch_setup.run()
    assert rc == 1
