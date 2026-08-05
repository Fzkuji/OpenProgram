"""``openprogram channels`` accounts + bindings dispatchers + login flow."""
from __future__ import annotations

import sys


def _dispatch_accounts_verb(args, parser) -> None:
    from openprogram.channels import accounts as _acc
    verb = getattr(args, "accounts_verb", None)
    if verb == "list":
        rows = _acc.list_all_accounts()
        if not rows:
            print("No channel accounts. "
                  "Run `openprogram channels accounts add <channel>`.")
            return
        print(f"{'channel':10} {'account':14} {'name':20} "
              f"{'enabled':8} configured")
        for a in rows:
            print(f"{a.channel:10} {a.account_id:14} {a.name[:19]:20} "
                  f"{str(_acc.is_enabled(a.channel, a.account_id)):8} "
                  f"{_acc.is_configured(a.channel, a.account_id)}")
        return
    if verb == "add":
        try:
            _acc.create(args.channel, args.id)
        except ValueError as e:
            print(f"[error] {e}")
            sys.exit(1)
        print(f"Created {args.channel}:{args.id}. "
              f"Now set credentials with "
              f"`openprogram channels accounts login {args.channel} "
              f"--id {args.id}`.")
        return
    if verb == "rm":
        from openprogram.channels import bindings as _b
        _b.remove_for_account(args.channel, args.account_id)
        _acc.delete(args.channel, args.account_id)
        print(f"Removed {args.channel}:{args.account_id} (and its bindings)")
        return
    if verb == "login":
        _login_account(args.channel, args.id)
        return
    if verb == "set":
        try:
            _acc.set_setting(args.channel, args.id, args.key, args.value)
        except ValueError as e:
            print(f"[error] {e}")
            sys.exit(1)
        print(f"{args.channel}:{args.id} {args.key} = {args.value} "
              f"(restart the worker to apply)")
        return
    parser.print_help()


def _dispatch_access_verb(args, parser) -> None:
    from openprogram.channels import _access
    verb = getattr(args, "access_verb", None)
    if verb == "list":
        from openprogram.channels import accounts as _acc
        channels = [args.channel] if getattr(args, "channel", None) else None
        rows = []
        for acct in _acc.list_all_accounts():
            if channels and acct.channel not in channels:
                continue
            rows.append((acct.channel, acct.account_id,
                         _access.describe(acct.channel, acct.account_id)))
        if not rows:
            print("No channel accounts.")
            return
        for channel, account_id, data in rows:
            print(f"{channel}:{account_id}  policy={data['policy']}")
            for uid, row in sorted(data["allowlist"].items()):
                print(f"  allowed  {uid:24} {row.get('display','')}")
            for uid, row in sorted(data["pending"].items()):
                print(f"  pending  {uid:24} {row.get('display','')}  "
                      f"code={row.get('code','')}")
        return
    if verb == "approve":
        uid = _access.approve(args.channel, args.id, args.code)
        if uid is None:
            print(f"[error] no pending pairing with code {args.code!r} "
                  f"on {args.channel}:{args.id} (codes expire after 1h)")
            sys.exit(1)
        print(f"Approved {uid} on {args.channel}:{args.id}")
        return
    if verb == "allow":
        _access.approve_user(args.channel, args.id, args.user_id)
        print(f"Allowlisted {args.user_id} on {args.channel}:{args.id}")
        return
    if verb == "revoke":
        if _access.revoke(args.channel, args.id, args.user_id):
            print(f"Revoked {args.user_id} on {args.channel}:{args.id}")
        else:
            print(f"{args.user_id} was not on {args.channel}:{args.id}")
        return
    if verb == "policy":
        _access.set_policy(args.channel, args.id, args.policy)
        print(f"{args.channel}:{args.id} access policy = {args.policy}")
        return
    parser.print_help()


def _login_account(channel: str, account_id: str) -> None:
    """Interactive credential entry for one account.

    Telegram/Discord/Slack take tokens (env paste); WeChat does the QR flow.
    """
    from openprogram.channels import accounts as _acc
    if _acc.get(channel, account_id) is None:
        _acc.create(channel, account_id)
    if channel == "wechat":
        from openprogram.channels.implementations.wechat import login_account
        login_account(account_id)
        return
    import getpass
    if channel == "telegram":
        tok = getpass.getpass("Telegram bot token: ")
        _acc.update_credentials("telegram", account_id, {"bot_token": tok})
    elif channel == "discord":
        tok = getpass.getpass("Discord bot token: ")
        _acc.update_credentials("discord", account_id, {"bot_token": tok})
    elif channel == "slack":
        bot = getpass.getpass("Slack bot token (xoxb-...): ")
        app = getpass.getpass("Slack app-level token (xapp-...): ")
        patch: dict = {}
        if bot:
            patch["bot_token"] = bot
        if app:
            patch["app_token"] = app
        if patch:
            _acc.update_credentials("slack", account_id, patch)
    else:
        print(f"Unknown channel {channel!r}")
        sys.exit(1)
    print(f"{channel}:{account_id} credentials saved")


def _dispatch_bindings_verb(args, parser) -> None:
    from openprogram.channels import bindings as _b
    verb = getattr(args, "bindings_verb", None)
    if verb == "list":
        rows = _b.list_all()
        if not rows:
            print("No bindings. Inbound messages route to the default "
                  "agent until you add one with `openprogram channels "
                  "bindings add <agent_id> --channel <channel>`.")
            return
        print(f"{'id':18} {'agent':14} {'channel':10} {'account':12} "
              f"peer")
        for r in rows:
            m = r["match"]
            peer = m.get("peer") or {}
            peer_str = (f"{peer.get('kind','?')}:{peer.get('id','?')}"
                        if peer else "-")
            print(f"{r['id']:18} {r['agent_id']:14} "
                  f"{m.get('channel','*'):10} "
                  f"{m.get('account_id','*'):12} {peer_str}")
        return
    if verb == "add":
        match: dict = {"channel": args.channel}
        if args.account:
            match["account_id"] = args.account
        if args.peer:
            match["peer"] = {"kind": args.peer_kind, "id": args.peer}
        entry = _b.add(args.agent_id, match)
        print(f"Binding {entry['id']}: {match} → {args.agent_id}")
        return
    if verb == "rm":
        removed = _b.remove(args.binding_id)
        if removed:
            print(f"Removed binding {args.binding_id}")
        else:
            print(f"No binding {args.binding_id!r}")
        return
    parser.print_help()
