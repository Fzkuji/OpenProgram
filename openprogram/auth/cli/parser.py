"""Credential CLI parser responsibilities."""
from __future__ import annotations
from ..account.accounts import DEFAULT_ACCOUNT_NAME


def build_parser(sub: "argparse._SubParsersAction") -> None:
    """Register credential-management verbs directly on the parent.

    Per docs/design/cli/naming.md, commands have the shape
    ``<noun> [<noun> ...] <verb>``. This function is called with the
    ``providers`` subparser as its parent, so the verbs land as
    ``providers login``, ``providers list``, etc. ``accounts`` is the
    only nested noun (`providers accounts list` / `create` / `delete`).

    Expected use from :func:`openprogram.cli.main`::

        p_providers = sub.add_parser("providers", ...)
        providers_sub = p_providers.add_subparsers(dest="providers_cmd")
        from openprogram.auth.cli import build_parser
        build_parser(providers_sub)
    """
    auth_sub = sub

    # login
    p_login = auth_sub.add_parser("login", help="Log into a provider")
    p_login.add_argument("provider", help="Provider id (e.g. openai-codex, anthropic)")
    p_login.add_argument("--account", default=DEFAULT_ACCOUNT_NAME,
                         help=f"Account (default: {DEFAULT_ACCOUNT_NAME})")
    p_login.add_argument("--method", default=None,
                         help="Advanced: force a specific login method. Omit it "
                              "and the right one for the provider is picked "
                              "automatically.")
    p_login.add_argument("--api-key", default=None, dest="api_key",
                         help="Supply the API key non-interactively (scripts / "
                              "agents). Implies --method api_key; skips the "
                              "prompt. WARNING: visible in shell history and "
                              "`ps` — prefer --api-key-stdin or an env var.")
    p_login.add_argument("--api-key-stdin", action="store_true", dest="api_key_stdin",
                         help="Read the API key from stdin (until EOF), non-"
                              "interactively. Implies --method api_key. e.g. "
                              "`printf %%s \"$KEY\" | openprogram providers login "
                              "minimax-cn --api-key-stdin`.")

    # list
    p_list = auth_sub.add_parser("list", help="List pools per account")
    p_list.add_argument("--account", default=None,
                        help="Filter to one account (default: all)")
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
        help="Source id from `discover` output (e.g. codex_cli, "
             "env:OPENAI_API_KEY). Omit when using --all.")
    p_adopt.add_argument("--account", default=DEFAULT_ACCOUNT_NAME,
                         help=f"Target account (default: {DEFAULT_ACCOUNT_NAME})")
    p_adopt.add_argument("--all", dest="adopt_all", action="store_true",
                         help="Adopt every credential discover() finds. "
                              "Skips pools that already contain the same credential_id.")

    # logout
    p_logout = auth_sub.add_parser("logout", help="Remove credentials for a provider")
    p_logout.add_argument("provider", help="Provider id to log out of")
    p_logout.add_argument("--account", default=DEFAULT_ACCOUNT_NAME, help="Account to remove credentials from")
    p_logout.add_argument("--yes", action="store_true", help="Skip confirmation")

    # status
    p_status = auth_sub.add_parser("status", help="Check a provider's current credential")
    p_status.add_argument("provider", help="Provider id to check")
    p_status.add_argument("--account", default=DEFAULT_ACCOUNT_NAME, help="Account to check")

    # use — pick which account (account) a provider runs on
    p_use = auth_sub.add_parser("use", help="Set which account (account) a provider runs on")
    p_use.add_argument("provider", help="Provider id")
    p_use.add_argument("account", nargs="?", default="",
                       help="Account to activate; omit to clear back to the default")

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

    # migrate — one-shot rewrite of old-format credential JSON
    auth_sub.add_parser(
        "migrate", help="Migrate stored credentials to the current format",
    )

    # accounts (plural noun, per CLI naming convention — see
    # docs/design/cli/naming.md). Verbs follow: list/create/delete.
    p_accounts = auth_sub.add_parser("accounts", help="Account management")
    acct_sub = p_accounts.add_subparsers(dest="accounts_cmd", metavar="verb")
    acct_sub.add_parser("list", help="List accounts")
    pc = acct_sub.add_parser("create", help="Create an account")
    pc.add_argument("name", help="New account name")
    pc.add_argument("--display-name", default="", help="Human-readable label for the account")
    pc.add_argument("--description", default="", help="Optional description for the account")
    pd = acct_sub.add_parser("delete", help="Delete an account")
    pd.add_argument("name", help="Account name to delete")
    pd.add_argument("--yes", action="store_true", help="Skip confirmation")

