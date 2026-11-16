"""``openprogram providers keys <provider> ...`` — a provider's keys and sign-ins.

The same list the Providers page shows: an ordered list per provider, used
in order, in turn, or least used first. Every verb changes it through
:mod:`openprogram.models.auth.manage`, which the web routes use too, and
names a credential by its id, its name, or its 1-based place in ``list``.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import sys
from typing import Optional

from openprogram.models.auth import manage
from openprogram.models.auth.account.aliases import resolve as _canonical
from openprogram.models.auth.store import get_store
from openprogram.models.auth.types import AuthError, Credential

_log = logging.getLogger(__name__)

_STATUS = {
    "ready": "ready",
    "cooling": "cooling down",
    "quota": "out of quota",
    "invalid_key": "key refused",
    "needs_login": "sign in again",
    "not_set_up": "provider not set up",
    "no_file": "sign-in file not found",
}


def _err(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def _credentials(provider: str) -> list[Credential]:
    return list(get_store().get(provider).credentials)


def resolve_name(provider: str, name: str) -> Optional[Credential]:
    """The credential ``name`` refers to: its id, its name (case-insensitive),
    or its 1-based place in the list."""
    creds = _credentials(provider)
    for cred in creds:
        if cred.id == name:
            return cred
    folded = name.strip().casefold()
    for cred in creds:
        if folded and folded in (cred.label.casefold(), cred.display_label().casefold()):
            return cred
    if name.strip().isdigit() and 1 <= int(name) <= len(creds):
        return creds[int(name) - 1]
    return None


def _one(provider: str, name: str) -> Optional[Credential]:
    cred = resolve_name(provider, name)
    if cred is None:
        print(f"{provider} has no key or sign-in named {name!r}. "
              f"See: openprogram providers keys {provider} list", file=sys.stderr)
    return cred


def _ago(seconds: Optional[float]) -> str:
    if not seconds:
        return "never"
    import time
    from openprogram.models.auth.summary import format_duration
    return f"{format_duration(int(max(0.0, time.time() - seconds) * 1000))} ago"


def _print_list(provider: str) -> None:
    listing = manage.provider_credentials(provider)
    rows = listing["credentials"]
    print(f"{provider} — {listing['strategy'].replace('_', '-')}")
    if not rows:
        print("  No keys or sign-ins yet. Add one with:")
        print(f"  openprogram providers keys {provider} add")
        return
    names = [_name(r) for r in rows]
    width = max(len(n) for n in names)
    for i, (row, name) in enumerate(zip(rows, names), 1):
        mark = "*" if row["id"] == listing["active"] else " "
        state = _STATUS.get(row["status"], row["status"])
        if not row["enabled"]:
            state = "off"
        # Where it came from, unless the kind already says it ("import").
        origin = f"env {row['env_var']}" if row["env_var"] else \
            "" if row["source"] == row["kind"] else row["source"]
        print(f" {mark}{i:>2}. {name:<{width}}  {row['kind']:<18}  "
              f"{origin:<10}  {state:<19}  used {_ago(row['last_used_at'])}")
        if row["last_error"] and row["status"] != "ready":
            print(f"       {row['last_error'].get('message', '')}")
    print("\n * is the one a new conversation uses now.")


def _name(row: dict) -> str:
    """The row's name with its plan after it ("a@example.com (Plus)")."""
    plan = (row.get("identity") or {}).get("plan")
    return f"{row['display_label']} ({_plan(plan)})" if plan else row["display_label"]


_PLAN_NAMES = {"prolite": "Pro Lite"}


def _plan(plan: str) -> str:
    """A plan as its provider writes it, as a name: "pro_lite" / "prolite" → "Pro Lite"."""
    key = str(plan).strip().lower()
    return _PLAN_NAMES.get(key) or " ".join(w.capitalize() for w in key.replace("-", "_").split("_") if w)


def _cmd_list(provider: str, as_json: bool) -> int:
    if as_json:
        print(json.dumps(manage.provider_credentials(provider), indent=2, ensure_ascii=False))
        return 0
    _print_list(provider)
    return 0


def _read_key(api_key: Optional[str], from_stdin: bool, prompt: str) -> Optional[str]:
    if from_stdin:
        return sys.stdin.read().strip()
    if api_key is not None:
        return api_key.strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return getpass.getpass(f"{prompt} (hidden): ").strip()


