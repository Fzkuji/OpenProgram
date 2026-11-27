"""Interactive credential wizard for ``openprogram providers setup`` — arrow-key menus, multi-select, back-nav.

OpenClaw uses `@clack/prompts`. The closest Python equivalent is
`questionary` (prompt_toolkit-based); same three primitives:

  * ``select(choices)``       — one of N, arrow keys
  * ``checkbox(choices)``     — multi-select, space to toggle
  * ``confirm(message)``      — y/n

If questionary isn't installed (for example in a minimal development
environment), every primitive falls back to plain
``input()`` — same behaviour as before, just less ergonomic. No hard
dependency at import time so ``openprogram providers list`` keeps
working in environments that don't have questionary.

Ctrl-C in questionary returns ``None`` from the prompt; treat that as
"go back / cancel". The top-level :func:`run_interactive_setup` loops
until the user picks Quit or sends EOF.

All real work (scanning, adopting, signing in) is delegated to
the same helpers that the non-interactive CLI uses — this module only
handles presentation.
"""
from __future__ import annotations

import sys
from typing import Any, Optional

try:  # optional dependency — see module docstring
    import questionary
    from questionary import Choice
    _HAS_QUESTIONARY = True
except ImportError:  # pragma: no cover — graceful degradation
    questionary = None  # type: ignore[assignment]
    Choice = None        # type: ignore[assignment]
    _HAS_QUESTIONARY = False


def _questionary_usable() -> bool:
    """Combined "is questionary actually usable here?" check.

    Wraps both the import test and the prompt_toolkit terminal probe
    so call sites can replace ``_HAS_QUESTIONARY and sys.stdin.isatty()``
    with one call that also catches Git Bash / MinTTY / non-native
    Windows consoles where ``questionary.select(...).ask()`` would
    raise ``NoConsoleScreenBufferError`` on first prompt.
    """
    if not _HAS_QUESTIONARY:
        return False
    if not sys.stdin.isatty():
        return False
    from openprogram.host._platform.terminal import prompt_toolkit_usable
    return prompt_toolkit_usable()


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def run_interactive_setup() -> int:
    """Top-level interactive menu, looping until the user picks
    "Continue".

    Shown by ``openprogram providers setup`` and by ``openprogram
    setup``'s providers section. On every iteration we show what's
    already imported so the user can judge whether to continue or add
    more. Menu labels lead with what they DO ("Continue", "Add a
    provider login") — never the ambiguous "Quit".

    Returns 0 on clean exit.
    """
    if not _questionary_usable():
        # Non-TTY stdin (test harness, CI, piped input) can't drive
        # questionary's prompt_toolkit UI — it would raise reading from
        # a closed fd. Fall back to the plain-input wizard, which still
        # works under input()-style redirection.
        from .setup import _run_setup as _plain
        return _plain()

    _banner()
    while True:
        has_any = _show_provider_status()

        # Menu order + wording so the first-highlighted option is the
        # expected default. If there's nothing imported yet, the most
        # useful next action is importing — start there. Otherwise
        # surface "Continue" first so users know they can move on.
        choices: list[Any]
        if has_any:
            choices = [
                Choice("Continue — use these credentials",    value="done"),
                Choice("Scan & import discoverable",           value="scan"),
                Choice("Add a provider login",                 value="login"),
                Choice("Diagnose current credentials",         value="doctor"),
            ]
        else:
            choices = [
                Choice("Scan & import discoverable",           value="scan"),
                Choice("Add a provider login",                 value="login"),
                Choice("Skip (set up providers later)",        value="done"),
            ]
        # unsafe_ask lets KeyboardInterrupt propagate. When called from
        # `openprogram setup`'s providers section, the outer
        # run_full_setup catches it and cancels the whole wizard —
        # which is what users expect from Ctrl-C. When called standalone
        # via `openprogram providers setup`, _cmd_setup catches it and
        # prints the "Setup interrupted" message. Either way we never
        # swallow Ctrl-C and silently bounce back to a menu.
        pick = questionary.select(
            "Providers — what now?",
            choices=choices,
            qmark="›",
        ).unsafe_ask()

        if pick is None or pick == "done":
            return 0

        if pick == "scan":
            _action_scan_and_import()
        elif pick == "login":
            _action_pick_provider_and_login()
        elif pick == "doctor":
            _action_run_doctor()


def _show_provider_status() -> bool:
    """Print the providers with credentials and the sign-ins other tools
    hold that are not added yet. Returns True iff any provider has one."""
    from openprogram.models.auth.store import get_store
    store = get_store()
    imported_rows: list[str] = []
    for p in store.list_pools():
        names = ", ".join(c.display_label() for c in p.credentials)
        imported_rows.append(f"  ✓ {p.provider_id}  ({names})")

    available_rows = [f"  · {cred.provider_id}  (via {src.source_id})"
                      for src, cred, held in _discoverable() if not held]

    if imported_rows:
        _say("\nAdded:")
        for row in imported_rows:
            _say(row)
    if available_rows:
        _say("\nAvailable to import:")
        for row in available_rows:
            _say(row)
    if not imported_rows and not available_rows:
        _say("\nNo providers added yet, nothing auto-detected.\n"
             "You can add one via `Scan` (re-tries detection) or "
             "`Add a provider login`.")
    _say("")
    return bool(imported_rows)


