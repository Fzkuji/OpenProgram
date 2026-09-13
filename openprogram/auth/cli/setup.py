"""Credential CLI setup responsibilities."""
from __future__ import annotations
import sys
from typing import Any
from ..account.accounts import DEFAULT_ACCOUNT_NAME
from ..account.accounts import get_account_manager
from ..store import get_store
from ..types import Credential


def _cmd_setup() -> int:
    """Interactive wizard — arrow-key menus when questionary is
    installed, plain input() fallback otherwise.

    The questionary path is the primary UX (modelled after OpenClaw's
    clack-based setup). The fallback exists so environments without the
    dep — minimal installs, some CI sandboxes — still get a usable
    wizard, just without the navigation ergonomics.
    """
    try:
        from ..login.interactive import run_interactive_setup
        return run_interactive_setup()
    except (KeyboardInterrupt, EOFError):
        print("\n\nSetup interrupted. Run `openprogram providers setup` again "
              "when you're ready. Progress so far is already saved.",
              file=sys.stderr)
        return 130



def _run_setup() -> int:
    from .formatting import _confirm, _payload_summary
    from .login import _cmd_login
    from openprogram.auth.sources import (
        CodexCliSource,
        EnvApiKeySource,
        GhCliSource,
        QwenCliSource,
    )

    store = get_store()
    pm = get_account_manager()
    default = pm.get_account(DEFAULT_ACCOUNT_NAME)

    print("═" * 60)
    print("  OpenProgram — provider setup")
    print("═" * 60)
    print()
    print("This wizard walks you through connecting AI providers.")
    print("We'll scan for existing logins (Codex CLI, Claude Code, env")
    print("variables, etc.) and offer to import them. You can skip any")
    print("step. Nothing is sent anywhere; everything stays on this box.")
    print()
    if _confirm("Start?", default=True) is False:
        print("Cancelled.")
        return 0

    # --- step 1: scan -----------------------------------------------------
    print("\n[1/3] Scanning for existing credentials...\n")
    sources: list[Any] = [
        CodexCliSource(),
        QwenCliSource(),
        GhCliSource(),
    ]
    from openprogram.providers.env_api_keys import PROVIDER_ENV_VARS
    for provider, env_var in PROVIDER_ENV_VARS.items():
        sources.append(EnvApiKeySource(provider_id=provider, env_var=env_var))

    findings: list[tuple[Any, list[Credential]]] = []
    for src in sources:
        try:
            creds = src.try_import(default.root)
        except Exception:
            continue
        if creds:
            findings.append((src, creds))

    if not findings:
        print("  (nothing detected)")
    else:
        print(f"  Found {sum(len(c) for _, c in findings)} adoptable credential(s):")
        for src, creds in findings:
            for c in creds:
                print(f"    · {src.source_id:24s} → {c.provider_id} "
                      f"({_payload_summary(c)})")

    # --- step 2: adopt ----------------------------------------------------
    adopted = 0
    if findings:
        print()
        adopt_all = _confirm("Import all of them?", default=True)
        for src, creds in findings:
            for cred in creds:
                if adopt_all is False:
                    pick = _confirm(
                        f"  Import {src.source_id} → {cred.provider_id}?",
                        default=True,
                    )
                    if not pick:
                        continue
                # Dedup by source label — credential_id is freshly
                # minted every try_import, so id-equality never matches
                # and we used to create duplicates on every wizard run.
                existing = store.find_pool(cred.provider_id, cred.account_id)
                if existing and any(
                    c.source == cred.source for c in existing.credentials
                ):
                    continue
                try:
                    store.add_credential(cred)
                    adopted += 1
                except Exception as e:
                    print(f"    (failed to add: {e})")
        print(f"\n  Imported {adopted} credential(s).")

    # --- step 3: manual login for missing popular providers ---------------
    print("\n[2/3] Manual login for providers not yet covered...\n")
    popular = [
        ("openai-codex", "OpenAI via Codex CLI (ChatGPT account)"),
        ("anthropic",    "Anthropic (Claude)"),
        ("gemini-subscription", "Google Gemini via CLI"),
        ("github-copilot", "GitHub Copilot"),
        ("openai",       "OpenAI (raw API key)"),
    ]
    for prov_id, label in popular:
        if store.find_pool(prov_id, DEFAULT_ACCOUNT_NAME) is not None:
            continue
        pick = _confirm(f"  Log into {label} now?", default=False)
        if not pick:
            continue
        try:
            rc = _cmd_login(prov_id, DEFAULT_ACCOUNT_NAME, method=None)
            if rc != 0:
                print(f"    (skipped — {prov_id} login exited {rc})")
        except (KeyboardInterrupt, EOFError):
            print("\n    (aborted)")
            continue

    # --- outro ------------------------------------------------------------
    print("\n[3/3] Done.\n")
    print("  Verify anytime:")
    print("    openprogram providers list")
    print("    openprogram providers doctor")
    print("    openprogram providers status <provider>")
    print()
    return 0

