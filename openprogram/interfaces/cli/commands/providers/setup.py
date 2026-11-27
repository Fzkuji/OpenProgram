"""``openprogram providers setup`` — the interactive first-time credential wizard."""
from __future__ import annotations
import sys
from typing import Any
from openprogram.models.auth.store import get_store
from openprogram.models.auth.types import Credential


def _already_held(store, cred: Credential) -> bool:
    """The provider's list already holds this sign-in's secret."""
    held = {c.payload.auth_value for c in store.stored(cred.provider_id).credentials}
    return bool(cred.payload.auth_value) and cred.payload.auth_value in held


def _cmd_setup() -> int:
    """Interactive wizard — arrow-key menus when questionary is
    installed, plain input() fallback otherwise.

    The questionary path is the primary UX (modelled after OpenClaw's
    clack-based setup). The fallback exists so environments without the
    dep — minimal installs, some CI sandboxes — still get a usable
    wizard, just without the navigation ergonomics.
    """
    try:
        from .interactive import run_interactive_setup
        return run_interactive_setup()
    except (KeyboardInterrupt, EOFError):
        print("\n\nSetup interrupted. Run `openprogram providers setup` again "
              "when you're ready. Progress so far is already saved.",
              file=sys.stderr)
        return 130



def _run_setup() -> int:
    from .formatting import _confirm, _payload_summary
    from .keys import _cmd_add
    from openprogram.models.auth.adoption import _sources

    store = get_store()

    print("═" * 60)
    print("  OpenProgram — provider setup")
    print("═" * 60)
    print()
    print("This wizard walks you through connecting AI providers.")
    print("We'll scan for sign-ins other tools hold (Codex, Qwen, GitHub")
    print("CLIs) and offer to import them. You can skip any")
    print("step. Nothing is sent anywhere; everything stays on this box.")
    print()
    if _confirm("Start?", default=True) is False:
        print("Cancelled.")
        return 0

    # --- step 1: scan -----------------------------------------------------
    print("\n[1/3] Scanning for existing credentials...\n")
    findings: list[tuple[Any, list[Credential]]] = []
    for src in _sources():
        try:
            creds = src.try_import(store.root)
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
                if _already_held(store, cred):
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
        ("anthropic",    "Anthropic (Claude)"),
        ("google",       "Google Gemini (API key)"),
        ("openai",       "OpenAI (raw API key)"),
    ]
    for prov_id, label in popular:
        if store.get(prov_id).credentials:
            continue
        pick = _confirm(f"  Log into {label} now?", default=False)
        if not pick:
            continue
        try:
            rc = _cmd_add(prov_id)
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
    print("    openprogram providers keys <provider> list")
    print()
    return 0

