"""Interactive auth wizard — arrow-key menus, multi-select, back-nav.

OpenClaw uses `@clack/prompts`. The closest Python equivalent is
`questionary` (prompt_toolkit-based); same three primitives:

  * ``select(choices)``       — one of N, arrow keys
  * ``checkbox(choices)``     — multi-select, space to toggle
  * ``confirm(message)``      — y/n

If questionary isn't installed (``pip install openprogram`` with no
extras, or a minimal sandbox), every primitive falls back to plain
``input()`` — same behaviour as before, just less ergonomic. No hard
dependency at import time so ``openprogram providers list`` keeps
working in environments that don't have questionary.

Ctrl-C in questionary returns ``None`` from the prompt; treat that as
"go back / cancel". The top-level :func:`run_interactive_setup` loops
until the user picks Quit or sends EOF.

All real work (scanning, adopting, login, profile CRUD) is delegated to
the same helpers that the non-interactive CLI uses — this module only
handles presentation.
"""
from __future__ import annotations

import sys
from typing import Any, Callable, Optional

try:  # optional dependency — see module docstring
    import questionary
    from questionary import Choice
    _HAS_QUESTIONARY = True
except ImportError:  # pragma: no cover — graceful degradation
    questionary = None  # type: ignore[assignment]
    Choice = None        # type: ignore[assignment]
    _HAS_QUESTIONARY = False


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def run_interactive_setup() -> int:
    """Top-level interactive menu, looping until Quit.

    Shown by ``openprogram providers setup``. Each menu action returns
    to this top menu on completion or on back-nav (Ctrl-C) so the user
    can chain multiple actions in one session.

    Returns 0 on clean exit (Quit or EOF), 130 if an inner action was
    interrupted and the user chose not to continue.
    """
    if not _HAS_QUESTIONARY or not sys.stdin.isatty():
        # Non-TTY stdin (test harness, CI, piped input) can't drive
        # questionary's prompt_toolkit UI — it would raise reading from
        # a closed fd. Fall back to the plain-input wizard, which still
        # works under input()-style redirection.
        from .cli import _run_setup_wizard as _plain
        return _plain()

    _banner()
    while True:
        try:
            pick = questionary.select(
                "What do you want to do?",
                choices=[
                    Choice("Scan & import discoverable credentials", value="scan"),
                    Choice("Log into a specific provider",           value="login"),
                    Choice("Run diagnostic on current pools",        value="doctor"),
                    Choice("Manage profiles",                        value="profiles"),
                    Choice("Show current pools",                     value="list"),
                    Choice("Quit",                                   value="quit"),
                ],
                qmark="›",
            ).ask()
        except KeyboardInterrupt:
            return 0

        if pick is None or pick == "quit":
            _say("Bye.")
            return 0

        # Each branch is wrapped so Ctrl-C bounces back to the top
        # menu instead of aborting the whole session.
        try:
            if pick == "scan":
                _action_scan_and_import()
            elif pick == "login":
                _action_pick_provider_and_login()
            elif pick == "doctor":
                _action_run_doctor()
            elif pick == "profiles":
                _action_profiles_menu()
            elif pick == "list":
                _action_list_pools()
        except KeyboardInterrupt:
            _say("  (back to menu)")
            continue


def pick_login_method_interactive(
    provider: str, choices: list[tuple[str, str]],
) -> Optional[str]:
    """Arrow-key picker for ``openprogram providers login <prov>`` method.

    Returns the chosen method id, or ``None`` if the user cancelled
    (Ctrl-C / EOF). Caller treats None as "abort login".

    Falls back to the numeric picker the CLI used before when
    questionary isn't available.
    """
    if not _HAS_QUESTIONARY or not sys.stdin.isatty():
        return None  # signal to caller: fall through to numeric prompt

    try:
        return questionary.select(
            f"How do you want to log into {provider}?",
            choices=[Choice(f"{mid:24s} — {label}", value=mid) for mid, label in choices],
            qmark="›",
        ).ask()
    except KeyboardInterrupt:
        return None


# ---------------------------------------------------------------------------
# Top-menu actions
# ---------------------------------------------------------------------------

def _action_scan_and_import() -> None:
    from .cli import _payload_summary
    from .sources import (
        ClaudeCodeSource,
        CodexCliSource,
        EnvApiKeySource,
        GhCliSource,
        QwenCliSource,
    )
    from .profiles import DEFAULT_PROFILE_NAME, get_profile_manager
    from .store import get_store

    store = get_store()
    pm = get_profile_manager()
    profile = _pick_profile(pm, default=DEFAULT_PROFILE_NAME) or DEFAULT_PROFILE_NAME
    profile_obj = pm.get_profile(profile)

    sources: list[Any] = [
        CodexCliSource(profile_id=profile),
        ClaudeCodeSource(profile_id=profile),
        QwenCliSource(profile_id=profile),
        GhCliSource(),
    ]
    from openprogram.providers.env_api_keys import PROVIDER_ENV_VARS
    for p_id, env_var in PROVIDER_ENV_VARS.items():
        sources.append(
            EnvApiKeySource(provider_id=p_id, env_var=env_var, profile_id=profile),
        )

    candidates: list[tuple[Any, Any]] = []
    for src in sources:
        try:
            for cred in src.try_import(profile_obj.root):
                candidates.append((src, cred))
        except Exception:
            continue

    if not candidates:
        _say("  Nothing discoverable.")
        return

    # Pre-check items whose (provider, source) isn't already in the pool
    # so re-runs default to "nothing new" rather than "import everything".
    choices = []
    for i, (src, cred) in enumerate(candidates):
        cred.profile_id = profile
        existing = store.find_pool(cred.provider_id, cred.profile_id)
        already = existing is not None and any(
            c.source == cred.source for c in existing.credentials
        )
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
    ).ask()
    if not picks:
        _say("  No selection.")
        return

    from .cli import _payload_summary as _preview
    adopted = 0
    for i in picks:
        src, cred = candidates[i]
        existing = store.find_pool(cred.provider_id, cred.profile_id)
        if existing and any(c.source == cred.source for c in existing.credentials):
            continue  # user left it checked but it's already in — skip
        store.add_credential(cred)
        adopted += 1
        _say(f"  + {cred.provider_id}/{cred.profile_id}: {_preview(cred)}")
    _say(f"  Imported {adopted}.")