def _pick_method(provider: str, method: Optional[str], key_given: bool) -> Optional[str]:
    """The way to connect: ``api_key`` when a key is given, else ``method``,
    else the only or the picked one; without a terminal, the API key read from stdin."""
    from openprogram.models.auth.login.login_method_registry import login_methods
    choices = login_methods(provider)
    ids = [m for m, _ in choices]
    if not choices:
        print(f"{provider} cannot be added here.", file=sys.stderr)
        return None
    wanted = "api_key" if key_given else method
    if wanted is not None:
        if wanted not in ids:
            print(f"{provider} has no way to connect named {wanted!r}. Try: {', '.join(ids)}",
                  file=sys.stderr)
            return None
        return wanted
    if not sys.stdin.isatty():
        if "api_key" in ids:
            return "api_key"
        print(f"{provider}: signing in needs a terminal (ways to connect: {', '.join(ids)}).",
              file=sys.stderr)
        return None
    if len(choices) == 1:
        return ids[0]
    from .interactive import pick_login_method_interactive
    picked = pick_login_method_interactive(provider, choices)
    if picked is not None:
        return picked
    print(f"Ways to connect {provider}:")
    for i, (_mid, label) in enumerate(choices, 1):
        print(f"  {i}. {label}")
    try:
        pick = input(f"Pick one [1-{len(choices)}] (default 1): ").strip() or "1"
        return choices[int(pick) - 1][0]
    except (EOFError, ValueError, IndexError):
        print("Cancelled.", file=sys.stderr)
        return None


_CHECK = {
    "works": "works",
    "rejected": "the provider refused this key",
    "no_credit": "no balance left",
    "not_available": "this key cannot use the provider's models",
    "unreachable": "the provider did not answer; try again later",
    "unverified": "not confirmed",
    "not_configured": "the provider is not set up here, so the key was not checked",
}
# Results whose line says why: what the provider or the check said.
_SAY_WHY = ("unverified", "unreachable", "not_configured")


def _verdict(check: Optional[dict]) -> str:
    check = check or {}
    result = check.get("result") or ""
    said = _CHECK.get(result, result)
    detail = (check.get("detail") or "").strip()
    if detail and (result in _SAY_WHY or (result == "works" and detail.startswith("Key accepted;"))):
        return f"{said} ({detail})"
    return said


def _after_sign_in(provider: str, saved: str) -> None:
    """A new sign-in turns on the provider's default models and reads its model list."""
    try:
        from openprogram.models.auth.login.login_seed_models import enable_default_models_on_login
        written = enable_default_models_on_login(provider)
        if written:
            print(f"  turned on models: {', '.join(written)}")
    except Exception:
        _log.debug("default models not turned on for %s", provider, exc_info=True)
    try:
        from openprogram.models.listing import fetch_models_remote
        res = fetch_models_remote(saved)
        if isinstance(res, dict) and res.get("fetched"):
            print(f"  read {res['fetched']} models from {saved}")
    except Exception:
        _log.debug("model list not read for %s", saved, exc_info=True)


def _cmd_add(provider: str, *, name: str = "", method: Optional[str] = None,
             api_key: Optional[str] = None, api_key_stdin: bool = False, check: bool = True) -> int:
    key_given = api_key is not None or api_key_stdin
    chosen = _pick_method(provider, method, key_given)
    if chosen is None:
        return 1
    if chosen == "api_key":
        key = _read_key(api_key, api_key_stdin, f"Paste the API key for {provider}")
        if not key:
            return _err("No key given; nothing saved.")
        try:
            result = manage.add_api_key(provider, key, label=name, validate=check, source="cli")
        except manage.CredentialEditError as exc:
            return _err(f"Not saved: {exc}")
        row = result["credential"]
        print(f"Added {row['display_label']} to {provider}.")
        verdict = _verdict(result.get("check"))
        if verdict:
            print(f"  check: {verdict}")
        return 0
    try:
        cred = _sign_in(provider, chosen, api_key=_read_key(api_key, api_key_stdin, "")
                        if key_given else None)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except AuthError as exc:
        return _err(f"Sign-in failed: {exc}")
    except Exception as exc:
        return _err(f"Sign-in failed: {exc.__class__.__name__}: {exc}")
    from openprogram.models.auth.login.login_driver import persist
    kept = persist(cred)
    if name:
        manage.edit(kept.provider_id, kept.id, label=name)
    row = manage.credential_row(get_store().find(kept.provider_id, kept.id) or kept)
    print(f"Signed in: {row['display_label']} ({kept.provider_id}).")
    _after_sign_in(provider, kept.provider_id)
    return 0


def _sign_in(provider: str, method: str, *, api_key: Optional[str]) -> Credential:
    """Run a sign-in through the shared login driver, in this terminal."""
    if method == "pkce_oauth":
        title, body = _SIGN_IN_NOTES.get(provider, ("", []))
        if body:
            from .tui import print_note
            print_note(title, body)
    from openprogram.models.auth.login.login_driver import run_login
    return asyncio.run(run_login(provider, method, _TerminalLoginUi(), api_key=api_key, source="cli"))


