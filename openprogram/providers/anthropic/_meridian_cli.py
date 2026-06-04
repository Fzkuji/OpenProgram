"""CLI for managing the Claude accounts OpenProgram's ``claude-code``
provider can use, and which one is active.

Registered as ``openprogram providers claude-code accounts <verb>``:

  * ``add <name>``    — add a Claude account (opens a browser login)
  * ``remove <name>`` — remove a saved account
  * ``list``          — list accounts and which one is active
  * ``use <name>``    — activate an account (claude-code runs on it)
  * ``status``        — backend readiness + active account + accounts

Implementation note: the accounts live in a local Claude proxy (Meridian)
that holds each subscription as a named profile; ``claude-code`` requests
carry an ``x-meridian-profile`` header naming the active one. That proxy
is an INTERNAL detail — nothing user-facing here says "meridian"; users
only ever see "Claude account". The one exception is the one-time backend
install hint in ``status`` (the proxy ships as an npm package, so its name
is unavoidable there). See docs/design/claude-code-meridian-profile.md.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys


_DEFAULT_PROXY_URL = "http://localhost:3456"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def build_parser(accounts_sub: "argparse._SubParsersAction") -> None:
    """Register account verbs on ``providers claude-code accounts``."""
    p_add = accounts_sub.add_parser(
        "add",
        help="Add a Claude account (opens a browser login — sign in with the "
             "account you want to add).",
        description=(
            "Opens a browser login and saves the account under <name>. Sign "
            "in with whichever Claude account you want to add (switch accounts "
            "in the browser first if a different one is signed in). Your "
            "terminal chat account is not affected — you drive everything "
            "through OpenProgram, there's no other tool to run."
        ),
    )
    p_add.add_argument("name", help="A label for the account, e.g. experiment.")

    p_remove = accounts_sub.add_parser(
        "remove", aliases=["rm"], help="Remove a saved Claude account.",
    )
    p_remove.add_argument("name", help="Account label to remove.")

    accounts_sub.add_parser(
        "list", help="List saved Claude accounts and which one is active.",
    )

    p_use = accounts_sub.add_parser(
        "use", aliases=["activate"],
        help="Activate an account — claude-code runs OpenProgram on it.",
    )
    p_use.add_argument("name", help="Account label to activate.")

    accounts_sub.add_parser(
        "status",
        help="Show backend readiness, the active account, and all accounts.",
    )


def dispatch(args: argparse.Namespace) -> int:
    verb = getattr(args, "accounts_cmd", None)
    if verb == "add":
        return _cmd_add(args.name)
    if verb in ("remove", "rm"):
        return _cmd_remove(args.name)
    if verb == "list":
        return _cmd_list()
    if verb in ("use", "activate"):
        return _cmd_use(args.name)
    if verb == "status":
        return _cmd_status()
    print(
        "Usage: openprogram providers claude-code accounts <verb>\n"
        "Verbs: add <name>, remove <name>, list, use <name>, status",
        file=sys.stderr,
    )
    return 2


# ---------------------------------------------------------------------------
# backend helpers (the local proxy = Meridian; kept internal)
# ---------------------------------------------------------------------------

def _proxy_bin() -> str | None:
    return shutil.which("meridian")


def _run_proxy(args: list[str]) -> tuple[int, str]:
    binp = _proxy_bin()
    if not binp:
        return 127, "backend not installed"
    try:
        p = subprocess.run([binp, *args], capture_output=True, text=True, timeout=30)
    except Exception as e:  # noqa: BLE001
        return 1, f"backend error: {e}"
    return p.returncode, (p.stdout + p.stderr)


def _active_account() -> str | None:
    """The account name claude-code is currently pinned to (or None)."""
    from ._claude_max_proxy_registry import meridian_profile
    return meridian_profile()


def _backend_ready() -> tuple[bool, str]:
    url = os.environ.get("CLAUDE_MAX_PROXY_URL") or _DEFAULT_PROXY_URL
    try:
        import urllib.request

        urllib.request.urlopen(url.rstrip("/") + "/v1/models", timeout=3)
        return True, url
    except Exception:
        return False, url


def _parse_accounts() -> list[dict]:
    """Parse the proxy's profile listing into ``[{name, email}]``.

    We don't pass the raw backend output through — it mentions the
    underlying tool. Instead we pull out the account label + email and
    re-render them under Claude-account wording.
    """
    rc, out = _run_proxy(["profile", "list"])
    accounts: list[dict] = []
    for line in out.splitlines():
        s = _ANSI.sub("", line).strip()
        if not s or s.endswith(":") or s.startswith("Config") or "no restart" in s:
            continue
        if "No profiles" in s or "Add one" in s:
            continue
        parts = s.split()
        if not parts:
            continue
        name = parts[0]
        email = next((p for p in parts if "@" in p), "")
        accounts.append({"name": name, "email": email})
    return accounts


def accounts_summary() -> dict:
    """Structured Claude-account state for the REST / UI layer.

    CLI verbs and the web/TUI all read through this one shape. Never
    includes raw backend text, so no proxy/tool name leaks to the UI.
    """
    ready, url = _backend_ready()
    binp = _proxy_bin()
    return {
        "installed": bool(binp),
        "ready": ready,
        "backend_url": url,
        "active": _active_account(),
        "accounts": _parse_accounts() if binp else [],
    }


def add_account_async(name: str) -> dict:
    """Kick off a browser login for ``name`` without blocking (for the web
    button). Returns immediately; the UI polls :func:`accounts_summary`
    until the account appears. CLI ``add`` uses the inherited-stdio path
    instead so the terminal shows the prompts."""
    name = (name or "").strip()
    if not name:
        return {"started": False, "error": "an account name is required"}
    binp = _proxy_bin()
    if not binp:
        return {"started": False, "error": "backend not installed"}
    subprocess.Popen(
        [binp, "profile", "add", name],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return {"started": True, "name": name}


def remove_account(name: str) -> dict:
    """Remove an account; clear the active pin if it pointed there."""
    name = (name or "").strip()
    if not name:
        return {"removed": False, "error": "an account name is required"}
    if not _proxy_bin():
        return {"removed": False, "error": "backend not installed"}
    rc, _out = _run_proxy(["profile", "remove", name])
    cleared = False
    if rc == 0 and _active_account() == name:
        from openprogram.webui._model_catalog.storage import set_provider_config
        set_provider_config("claude-code", {"meridian_profile": ""})
        cleared = True
    return {"removed": rc == 0, "name": name, "cleared_active": cleared}


def activate_account(name: str) -> dict:
    """Set ``name`` as the account claude-code runs on (empty = unset)."""
    from openprogram.webui._model_catalog.storage import set_provider_config
    name = (name or "").strip()
    set_provider_config("claude-code", {"meridian_profile": name})
    return {"active": name}


def _print_install_hint() -> None:
    binp = _proxy_bin()
    print("\n  Backend not ready. One-time setup of the local Claude proxy")
    print("  that serves your accounts:")
    if not binp:
        print("    npm install -g @rynfar/meridian")
    print("    meridian        # keep this running while you use Claude here")
    print("  (Or set it up from the app: Settings → LLM Providers → Claude Code.)")


# ---------------------------------------------------------------------------
# verbs
# ---------------------------------------------------------------------------

def _cmd_add(name: str) -> int:
    name = (name or "").strip()
    if not name:
        print("An account label is required: openprogram providers claude-code "
              "accounts add <name>", file=sys.stderr)
        return 2
    if not _proxy_bin():
        _print_install_hint()
        return 1
    print(f"Adding Claude account {name!r} — a browser window will open.")
    print("Sign in with the Claude account you want to add; if a different one "
          "is\nalready signed in, switch it in the browser first (claude.ai → "
          "sign out →\nsign in). Your terminal chat account is not affected.\n")
    # Inherit stdio so the login prompts + browser open work. Internally this
    # is the proxy's profile-add; the user only ever sees Claude-account wording.
    try:
        rc = subprocess.run([_proxy_bin(), "profile", "add", name]).returncode
    except Exception as e:  # noqa: BLE001
        print(f"Adding the account failed: {e}", file=sys.stderr)
        return 1
    if rc == 0:
        print(f"\n✓ Added Claude account {name!r}. Activate it for OpenProgram:")
        print(f"  openprogram providers claude-code accounts use {name}")
    return rc


def _cmd_remove(name: str) -> int:
    name = (name or "").strip()
    if not name:
        print("An account label is required: openprogram providers claude-code "
              "accounts remove <name>", file=sys.stderr)
        return 2
    if not _proxy_bin():
        _print_install_hint()
        return 1
    rc, out = _run_proxy(["profile", "remove", name])
    # Surface a cleaned line, not the raw backend output.
    msg = _ANSI.sub("", out).strip().splitlines()
    print(f"✓ Removed Claude account {name!r}." if rc == 0
          else (msg[0] if msg else f"Could not remove {name!r}."))
    if rc == 0 and _active_account() == name:
        # It was the active one — clear the pin so we don't point at a gone account.
        from openprogram.webui._model_catalog.storage import set_provider_config
        set_provider_config("claude-code", {"meridian_profile": ""})
        print("  (It was active — OpenProgram is now unset; activate another "
              "with `accounts use <name>`.)")
    return rc


def _cmd_list() -> int:
    if not _proxy_bin():
        _print_install_hint()
        return 1
    accounts = _parse_accounts()
    active = _active_account()
    if not accounts:
        print("No Claude accounts yet. Add one:")
        print("  openprogram providers claude-code accounts add <name>")
        return 0
    print("Claude accounts (→ = active for OpenProgram):")
    for a in accounts:
        mark = "→" if a["name"] == active else " "
        print(f"  {mark} {a['name']:18s} {a.get('email', '')}")
    if active and active not in {a["name"] for a in accounts}:
        print(f"\n  Active account {active!r} isn't in the list — add it with "
              f"`accounts add {active}`.")
    return 0


def _cmd_use(name: str) -> int:
    name = (name or "").strip()
    if not name:
        print("An account label is required: openprogram providers claude-code "
              "accounts use <name>", file=sys.stderr)
        return 2
    from openprogram.webui._model_catalog.storage import set_provider_config

    set_provider_config("claude-code", {"meridian_profile": name})
    print(f"✓ OpenProgram now runs claude-code on the Claude account {name!r}.")
    print("  Takes effect on the next request — no restart. Your terminal chat "
          "account is unaffected.")
    accounts = {a["name"] for a in _parse_accounts()} if _proxy_bin() else set()
    if accounts and name not in accounts:
        print(f"  Note: {name!r} isn't added yet — add it with "
              f"`openprogram providers claude-code accounts add {name}`.")
    return 0


def _cmd_status() -> int:
    binp = _proxy_bin()
    ready, url = _backend_ready()
    active = _active_account()

    print("claude-code — Claude accounts")
    print(f"  backend   : {'ready (' + url + ')' if ready else 'not ready'}")
    print(f"  active    : {active!r}" if active
          else "  active    : (none — claude-code follows your terminal login)")

    if binp:
        accounts = _parse_accounts()
        print("\n  accounts:")
        if accounts:
            for a in accounts:
                mark = "→" if a["name"] == active else " "
                print(f"    {mark} {a['name']:18s} {a.get('email', '')}")
        else:
            print("    (none yet)")
    if not binp or not ready:
        _print_install_hint()
    else:
        print("\n  Add an account:     openprogram providers claude-code accounts add <name>")
        print("  Activate one:       openprogram providers claude-code accounts use <name>")
    return 0


__all__ = [
    "build_parser", "dispatch",
    "accounts_summary", "add_account_async", "remove_account", "activate_account",
]
