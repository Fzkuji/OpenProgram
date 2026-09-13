"""Credential CLI accounts responsibilities."""
from __future__ import annotations
import json
import sys
from ..account.aliases import known_aliases
from ..context import auth_scope
from ..credential_provider import get_credential_provider
from ..account.accounts import get_account_manager
from ..store import get_store
from ..types import AuthConfigError, AuthError, CredentialPool, RemovalStep


def _cmd_logout(provider: str, account: str, *, skip_confirm: bool) -> int:
    from .formatting import _payload_summary
    store = get_store()
    pool = store.find_pool(provider, account)
    if pool is None or not pool.credentials:
        print(f"No credentials for {provider}/{account} — nothing to remove.")
        return 0

    if not skip_confirm:
        print(f"About to remove {len(pool.credentials)} credential(s) for "
              f"{provider}/{account}:")
        for c in pool.credentials:
            print(f"  - {c.credential_id} ({_payload_summary(c)})")
        confirm = input("Proceed? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            return 0

    removed_steps = _collect_removal_steps(pool)
    store.delete_pool(provider, account)
    print(f"✓ Removed {len(pool.credentials)} credential(s) from "
          f"{store.root}/{provider}/{account}.json")

    non_exec = [s for s in removed_steps if not s.executable]
    if non_exec:
        print("\nManual cleanup still required:")
        for step in non_exec:
            print(f"  - {step.description}")
    return 0



def _collect_removal_steps(pool: CredentialPool) -> list[RemovalStep]:
    """Gather :class:`RemovalStep` entries from whichever source produced
    each credential. Returns a flat list; duplicates are OK because
    the user reads through them regardless."""
    from .catalog import _source_by_id
    steps: list[RemovalStep] = []
    for cred in pool.credentials:
        src = _source_by_id(cred.source, cred.account_id) if cred.source else None
        if src is not None and hasattr(src, "removal_steps"):
            try:
                steps.extend(src.removal_steps(cred))
            except Exception:
                continue
    return steps



def _cmd_status(provider: str, account: str) -> int:
    from .formatting import _payload_summary
    manager = get_credential_provider()
    with auth_scope(account_id=account):
        try:
            cred = manager.acquire_sync(provider, account)
        except AuthConfigError as e:
            print(f"No credential configured for {provider}/{account}.")
            print(f"  → {e}")
            print(f"Try: openprogram providers login {provider} --account {account}")
            return 1
        except AuthError as e:
            print(f"Credential exists but is not usable: {e}")
            return 1

    print(f"Provider: {provider}")
    print(f"Account:  {account}")
    print(f"Kind:     {cred.kind}")
    print(f"Status:   {cred.status}")
    print(f"Preview:  {_payload_summary(cred)}")
    if cred.metadata:
        print("Metadata:")
        for k, v in cred.metadata.items():
            print(f"  {k}: {v}")
    return 0



def _cmd_account_list() -> int:
    pm = get_account_manager()
    accounts = pm.list_accounts()
    print(f"{'name':16s}  {'display name':24s}  root")
    for p in accounts:
        print(f"{p.name:16s}  {p.display_name or '-':24s}  {p.root}")
    return 0



def _cmd_account_create(name: str, display_name: str, description: str) -> int:
    pm = get_account_manager()
    try:
        account = pm.create_account(
            name, display_name=display_name, description=description,
        )
    except AuthConfigError as e:
        print(f"Failed to create account: {e}", file=sys.stderr)
        return 1
    print(f"✓ Created account {account.name} at {account.root}")
    return 0



def _cmd_account_delete(name: str, skip_confirm: bool) -> int:
    pm = get_account_manager()
    if not skip_confirm:
        confirm = input(f"Delete account {name!r} and all its credentials? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            return 0
    try:
        pm.delete_account(name)
    except AuthConfigError as e:
        print(f"Failed to delete account: {e}", file=sys.stderr)
        return 1
    print(f"✓ Deleted account {name}")
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



def _cmd_migrate() -> int:
    from .._migrate_payload import migrate_store
    n = migrate_store()
    print(f"Migrated {n} credential file(s).")
    return 0