def _discoverable() -> list[tuple[Any, Any, bool]]:
    """``(source, credential, already held)`` for each sign-in another tool holds."""
    from openprogram.models.auth.adoption import _sources
    from openprogram.models.auth.store import get_store
    from .setup import _already_held
    store = get_store()
    found: list[tuple[Any, Any, bool]] = []
    for src in _sources():
        try:
            for cred in src.try_import(store.root):
                found.append((src, cred, _already_held(store, cred)))
        except Exception:
            continue
    return found


def pick_login_method_interactive(
    provider: str, choices: list[tuple[str, str]],
) -> Optional[str]:
    """Arrow-key picker for ``openprogram providers login <prov>`` method.

    Returns the chosen method id, or ``None`` if the user cancelled
    (Ctrl-C / EOF). Caller treats None as "abort login".

    Falls back to the numeric picker the CLI used before when
    questionary isn't available.
    """
    if not _questionary_usable():
        return None  # signal to caller: fall through to numeric prompt

    return questionary.select(
        f"How do you want to log into {provider}?",
        choices=[Choice(label, value=mid) for mid, label in choices],
        qmark="›",
    ).unsafe_ask()


# ---------------------------------------------------------------------------
# Top-menu actions
# ---------------------------------------------------------------------------

def _action_scan_and_import() -> None:
    from .formatting import _payload_summary
    from openprogram.models.auth.store import get_store

    store = get_store()
    candidates = _discoverable()
    if not candidates:
        _say("  Nothing discoverable.")
        return

    choices = []
    for i, (src, cred, already) in enumerate(candidates):
        label = (f"{src.source_id:24s} → {cred.provider_id}  "
                 f"{_payload_summary(cred)}"
                 + ("  (already imported)" if already else ""))
        choices.append(Choice(
            label, value=i, checked=not already, disabled=None,
        ))

    picks = questionary.checkbox(
        "Select credentials to import (space to toggle):",
        choices=choices,
        qmark="›",
    ).unsafe_ask()
    if not picks:
        _say("  No selection.")
        return

    from .formatting import _payload_summary as _preview
    adopted = 0
    for i in picks:
        src, cred, already = candidates[i]
        if already:
            continue  # left checked, but it's already in — skip
        kept = store.add_credential(cred)
        adopted += 1
        _say(f"  + {kept.provider_id}: {_preview(kept)}")
    _say(f"  Imported {adopted}.")


def _action_pick_provider_and_login() -> None:
    from .keys import _cmd_add
    from openprogram.models.auth.login.login_method_registry import login_methods
    from openprogram.models.auth.store import get_store

    # Popular canonical providers, plus anything we already have a pool
    # for (so the user can re-login / add a second credential).
    popular = [
        ("anthropic",         "Anthropic (Claude)"),
        ("openai",            "OpenAI (raw API key)"),
        ("openrouter",        "OpenRouter"),
        ("google",            "Google Gemini (direct)"),
        ("groq",              "Groq"),
        ("xai",               "xAI (Grok)"),
        ("mistral",           "Mistral"),
    ]
    store = get_store()
    existing = {p.provider_id for p in store.list_pools()}
    popular_ids = {pid for pid, _ in popular}
    choices = []
    for prov_id, label in popular:
        tag = "✓ " if prov_id in existing else "  "
        choices.append(Choice(f"{tag}{prov_id:22s} — {label}", value=prov_id))
    # Surface any already-configured provider that isn't in the popular
    # list (e.g. a community provider added earlier) so it's re-selectable.
    for prov_id in sorted(existing - popular_ids):
        choices.append(Choice(f"✓ {prov_id:22s} — (configured)", value=prov_id))
    # Escape hatch: configure ANY provider by id — the popular list is a
    # shortcut, not the full set. Matches the web UI, which lists every
    # provider (incl. community ones like minimax-cn / deepseek / kimi).
    choices.append(Choice("  Other — enter a provider id…", value="__other__"))
    choices.append(Choice("← Back", value="__back__"))

    provider = questionary.select(
        "Pick a provider to add a key or sign in to",
        choices=choices,
        qmark="›",
    ).unsafe_ask()
    # questionary.Choice replaces `value=None` with the title, so our
    # "← Back" entries come back as the literal string, never None.
    # Use a sentinel value and match on it here.
    if provider is None or provider == "__back__":
        return
    if provider == "__other__":
        typed = questionary.text(
            "Provider id (see the web UI's LLM Providers list, "
            "e.g. minimax-cn, deepseek):",
            qmark="›",
        ).unsafe_ask()
        if not typed or not typed.strip():
            return
        from openprogram.models.auth.account.aliases import resolve as _canonical
        provider = _canonical(typed.strip())

    methods = login_methods(provider)
    if not methods:
        _say(f"  No login method implemented for {provider!r}.")
        return
    method = pick_login_method_interactive(provider, methods)
    if method is None:
        return

    # Hand off to the shared login implementation — it does the
    # paste/import work and writes to the store.
    _cmd_add(provider, method=method)


def _action_run_doctor() -> None:
    from openprogram.models.auth.doctor import run_doctor
    from .doctor import _print_doctor_report
    report = run_doctor()
    _print_doctor_report(report["pools_checked"], report["findings"])


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _banner() -> None:
    print()
    print("═" * 60)
    print("  OpenProgram — provider setup")
    print("═" * 60)
    print("  Arrow keys to navigate, Space to toggle, Enter to confirm,")
    print("  Ctrl-C to cancel / go back.")
    print()


def _say(msg: str) -> None:
    print(msg, file=sys.stdout, flush=True)


__all__ = [
    "run_interactive_setup",
    "pick_login_method_interactive",
]
