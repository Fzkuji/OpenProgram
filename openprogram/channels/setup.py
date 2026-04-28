"""Channels setup wizard — one command runs the full enrollment.

Replaces the four-step ``accounts add`` → ``accounts login`` →
``bindings add`` → ``channels start`` ceremony with a single
interactive flow:

    $ openprogram channels setup

    ? Which channel?
      ❯ wechat / telegram / discord / slack
    ? Account name? default
    [scan QR / enter token]
    ? Which agent should handle messages? main
    ? Routing? Catch-all / per-peer / skip
    ? Start the worker? Y/n

Reuses ``openprogram.setup``'s questionary helpers so the look
matches the global setup wizard.
"""
from __future__ import annotations

import time
from typing import Optional

from openprogram.channels import accounts as _accounts
from openprogram.channels import bindings as _bindings
from openprogram.channels import worker as _worker
from openprogram.setup import _choose_one, _confirm, _text


SUPPORTED = ("wechat", "telegram", "discord", "slack")


def run() -> int:
    """Drive the interactive flow. Returns shell-style exit code:
    0 = OK, 1 = aborted, 2 = unknown error."""
    print()
    print("OpenProgram channels setup")
    print("─" * 30)

    try:
        channel = _pick_channel()
        if channel is None:
            return 1

        account_id = _pick_account_id(channel)
        if account_id is None:
            return 1

        # Login flow if we don't have working credentials yet.
        if not _accounts.is_configured(channel, account_id):
            ok = _run_login(channel, account_id)
            if not ok:
                print(f"\n[setup] login for {channel}:{account_id} did not complete.")
                return 1
        else:
            print(f"\n[setup] {channel}:{account_id} already has saved "
                  f"credentials — skipping login.")

        # Routing — pick agent + match rule
        agent_id = _pick_agent()
        if agent_id is None:
            return 1

        if not _add_binding(channel, account_id, agent_id):
            return 1

        # Start the worker?
        running = _worker.current_worker_pid() is not None
        if running:
            print("\n[setup] channels worker is already running — "
                  "no need to restart.")
        else:
            if _confirm("Start the channels worker now?", default=True):
                pid = _worker.spawn_detached()
                print(f"\n[setup] worker started, PID {pid}")
                print("[setup] log: ~/.agentic/channels/worker.log")
            else:
                print("\n[setup] worker not started — run "
                      "`openprogram channels start` when ready.")

        print("\n✅ Setup complete. Send a message from the channel — "
              "watch the conversation appear in /resume.")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\n[setup] aborted.")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"\n[setup] unexpected error: {type(e).__name__}: {e}")
        return 2


# ---------------------------------------------------------------------------
# Step pickers
# ---------------------------------------------------------------------------

def _pick_channel() -> Optional[str]:
    return _choose_one("Which channel do you want to connect?",
                        list(SUPPORTED), default="wechat")


def _pick_account_id(channel: str) -> Optional[str]:
    """Pick which account_id to set up. Existing accounts are listed
    so re-running setup updates instead of duplicating."""
    existing = [a.account_id for a in _accounts.list_accounts(channel)]
    NEW_LABEL = "[+ new account]"

    if not existing:
        # No accounts yet — straight to "name your account".
        name = _text("Account name (a label, like 'personal' or 'work')",
                     default="default")
        if not name:
            return None
        name = name.strip().lower()
        if not name:
            return None
        # Create the account row so subsequent steps find it.
        try:
            _accounts.create(channel, name)
        except ValueError as e:
            print(f"[setup] {e}")
            return None
        return name

    choices = [f"{aid} (existing)" for aid in existing] + [NEW_LABEL]
    pick = _choose_one(
        f"This {channel} channel has existing accounts. Use one or add a new one?",
        choices, default=choices[0])
    if pick is None:
        return None
    if pick == NEW_LABEL:
        name = _text("Account name (a label, like 'personal' or 'work')",
                     default="default" if "default" not in existing else "")
        if not name:
            return None
        name = name.strip().lower()
        if not name:
            return None
        try:
            _accounts.create(channel, name)
        except ValueError as e:
            print(f"[setup] {e}")
            return None
        return name
    return pick.split(" ", 1)[0]   # strip "(existing)" suffix


