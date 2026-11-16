"""``openprogram providers logout|status|aliases`` — removing a provider's
credentials, showing them, and the provider short names."""
from __future__ import annotations

import json
import sys

from openprogram.models.auth.account.aliases import known_aliases
from openprogram.models.auth.store import get_store
from openprogram.models.auth.types import Credential, RemovalStep


def _cmd_logout(provider: str, *, skip_confirm: bool) -> int:
    """Remove every stored key and sign-in of ``provider``. Environment
    variables' keys stay: they are removed by unsetting the variable."""
    store = get_store()
    stored = store.stored(provider).credentials
    if not stored:
        print(f"{provider} has no stored keys or sign-ins; nothing to remove.")
        return 0
    if not skip_confirm:
        if not sys.stdin.isatty():
            print("Pass --yes to remove without a terminal.", file=sys.stderr)
            return 1
        print(f"About to remove {len(stored)} key(s) and sign-in(s) of {provider}:")
        for c in stored:
            print(f"  - {c.display_label()}")
        if input("Proceed? [y/N]: ").strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return 0

    steps = _removal_steps(stored)
    store.delete_provider(provider)
    from openprogram.models.usage.quota import forget_accounts
    forget_accounts()
    print(f"Removed {len(stored)} key(s) and sign-in(s) of {provider}.")
    left = [c for c in store.get(provider).credentials if c.source == "env"]
    for c in left:
        name = c.metadata.get("env_var") or ""
        print(f"  {name} is still set; unset it to stop using that key.")
    manual = [s for s in steps if not s.executable]
    if manual:
        print("\nStill to do by hand:")
        for step in manual:
            print(f"  - {step.description}")
    return 0


def _removal_steps(creds: list[Credential]) -> list[RemovalStep]:
    """What the tool an imported login came from still holds."""
    from openprogram.models.auth.adoption import _sources, source_by_id
    steps: list[RemovalStep] = []
    for cred in creds:
        src = source_by_id(str(cred.metadata.get("imported_from") or "")) or next(
            (s for s in _sources() if cred.source == "import"
             and getattr(s, "provider_id", "") == cred.provider_id), None)
        if src is None:
            continue
        try:
            steps.extend(src.removal_steps(cred))
        except Exception:
            continue
    return steps


def _cmd_status(provider: str) -> int:
    """One provider's keys and sign-ins, in order, with their state."""
    from .keys import _print_list
    _print_list(provider)
    return 0


def _cmd_aliases(as_json: bool) -> int:
    table = known_aliases()
    if as_json:
        print(json.dumps(table, indent=2))
        return 0
    print(f"{'alias':24s}  canonical")
    for alias in sorted(table):
        print(f"{alias:24s}  {table[alias]}")
    print("\nUse either form: `openprogram providers login codex` and "
          "`openprogram providers login openai-codex` do the same thing.")
    return 0