class _TerminalLoginUi:
    """The login driver's prompts in this terminal and the system browser."""

    async def open_url(self, url: str) -> None:
        import webbrowser
        print(f"\nOpen this address to continue signing in:\n  {url}\n")
        try:
            webbrowser.open(url)
        except Exception:
            _log.debug("no browser opened for the sign-in", exc_info=True)

    async def prompt(self, message: str, *, secret: bool = False) -> str:
        loop = asyncio.get_running_loop()
        ask = getpass.getpass if secret else input
        return (await loop.run_in_executor(None, ask, f"{message}: ")).strip()

    async def show_progress(self, message: str) -> None:
        print(message, flush=True)

    async def show_code(self, user_code: str, verification_uri: str) -> None:
        print(f"\nGo to {verification_uri} and enter code: {user_code}\n", flush=True)


_SIGN_IN_NOTES: dict[str, tuple[str, list[str]]] = {
    "openai-codex": (
        "Sign in with ChatGPT",
        [
            "A browser window opens for OpenAI sign-in; the callback finishes it.",
            "Needs a ChatGPT Plus / Pro / Team / Enterprise account.",
            "The callback listens on localhost:1455; close other codex processes",
            "that hold that port.",
        ],
    ),
}


def _cmd_replace(provider: str, name: str, api_key: Optional[str], api_key_stdin: bool) -> int:
    cred = _one(provider, name)
    if cred is None:
        return 1
    key = _read_key(api_key, api_key_stdin, f"Paste the new key for {cred.display_label()}")
    if not key:
        return _err("No key given; nothing changed.")
    try:
        result = manage.replace(provider, cred.id, key)
    except manage.CredentialEditError as exc:
        return _err(f"Not replaced: {exc}")
    print(f"Replaced the key of {result['credential']['display_label']}.")
    return 0


def _cmd_rename(provider: str, name: str, new: str) -> int:
    cred = _one(provider, name)
    if cred is None:
        return 1
    try:
        row = manage.edit(provider, cred.id, label=new)
    except manage.CredentialEditError as exc:
        return _err(str(exc))
    print(f"Renamed to {row['display_label']}.")
    return 0


def _cmd_enable(provider: str, name: str, enabled: bool) -> int:
    cred = _one(provider, name)
    if cred is None:
        return 1
    row = manage.edit(provider, cred.id, enabled=enabled)
    print(f"{row['display_label']} is {'on' if enabled else 'off'}.")
    return 0


def _cmd_move(provider: str, name: str, *, up: bool, down: bool, to: Optional[int]) -> int:
    cred = _one(provider, name)
    if cred is None:
        return 1
    ids = [c.id for c in _credentials(provider)]
    at = ids.index(cred.id)
    target = (to - 1) if to is not None else at - 1 if up else at + 1 if down else at
    target = max(0, min(len(ids) - 1, target))
    ids.insert(target, ids.pop(at))
    manage.reorder(provider, ids)
    _print_list(provider)
    return 0


def _cmd_check(provider: str, name: Optional[str]) -> int:
    creds = [_one(provider, name)] if name else _credentials(provider)
    if not creds or creds[0] is None:
        return 1 if name else _err(f"{provider} has no keys or sign-ins to check.")
    worst = 0
    for cred in creds:
        result = manage.check(provider, cred.id)
        row = result["credential"]
        print(f"{row['display_label']}: {_verdict(result['check']) or _STATUS.get(row['status'], row['status'])}")
        if row["status"] in ("invalid_key", "needs_login"):
            worst = 1
    return worst


def _cmd_remove(provider: str, name: str, yes: bool) -> int:
    cred = _one(provider, name)
    if cred is None:
        return 1
    if not yes:
        if not sys.stdin.isatty():
            return _err("Pass --yes to remove without a terminal.")
        if input(f"Remove {cred.display_label()} from {provider}? [y/N]: ").strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return 0
    try:
        manage.remove(provider, cred.id)
    except manage.CredentialEditError as exc:
        return _err(str(exc))
    print(f"Removed {cred.display_label()}.")
    return 0


def _cmd_strategy(provider: str, strategy: str) -> int:
    try:
        listing = manage.set_strategy(provider, strategy.replace("-", "_"))
    except manage.CredentialEditError as exc:
        return _err(str(exc))
    print(f"{provider} now uses its keys {listing['strategy'].replace('_', '-')}.")
    return 0


