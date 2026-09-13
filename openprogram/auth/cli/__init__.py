"""Credential CLI dispatch responsibilities."""
from __future__ import annotations
import argparse
import sys
from ..account.aliases import resolve as _resolve_alias


def dispatch(args: argparse.Namespace) -> int:
    """Run the selected credential-management verb.

    Reads :attr:`providers_cmd` from the argparse namespace (the parent
    subparser dest) rather than any private auth-scoped dest. Returns a
    shell-style exit code so the outer ``main()`` can propagate to
    ``sys.exit``.
    """
    from .accounts import _cmd_aliases, _cmd_logout, _cmd_migrate, _cmd_status
    from .catalog import _cmd_adopt, _cmd_adopt_all, _cmd_available, _cmd_discover, _cmd_list, _cmd_use
    from .doctor import _cmd_doctor
    from .login import _cmd_login
    from .setup import _cmd_setup
    cmd = args.providers_cmd
    if cmd == "login":
        return _cmd_login(
            _resolve_alias(args.provider), args.account, args.method,
            api_key=getattr(args, "api_key", None),
            api_key_stdin=getattr(args, "api_key_stdin", False),
        )
    if cmd == "list":
        return _cmd_list(args.account, args.json)
    if cmd == "use":
        return _cmd_use(_resolve_alias(args.provider), args.account)
    if cmd in ("available", "search", "catalog"):
        return _cmd_available(
            args.query, args.json, getattr(args, "configured", False),
        )
    if cmd == "discover":
        return _cmd_discover(args.json)
    if cmd == "adopt":
        if getattr(args, "adopt_all", False):
            return _cmd_adopt_all(args.account)
        if args.source_id is None:
            print("Usage: openprogram providers adopt <source_id> | --all",
                  file=sys.stderr)
            return 2
        return _cmd_adopt(args.source_id, args.account)
    if cmd == "logout":
        return _cmd_logout(
            _resolve_alias(args.provider), args.account, skip_confirm=args.yes,
        )
    if cmd == "status":
        return _cmd_status(_resolve_alias(args.provider), args.account)
    if cmd == "doctor":
        return _cmd_doctor(args.json)
    if cmd == "setup":
        return _cmd_setup()
    if cmd == "aliases":
        return _cmd_aliases(args.json)
    if cmd == "migrate":
        return _cmd_migrate()
    if cmd == "accounts":
        return _dispatch_accounts(args)
    # No subcommand — print the help hint.
    print("Usage: openprogram providers <verb>\n"
          "Verbs: available (list/search the catalogue), login, logout, "
          "list (configured pools), status, discover, adopt, doctor, "
          "setup, aliases, migrate, accounts",
          file=sys.stderr)
    return 2



def _dispatch_accounts(args: argparse.Namespace) -> int:
    from .accounts import _cmd_account_create, _cmd_account_delete, _cmd_account_list
    pc = args.accounts_cmd
    if pc == "list":
        return _cmd_account_list()
    if pc == "create":
        return _cmd_account_create(args.name, args.display_name, args.description)
    if pc == "delete":
        return _cmd_account_delete(args.name, args.yes)
    print("Usage: openprogram providers accounts <verb>\n"
          "Verbs: list, create, delete", file=sys.stderr)
    return 2


from .parser import build_parser
from .doctor import run_doctor
from .catalog import run_adopt_all

__all__ = ["build_parser", "dispatch", "run_doctor", "run_adopt_all"]
