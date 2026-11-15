"""Terminal prompts for the setup wizards.

Uses ``questionary`` for arrow-key navigation when the dependency is present
and the terminal can render it, and falls back to plain ``input()`` otherwise
so a minimal install still gets a usable wizard. Every ``openprogram setup``
section, ``openprogram channels setup`` and the default-environment section
prompt through these.
"""
from __future__ import annotations


def prompt_schema_group(group: str) -> int:
    """Interactively prompt every setting in a config-schema ``group`` and
    persist each through ``settings.set_setting``.

    The wizard renders from the same schema the TUI panel and
    ``openprogram config`` use, so a new ``SettingSpec`` in this group
    shows up here automatically — no hand-coded prompt. Returns 0, or 1 if
    the user cancels. (state.settings is imported lazily: it imports this
    module, so a top-level import would cycle.)
    """
    from openprogram.state.settings import get_settings, set_setting

    rows = [r for r in get_settings() if r["group"] == group]
    for r in rows:
        key, label, widget, cur = r["key"], r["label"], r["widget"], r.get("value")
        if widget == "toggle":
            res = _confirm(label, default=bool(cur))
            if res is None:
                print("Cancelled.")
                return 1
            out = set_setting(key, res)
        else:
            choices = r.get("choices")
            prompt = f"{label} ({'/'.join(choices)})" if choices else label
            raw = _text(prompt, default=str(cur if cur is not None else ""))
            if raw is None:
                print("Cancelled.")
                return 1
            out = set_setting(key, raw)
        if out.get("error"):
            print(f"  ! {out['error']} — keeping {cur!r}")
        elif out.get("note"):
            print(f"  · {out['note']}")
    return 0


# --- UI primitives (questionary w/ input() fallback) ------------------------


def _have_questionary() -> bool:
    """Return True only if questionary is importable AND the underlying
    ``prompt_toolkit`` output backend can render in this terminal.

    Git Bash / MinTTY on Windows passes the import check but raises
    ``NoConsoleScreenBufferError`` at first prompt — would crash the
    whole setup wizard before any input was read. The cross-platform
    probe in :mod:`openprogram.host._platform.terminal` catches that case so the
    plain-``input()`` fallback below kicks in instead.
    """
    try:
        import questionary  # noqa: F401
    except ImportError:
        return False
    from openprogram.host._platform.terminal import prompt_toolkit_usable

    return prompt_toolkit_usable()


# Consistent look across every prompt in the wizard. Cursor-highlighted
# item is the obvious one (bright cyan on inverse); non-cursor items
# stay plain; pointer is an unambiguous `❯`. Applied to every
# questionary call site via style=_QSTYLE + pointer=_POINTER.
_POINTER = "❯"


def _qstyle():
    """Late-bound style object so import-time failures in questionary
    don't cascade into setup import.

    Never pass ``default=`` to a single-select prompt. Questionary's
    ``_is_selected`` (prompts/common.py:327) flags the default-matching
    choice permanently, and the render code falls into an ``elif``
    cascade where ``class:selected`` wins over ``class:highlighted``
    forever — so the cursor-on-that-row state is never reachable. Put
    the desired default at index 0 instead and let questionary's own
    initial-pointer land on it. Then ``class:highlighted`` works as
    expected: whichever row the cursor is on gets the cyan bold style.
    """
    try:
        from questionary import Style
    except ImportError:
        return None
    return Style(
        [
            ("qmark", "fg:ansicyan bold"),
            ("question", "bold"),
            ("answer", "fg:ansicyan bold"),
            ("pointer", "fg:ansicyan bold"),
            ("highlighted", "fg:ansicyan bold"),
            ("selected", "fg:ansicyan"),
            ("separator", "fg:ansibrightblack"),
            ("instruction", "fg:ansibrightblack"),
            ("disabled", "fg:ansibrightblack italic"),
        ]
    )


