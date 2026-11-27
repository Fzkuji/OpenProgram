"""Argument parser for the ``openprogram providers`` verbs."""
from __future__ import annotations

import argparse

from .keys import add_key_arguments, build_keys_parser


def build_parser(sub: argparse._SubParsersAction) -> None:
    """Register credential-management verbs directly on the parent.

    Per docs/design/cli/naming.md, commands have the shape
    ``<noun> [<noun> ...] <verb>``. This function is called with the
    ``providers`` subparser as its parent, so the verbs land as
    ``providers login``, ``providers list``, etc. ``keys`` is the only
    nested noun (``providers keys <provider> list`` / ``add`` / ...).

    Expected use from :func:`openprogram.interfaces.cli.application.main`::

        p_providers = sub.add_parser("providers", ...)
        providers_sub = p_providers.add_subparsers(dest="providers_cmd")
        from openprogram.interfaces.cli.commands.providers import build_parser
        build_parser(providers_sub)
    """
    auth_sub = sub

    # keys — a provider's ordered list of keys and sign-ins
    build_keys_parser(auth_sub)

    # login — the same as `keys <provider> add`
    p_login = auth_sub.add_parser(
        "login", help="Add a key or sign in to a provider (same as `keys PROVIDER add`)")
    p_login.add_argument("provider", help="Provider id (e.g. anthropic, deepseek)")
    add_key_arguments(p_login)

    # list
    p_list = auth_sub.add_parser("list", help="List every provider's keys and sign-ins")
    p_list.add_argument("--json", action="store_true", help="Output JSON")

    # available — browse/search the full provider catalogue
    p_avail = auth_sub.add_parser(
        "available",
        aliases=["search", "catalog"],
        help="List every LLM provider you can configure (the full "
             "catalogue, incl. community ones); optional QUERY filters it.",
        description=(
            "Show all providers OpenProgram knows about — the built-in "
            "ones plus the community catalogue (models.dev). This is the "
            "list you pick an id from for `openprogram providers login "
            "<id>`. Pass a QUERY to filter by id or label (case-"
            "insensitive substring), e.g. `providers available minimax`."
        ),
    )
    p_avail.add_argument(
        "query", nargs="?", default=None,
        help="Filter to providers whose id or label contains this text.",
    )
    p_avail.add_argument(
        "--json", action="store_true",
        help="Output JSON (for scripts / agents).")
    p_avail.add_argument(
        "--configured", action="store_true",
        help="Only show providers that already have a key / credential.")

    # discover
    p_disc = auth_sub.add_parser("discover", help="Scan external sources")
    p_disc.add_argument("--json", action="store_true", help="Output JSON")

    # adopt
    p_adopt = auth_sub.add_parser("adopt",
        help="Adopt a discovered credential into the store")
    p_adopt.add_argument("source_id", nargs="?", default=None,
        help="Source id from `discover` output (qwen_cli, gh_cli). "
             "Omit when using --all.")
    p_adopt.add_argument("--all", dest="adopt_all", action="store_true",
                         help="Adopt every sign-in discover finds; ones already added are skipped")

    # logout
    p_logout = auth_sub.add_parser("logout", help="Remove every stored key and sign-in of a provider")
    p_logout.add_argument("provider", help="Provider id to log out of")
    p_logout.add_argument("--yes", action="store_true", help="Skip confirmation")

    # status
    p_status = auth_sub.add_parser(
        "status",
        help="Every connected provider's health, quota and 30-day spend; "
             "with PROVIDER, that provider's keys and sign-ins",
    )
    p_status.add_argument("provider", nargs="?", default=None,
                          help="Provider id to show one provider (omit for every provider)")
    p_status.add_argument("--refresh", action="store_true",
                          help="Read each provider's quota endpoint now instead of using values "
                               "under ten minutes old")
    p_status.add_argument("--json", action="store_true",
                          help="Output the same JSON as GET /api/providers/status")

    # doctor — diagnostic report over every pool
    p_doctor = auth_sub.add_parser(
        "doctor", help="Diagnose credentials (expiry, refresh, cooldown, conflicts)",
    )
    p_doctor.add_argument("--json", action="store_true", help="Output JSON")

    # setup — interactive wizard that chains discover → login → status
    auth_sub.add_parser(
        "setup", help="Interactive first-time setup",
    )

    # aliases — show the short-name → canonical table
    p_aliases = auth_sub.add_parser(
        "aliases", help="List provider short-name aliases",
    )
    p_aliases.add_argument("--json", action="store_true", help="Output JSON")
