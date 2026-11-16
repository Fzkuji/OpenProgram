"""``openprogram providers`` (alias ``secrets``) — the credential command tree.

The reusable credential functions (:mod:`openprogram.models.auth.doctor`,
:mod:`openprogram.models.auth.adoption`, :mod:`openprogram.models.auth.summary`)
live in the core; these modules parse arguments, prompt and print.
"""
from __future__ import annotations
import argparse
import sys
from openprogram.models.auth.account.aliases import resolve as _resolve_alias


def dispatch(args: argparse.Namespace) -> int:
    """Run the selected credential-management verb.

    Reads :attr:`providers_cmd` from the argparse namespace (the parent
    subparser dest). Returns a shell-style exit code so the outer
    ``main()`` can propagate to ``sys.exit``.
    """
    from .logout import _cmd_aliases, _cmd_logout, _cmd_status
    from .catalog import _cmd_adopt, _cmd_adopt_all, _cmd_available, _cmd_discover, _cmd_list
    from .doctor import _cmd_doctor
    from .keys import _cmd_add, dispatch_keys
    from .setup import _cmd_setup
    cmd = args.providers_cmd
    if cmd == "keys":
        return dispatch_keys(args)
    if cmd == "login":
        return _cmd_add(_resolve_alias(args.provider), name=args.name or "", method=args.method,
                        api_key=args.api_key, api_key_stdin=args.api_key_stdin,
                        check=not args.no_check)
    if cmd == "list":
        return _cmd_list(args.json)
    if cmd in ("available", "search", "catalog"):
        return _cmd_available(
            args.query, args.json, getattr(args, "configured", False),
        )
    if cmd == "discover":
        return _cmd_discover(args.json)
    if cmd == "adopt":
        if getattr(args, "adopt_all", False):
            return _cmd_adopt_all()
        if args.source_id is None:
            print("Usage: openprogram providers adopt <source_id> | --all",
                  file=sys.stderr)
            return 2
        return _cmd_adopt(args.source_id)
    if cmd == "logout":
        return _cmd_logout(_resolve_alias(args.provider), skip_confirm=args.yes)
    if cmd == "status":
        if not args.provider:
            from .quota_status import cmd_overview
            return cmd_overview(getattr(args, "refresh", False), getattr(args, "json", False))
        return _cmd_status(_resolve_alias(args.provider))
    if cmd == "doctor":
        return _cmd_doctor(args.json)
    if cmd == "setup":
        return _cmd_setup()
    if cmd == "aliases":
        return _cmd_aliases(args.json)
    # No subcommand — print the help hint.
    print("Usage: openprogram providers <verb>\n"
          "Verbs: keys, login, logout, list, available, status, discover, adopt, "
          "doctor, setup, aliases",
          file=sys.stderr)
    return 2


from .parser import build_parser

__all__ = ["build_parser", "dispatch"]