def _confirm(prompt: str, default: bool = True) -> bool:
    """Arrow-key Yes/No select. Uses questionary.select for a consistent
    look with every other prompt — no y/n keypress.

    Default is placed at index 0 (not passed via ``default=``) — see
    the comment in ``_qstyle`` for why.
    """
    if _have_questionary():
        import questionary

        choices = ["Yes", "No"] if default else ["No", "Yes"]
        # unsafe_ask raises KeyboardInterrupt on Ctrl-C instead of
        # returning None — so Ctrl-C in ANY prompt aborts the whole
        # wizard (caught once at run_full_setup's top-level try/except)
        # instead of silently bouncing to the next section.
        ans = questionary.select(
            prompt,
            choices=choices,
            use_shortcuts=False,
            use_arrow_keys=True,
            instruction="(↑/↓ enter)",
            pointer=_POINTER,
            style=_qstyle(),
        ).unsafe_ask()
        return ans == "Yes"
    hint = "Y/n" if default else "y/N"
    try:
        s = input(f"{prompt} [{hint}] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not s:
        return default
    return s in ("y", "yes")


def _choose_one(
    prompt: str, choices: list[str], default: str | None = None
) -> str | None:
    if not choices:
        return None
    if _have_questionary():
        import questionary

        # Never pass default= to questionary.select — see _qstyle
        # docstring. Reorder so the default sits at index 0; the initial
        # cursor position lands on it naturally.
        if default and default in choices and choices[0] != default:
            choices = [default] + [c for c in choices if c != default]
        ans = questionary.select(
            prompt,
            choices=choices,
            use_shortcuts=False,
            use_arrow_keys=True,
            instruction="(↑/↓ enter)",
            pointer=_POINTER,
            style=_qstyle(),
        ).unsafe_ask()
        return ans
    print(prompt)
    for i, c in enumerate(choices, 1):
        marker = "*" if c == default else " "
        print(f"  {marker} {i:>2}) {c}")
    try:
        raw = input(
            f"? [{(choices.index(default) + 1) if default in choices else 1}] "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return default if default in choices else choices[0]
    try:
        idx = int(raw) - 1
    except ValueError:
        print(f"Invalid: {raw!r}")
        return None
    if 0 <= idx < len(choices):
        return choices[idx]
    return None


def _checkbox(prompt: str, items: list[tuple[str, bool]]) -> list[str] | None:
    """Multi-select. space to toggle, enter to commit."""
    if not items:
        return []
    if _have_questionary():
        import questionary

        choices = [
            questionary.Choice(name, value=name, checked=enabled)
            for name, enabled in items
        ]
        ans = questionary.checkbox(
            prompt,
            choices=choices,
            instruction="(space to toggle, enter to confirm, a = all, i = invert)",
            pointer=_POINTER,
            style=_qstyle(),
        ).unsafe_ask()
        return ans
    names = [n for n, _ in items]
    selected: set[str] = {n for n, e in items if e}
    while True:
        print(prompt)
        for i, (n, _) in enumerate(items, 1):
            mark = "[x]" if n in selected else "[ ]"
            print(f"  {mark} {i:>2}) {n}")
        print("Enter numbers (1,3,5) to toggle, 'all' / 'none', or blank to finish.")
        try:
            raw = input("? ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        if raw == "":
            return sorted(selected)
        if raw == "all":
            selected = set(names)
            continue
        if raw == "none":
            selected = set()
            continue
        try:
            for tok in raw.split(","):
                idx = int(tok.strip()) - 1
                if 0 <= idx < len(names):
                    n = names[idx]
                    if n in selected:
                        selected.remove(n)
                    else:
                        selected.add(n)
                else:
                    print(f"  out of range: {idx + 1}")
        except ValueError:
            print(f"  invalid: {raw!r}")


def _text(prompt: str, default: str = "") -> str | None:
    if _have_questionary():
        import questionary

        ans = questionary.text(
            prompt,
            default=default,
            instruction="(enter to accept)" if default else "",
            style=_qstyle(),
        ).unsafe_ask()
        return ans
    hint = f" [{default}]" if default else ""
    try:
        s = input(f"{prompt}{hint} ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return s or default


def _password(prompt: str) -> str | None:
    if _have_questionary():
        import questionary

        ans = questionary.password(
            prompt,
            style=_qstyle(),
        ).unsafe_ask()
        return ans
    try:
        import getpass

        return getpass.getpass(f"{prompt} ")
    except (EOFError, KeyboardInterrupt):
        return None

