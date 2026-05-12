"""Channels section: list / add / edit / delete per-channel accounts."""
from __future__ import annotations

from typing import Any


_CHANNEL_LABELS = {
    "telegram": "Telegram",
    "discord":  "Discord",
    "slack":    "Slack (Socket Mode)",
    "wechat":   "WeChat (personal, QR login)",
}


def _add_telegram_account(account_id: str) -> None:
    from openprogram.setup import _password
    from openprogram.channels import accounts as _accts
    if _accts.get("telegram", account_id) is None:
        _accts.create("telegram", account_id)
    tok = _password(f"Telegram bot token for account `{account_id}`:")
    if tok:
        _accts.update_credentials("telegram", account_id, {"bot_token": tok})


def _add_discord_account(account_id: str) -> None:
    from openprogram.setup import _password
    from openprogram.channels import accounts as _accts
    if _accts.get("discord", account_id) is None:
        _accts.create("discord", account_id)
    tok = _password(f"Discord bot token for account `{account_id}`:")
    if tok:
        _accts.update_credentials("discord", account_id, {"bot_token": tok})


def _add_slack_account(account_id: str) -> None:
    from openprogram.setup import _password
    from openprogram.channels import accounts as _accts
    if _accts.get("slack", account_id) is None:
        _accts.create("slack", account_id)
    bot = _password(f"Slack bot token (xoxb-...) for `{account_id}`:")
    app = _password(f"Slack app-level token (xapp-...) for `{account_id}`:")
    patch: dict[str, Any] = {}
    if bot:
        patch["bot_token"] = bot
    if app:
        patch["app_token"] = app
    if patch:
        _accts.update_credentials("slack", account_id, patch)


def _add_wechat_account(account_id: str) -> None:
    from openprogram.channels import accounts as _accts
    from openprogram.channels.wechat import login_account
    if _accts.get("wechat", account_id) is None:
        _accts.create("wechat", account_id)
    print(f"[wechat] logging in account `{account_id}` — scan the QR "
          f"with your phone")
    login_account(account_id)


_NEW_ACCOUNT_FN = {
    "telegram": _add_telegram_account,
    "discord": _add_discord_account,
    "slack": _add_slack_account,
    "wechat": _add_wechat_account,
}


def _ask_channel() -> str | None:
    from openprogram.setup import _choose_one
    labels = [_CHANNEL_LABELS[k] for k in ("telegram", "discord",
                                            "slack", "wechat")]
    keys = ["telegram", "discord", "slack", "wechat"]
    picked = _choose_one("Pick a channel:", labels, labels[0])
    if picked is None:
        return None
    return keys[labels.index(picked)]


def _manage_channel_account(channel: str, account_id: str) -> None:
    """Top-level action menu for an existing channel account."""
    from openprogram.setup import _choose_one
    from openprogram.channels import accounts as _accts
    from openprogram.channels import bindings as _bindings
    configured = _accts.is_configured(channel, account_id)
    enabled = _accts.is_enabled(channel, account_id)
    label = (f"{_CHANNEL_LABELS.get(channel, channel)}:{account_id} "
             f"({'enabled' if enabled else 'disabled'}"
             f", {'configured' if configured else 'needs credentials'})")
    options = [
        "Re-enter credentials",
        "Disable" if enabled else "Enable",
        "Delete this account (credentials + bindings)",
        "Back",
    ]
    pick = _choose_one(label, options, options[-1])
    if pick in (None, "Back"):
        return
    if pick == "Re-enter credentials":
        fn = _NEW_ACCOUNT_FN.get(channel)
        if fn is not None:
            fn(account_id)
        return
    if pick == "Disable":
        _accts.set_enabled(channel, account_id, False)
        print(f"{channel}:{account_id} disabled")
        return
    if pick == "Enable":
        _accts.set_enabled(channel, account_id, True)
        print(f"{channel}:{account_id} enabled")
        return
    if pick.startswith("Delete"):
        confirm = _choose_one(
            f"Delete {channel}:{account_id} and its bindings?",
            ["Keep", "Delete"], "Keep",
        )
        if confirm == "Delete":
            _bindings.remove_for_account(channel, account_id)
            _accts.delete(channel, account_id)
            print(f"{channel}:{account_id} removed")


def run_channels_section() -> int:
    """List every channel account, add new ones, or edit existing.

    Account-oriented: each row is a ``(channel, account_id)`` pair.
    Multiple accounts per channel work out of the box.
    """
    from openprogram.setup import _choose_one, _text
    from openprogram.channels import accounts as _accts
    while True:
        rows = _accts.list_all_accounts()
        options: list[str] = []
        mapping: list[tuple[str, str]] = []
        for acct in rows:
            enabled = _accts.is_enabled(acct.channel, acct.account_id)
            configured = _accts.is_configured(acct.channel, acct.account_id)
            tags = []
            if enabled:
                tags.append("enabled")
            else:
                tags.append("disabled")
            tags.append("configured" if configured else "needs credentials")
            options.append(
                f"{_CHANNEL_LABELS.get(acct.channel, acct.channel)}:"
                f"{acct.account_id}  ({', '.join(tags)})"
            )
            mapping.append((acct.channel, acct.account_id))
        options.append("+ Add a channel account")
        mapping.append(("__add__", ""))
        options.append("Finished")
        mapping.append(("__done__", ""))

        picked = _choose_one("Channel accounts:", options, options[-1])
        if picked is None:
            return 0
        channel, account_id = mapping[options.index(picked)]
        if channel == "__done__":
            return 0
        if channel == "__add__":
            new_channel = _ask_channel()
            if new_channel is None:
                continue
            new_id = _text(
                "Account id (letters/numbers/-_, e.g. personal, work):",
                default="default",
            )
            if not new_id:
                continue
            try:
                _accts.create(new_channel, new_id)
            except ValueError as e:
                print(f"[warn] {e}")
                continue
            fn = _NEW_ACCOUNT_FN.get(new_channel)
            if fn is not None:
                fn(new_id)
            print(f"{new_channel}:{new_id} saved")
            continue
        _manage_channel_account(channel, account_id)