def _action_pick_provider_and_login() -> None:
    from .cli import _cmd_login, _available_login_methods
    from .aliases import known_aliases
    from .profiles import DEFAULT_PROFILE_NAME, get_profile_manager
    from .store import get_store

    # Popular canonical providers, plus anything we already have a pool
    # for (so the user can re-login / add a second credential).
    popular = [
        ("openai-codex",      "OpenAI via Codex CLI (ChatGPT account)"),
        ("anthropic",         "Anthropic (Claude)"),
        ("google-gemini-cli", "Google Gemini via CLI"),
        ("github-copilot",    "GitHub Copilot"),
        ("openai",            "OpenAI (raw API key)"),
        ("openrouter",        "OpenRouter"),
        ("google",            "Google Gemini (direct)"),
        ("groq",              "Groq"),
        ("xai",               "xAI (Grok)"),
        ("mistral",           "Mistral"),
    ]
    store = get_store()
    pm = get_profile_manager()
    profile = _pick_profile(pm, default=DEFAULT_PROFILE_NAME) or DEFAULT_PROFILE_NAME

    existing = {p.provider_id for p in store.list_pools() if p.profile_id == profile}
    choices = []
    for prov_id, label in popular:
        tag = "✓ " if prov_id in existing else "  "
        choices.append(Choice(f"{tag}{prov_id:22s} — {label}", value=prov_id))
    choices.append(Choice("← Back", value="__back__"))

    provider = questionary.select(
        f"Pick a provider to log into  (profile: {profile})",
        choices=choices,
        qmark="›",
    ).ask()
    # questionary.Choice replaces `value=None` with the title, so our
    # "← Back" entries come back as the literal string, never None.
    # Use a sentinel value and match on it here.
    if provider is None or provider == "__back__":
        return

    methods = _available_login_methods(provider)
    if not methods:
        _say(f"  No login method implemented for {provider!r}.")
        return
    method = pick_login_method_interactive(provider, methods)
    if method is None:
        return

    # Hand off to the shared login implementation — it does the
    # paste/import work and writes to the store.
    _cmd_login(provider, profile, method)


def _action_run_doctor() -> None:
    from .cli import run_doctor, _print_doctor_report
    report = run_doctor()
    _print_doctor_report(
        report["pools_checked"], report["profiles_checked"], report["findings"],
    )


def _action_list_pools() -> None:
    from .cli import _cmd_list
    _cmd_list(profile_filter=None, as_json=False)


def _action_profiles_menu() -> None:
    from .profiles import get_profile_manager, AuthConfigError
    pm = get_profile_manager()

    while True:
        profiles = pm.list_profiles()
        labels = [Choice(f"{p.name:16s}  {p.display_name or '-'}", value=p.name)
                  for p in profiles]
        labels.append(Choice("+ Create new profile", value="__create__"))
        labels.append(Choice("← Back",               value="__back__"))

        pick = questionary.select(
            "Profiles", choices=labels, qmark="›",
        ).ask()
        if pick is None or pick == "__back__":
            return

        if pick == "__create__":
            name = questionary.text("Profile name:").ask()
            if not name:
                continue
            display = questionary.text(
                "Display name (optional):", default="",
            ).ask() or ""
            try:
                pm.create_profile(name.strip(), display_name=display)
                _say(f"  Created profile {name}.")
            except AuthConfigError as e:
                _say(f"  Failed: {e}")
            continue

        # Existing profile selected — offer delete or back.
        sub = questionary.select(
            f"Profile {pick!r}",
            choices=[
                Choice("Delete",  value="delete"),
                Choice("← Back",  value="__back__"),
            ],
            qmark="›",
        ).ask()
        if sub == "delete":
            ok = questionary.confirm(
                f"Delete profile {pick!r} and all its credentials?",
                default=False,
            ).ask()
            if ok:
                try:
                    pm.delete_profile(pick)
                    _say(f"  Deleted {pick}.")
                except AuthConfigError as e:
                    _say(f"  Failed: {e}")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _pick_profile(pm, *, default: str) -> Optional[str]:
    """Ask which profile to act on. Returns None on cancel."""
    profiles = pm.list_profiles()
    if len(profiles) == 1:
        return profiles[0].name
    choices = [Choice(p.name, value=p.name) for p in profiles]
    return questionary.select(
        "Which profile?",
        choices=choices,
        default=default,
        qmark="›",
    ).ask()


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