def dispatch_keys(args: argparse.Namespace) -> int:
    provider = _canonical(args.provider)
    cmd = getattr(args, "keys_cmd", None) or "list"
    if cmd == "list":
        return _cmd_list(provider, getattr(args, "json", False))
    if cmd == "add":
        return _cmd_add(provider, name=args.name or "", method=args.method, api_key=args.api_key,
                        api_key_stdin=args.api_key_stdin, check=not args.no_check)
    if cmd == "replace":
        return _cmd_replace(provider, args.name, args.api_key, args.api_key_stdin)
    if cmd == "rename":
        return _cmd_rename(provider, args.name, args.new_name)
    if cmd in ("enable", "disable"):
        return _cmd_enable(provider, args.name, cmd == "enable")
    if cmd == "move":
        return _cmd_move(provider, args.name, up=args.up, down=args.down, to=args.to)
    if cmd == "check":
        return _cmd_check(provider, args.name)
    if cmd == "remove":
        return _cmd_remove(provider, args.name, args.yes)
    if cmd == "strategy":
        return _cmd_strategy(provider, args.strategy)
    return _err("Usage: openprogram providers keys <provider> "
                "list|add|replace|rename|move|enable|disable|check|remove|strategy")


def add_key_arguments(p: argparse.ArgumentParser) -> None:
    """The options ``keys <p> add`` and ``login <p>`` share."""
    p.add_argument("--name", default=None, help="A name to tell this key or sign-in apart")
    p.add_argument("--method", default=None,
                   help="Way to connect (api_key, pkce_oauth, device_code, import_from_cli, "
                        "paste_token); omit it to use the recommended one")
    p.add_argument("--api-key", default=None, dest="api_key",
                   help="The API key, without a prompt. It stays in shell history; "
                        "prefer --api-key-stdin")
    p.add_argument("--api-key-stdin", action="store_true", dest="api_key_stdin",
                   help="Read the API key from stdin, e.g. "
                        "`printf %%s \"$KEY\" | openprogram providers keys deepseek add --api-key-stdin`")
    p.add_argument("--no-check", action="store_true", dest="no_check",
                   help="Save the key without checking it with the provider first")


def build_keys_parser(sub: argparse._SubParsersAction) -> None:
    p_keys = sub.add_parser(
        "keys", help="List and manage a provider's keys and sign-ins",
        description="A provider's keys and sign-ins form one ordered list. Name one by its "
                    "name, its id, or its number in `list`.")
    p_keys.add_argument("provider", help="Provider id (e.g. openai, anthropic, openai-codex)")
    ks = p_keys.add_subparsers(dest="keys_cmd", metavar="verb")

    p_list = ks.add_parser("list", help="Show the provider's keys and sign-ins in order")
    p_list.add_argument("--json", action="store_true", help="Output the same JSON as the web API")

    add_key_arguments(ks.add_parser("add", help="Add a key or sign in"))

    p_rep = ks.add_parser("replace", help="Put a new key in a stored key's place")
    p_rep.add_argument("name", help="The key's name, id or number")
    p_rep.add_argument("--api-key", default=None, dest="api_key",
                       help="The new key, without a prompt; prefer --api-key-stdin")
    p_rep.add_argument("--api-key-stdin", action="store_true", dest="api_key_stdin",
                       help="Read the new key from stdin")

    p_ren = ks.add_parser("rename", help="Give a key or sign-in another name")
    p_ren.add_argument("name", help="The key's name, id or number")
    p_ren.add_argument("new_name", help="The new name (empty to show the account or key ending)")

    p_mv = ks.add_parser("move", help="Change where a key sits in the order")
    p_mv.add_argument("name", help="The key's name, id or number")
    where = p_mv.add_mutually_exclusive_group(required=True)
    where.add_argument("--up", action="store_true", help="One place earlier")
    where.add_argument("--down", action="store_true", help="One place later")
    where.add_argument("--to", type=int, default=None, metavar="N", help="To place N (1 is first)")

    for verb, text in (("enable", "Use this key again"), ("disable", "Stop using this key, keeping it")):
        ks.add_parser(verb, help=text).add_argument("name", help="The key's name, id or number")

    p_chk = ks.add_parser("check", help="Check keys with the provider now")
    p_chk.add_argument("name", nargs="?", default=None, help="The key to check (omit for all)")

    p_rm = ks.add_parser("remove", help="Remove a key or sign-in")
    p_rm.add_argument("name", help="The key's name, id or number")
    p_rm.add_argument("--yes", action="store_true", help="Remove without asking")

    p_st = ks.add_parser("strategy", help="Set how the keys take turns")
    p_st.add_argument("strategy", choices=["in-order", "evenly", "least-used"],
                      help="in-order: the first usable one; evenly: each in turn; "
                           "least-used: the one used least")


__all__ = ["add_key_arguments", "build_keys_parser", "dispatch_keys", "resolve_name"]