def _run_login(channel: str, account_id: str) -> bool:
    """Channel-specific login: WeChat scans QR, telegram/discord/slack
    each have their own enrollment. WeChat is the only one currently
    wired into the wizard — others surface a helpful message."""
    if channel == "wechat":
        from openprogram.channels.wechat import login_account
        creds = login_account(account_id)
        return creds is not None
    if channel == "telegram":
        return _telegram_login(account_id)
    if channel == "discord":
        return _discord_login(account_id)
    if channel == "slack":
        return _slack_login(account_id)
    return False


def _telegram_login(account_id: str) -> bool:
    print("\n[telegram] Open https://t.me/BotFather, run /newbot, then paste"
          " the token below. (Existing tokens fine — just paste the same.)")
    token = _text("Bot token", default="")
    if not token or not token.strip():
        return False
    _accounts.save_credentials("telegram", account_id,
                                {"bot_token": token.strip()})
    print(f"[telegram] saved token for telegram:{account_id}")
    return True


def _discord_login(account_id: str) -> bool:
    print("\n[discord] Create a Discord application + bot at "
          "https://discord.com/developers/applications. Paste the bot token "
          "below.")
    token = _text("Bot token", default="")
    if not token or not token.strip():
        return False
    _accounts.save_credentials("discord", account_id,
                                {"bot_token": token.strip()})
    print(f"[discord] saved token for discord:{account_id}")
    return True


def _slack_login(account_id: str) -> bool:
    print("\n[slack] Create a Slack app at https://api.slack.com/apps, "
          "install it to your workspace, then paste the Bot User OAuth Token "
          "(starts with xoxb-).")
    token = _text("Bot token", default="")
    if not token or not token.strip():
        return False
    _accounts.save_credentials("slack", account_id,
                                {"bot_token": token.strip()})
    print(f"[slack] saved token for slack:{account_id}")
    return True


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _pick_agent() -> Optional[str]:
    """List existing agents; let the user pick one. Bail with a helpful
    message if there are none configured."""
    try:
        from openprogram.agents import manager as _agents
        listed = _agents.list_all() if hasattr(_agents, "list_all") else []
    except Exception:
        listed = []
    ids = [a.id if hasattr(a, "id") else a.get("id") for a in listed]
    ids = [i for i in ids if i]
    if not ids:
        print("\n[setup] no agents configured. Run "
              "`openprogram agents add main` first, then re-run "
              "`openprogram channels setup`.")
        return None
    pick = _choose_one("Which agent should handle messages from this channel?",
                       ids, default=ids[0])
    return pick


def _add_binding(channel: str, account_id: str, agent_id: str) -> bool:
    SKIP = "Skip — I'll bind specific peers later from the TUI"
    CATCHALL = (f"Catch-all: every message on {channel}:{account_id} "
                f"goes to {agent_id}")
    PER_PEER = "Per-peer: only specified peers route here"

    pick = _choose_one("Routing rule for inbound messages?",
                       [CATCHALL, PER_PEER, SKIP], default=CATCHALL)
    if pick is None:
        return False
    if pick == SKIP:
        print(f"\n[setup] no binding added. Use the TUI's /channel command "
              f"or `openprogram channels bindings add` later.")
        return True
    if pick == CATCHALL:
        try:
            _bindings.add(agent_id, {
                "channel": channel,
                "account_id": account_id,
            })
            print(f"\n[setup] catch-all binding added: "
                  f"{channel}:{account_id} → {agent_id}")
        except Exception as e:  # noqa: BLE001
            print(f"[setup] binding failed: {e}")
            return False
        return True
    # Per-peer: walk the user through adding one or more peer ids
    return _add_per_peer_bindings(channel, account_id, agent_id)


def _add_per_peer_bindings(channel: str, account_id: str,
                            agent_id: str) -> bool:
    print(f"\nEnter peer IDs (one per line, blank line to finish).")
    print("For WeChat the peer id looks like wxid_xxx; you'll see them in "
          "the worker log once messages start arriving.")
    added = 0
    while True:
        peer_id = _text("Peer ID (or blank to stop)", default="")
        if peer_id is None:
            break
        peer_id = peer_id.strip()
        if not peer_id:
            break
        try:
            _bindings.add(agent_id, {
                "channel": channel,
                "account_id": account_id,
                "peer": {"kind": "direct", "id": peer_id},
            })
            print(f"  ✅ {channel}:{account_id}:{peer_id} → {agent_id}")
            added += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ❌ {peer_id}: {e}")
    if added == 0:
        print("\n[setup] no per-peer bindings added — channel is connected "
              "but no inbound routing yet.")
    return True
