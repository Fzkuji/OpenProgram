"""First-run setup + per-section config commands.

Four sections, each runnable standalone:

    openprogram setup                 # full walk-through
    openprogram providers setup       # section 1 alone
    openprogram setup model           # section 2 alone
    openprogram setup tools           # section 3 alone
    openprogram setup agent           # section 4 alone

UI layer: uses ``questionary`` for arrow-key navigation when the
dep is present, falls back to plain ``input()`` otherwise so a
minimal install still gets a usable wizard.

Storage lives under ``~/.openprogram/config.json`` alongside the
existing provider / api_keys config. Keys written here:
    default_provider   str
    default_model      str
    tools.disabled     list[str]
    agent.thinking_effort  str  (low/medium/high/xhigh)
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable

from openprogram.paths import get_config_path


# --- storage helpers --------------------------------------------------------


def _read_config() -> dict[str, Any]:
    try:
        return json.loads(get_config_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def read_config_with_revision() -> tuple[dict[str, Any], str]:
    """Read a prompt snapshot and its exact-byte revision under the file lock."""
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    from openprogram.auth.credentials import (
        _private_file_lock,
        _read_private_bytes,
        _revision,
    )

    with _private_file_lock(path, root=path.parent):
        raw = _read_private_bytes(path, root=path.parent)
        revision = _revision(raw)
    try:
        config = json.loads(raw.decode("utf-8")) if raw is not None else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        config = {}
    return config, revision


def _write_config(cfg: dict[str, Any], *, expected_revision: str | None = None):
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(cfg, indent=2) + "\n"
    from openprogram.auth.credentials import _private_atomic_write

    return _private_atomic_write(
        path,
        lambda handle: handle.write(data.encode("utf-8")),
        root=path.parent,
        expected_revision=expected_revision,
    )


# In-process lock for the read-modify-write critical section. Cross-process
# safety comes from the file lock acquired inside ``update_config``.
_config_write_lock = threading.Lock()


def update_config(
    mutator: Callable[[dict], Any], *, expected_revision: str | None = None
) -> dict:
    """Atomic read-modify-write of config.json.

    Holds an in-process lock (the worker's threads) AND a cross-process file
    lock (``config.json.lock`` — the worker vs the ``openprogram config`` /
    ``openprogram setup`` processes), reads the current config, applies
    ``mutator(cfg)`` in place, writes it back (0o600) and returns it.

    This is the ONLY correct way to change *part* of the config — a bare
    ``_read_config()`` … ``_write_config()`` pair races (the later write
    clobbers a concurrent one). ``_read_config`` / ``_write_config`` remain for
    read-only and full-replace.
    """
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    updated: dict[str, Any] = {}

    def update(raw: bytes | None) -> bytes:
        nonlocal updated
        try:
            cfg = json.loads(raw.decode("utf-8")) if raw is not None else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            cfg = {}
        mutator(cfg)
        updated = cfg
        return (json.dumps(cfg, indent=2) + "\n").encode("utf-8")

    with _config_write_lock:
        from openprogram.auth.credentials import _private_atomic_update

        _private_atomic_update(
            path,
            update,
            root=path.parent,
            expected_revision=expected_revision,
        )
    return updated


def read_disabled_tools() -> set[str]:
    """Public helper consumed by openprogram.programs to filter list_available.

    Kept in this module so the tools package doesn't import config from
    deeper webui modules and drag in FastAPI at tool-registry import time.

    Also honours ``memory.backend == "none"`` by hiding every memory tool,
    since they have no backing store in that mode. Which tools those are
    is read from the bundle that registers them, so a renamed tool cannot
    survive the switch by no longer matching a name written down here.
    """
    cfg = _read_config()
    disabled = set(cfg.get("tools", {}).get("disabled", []) or [])
    if (cfg.get("memory", {}) or {}).get("backend") == "none":
        from openprogram.programs.functions.vanilla.knowledge.memory import MEMORY_TOOL_NAMES

        disabled.update(MEMORY_TOOL_NAMES)
    return disabled


def read_disabled_skills() -> set[str]:
    """Skills the default agent opts out of.

    In the multi-agent model, skill enablement is per-agent. Callers
    that still think in global terms (the CLI chat banner, for
    example) read the default agent's list here.
    """
    from openprogram.agent.management import manager as _agents

    agent = _agents.get_default()
    if agent is None:
        return set()
    return set((agent.skills or {}).get("disabled") or [])


def read_search_default_provider() -> str | None:
    """User-pinned default web_search backend, or None to use priority order.

    Stored as ``cfg["search"]["default_provider"]``. Resolved at every
    web_search call so a change in settings takes effect immediately
    without a worker restart.
    """
    cfg = _read_config()
    name = ((cfg.get("search") or {}).get("default_provider") or "").strip()
    return name or None


def write_search_default_provider(name: str | None) -> None:
    """Persist the user's default web_search backend (or clear it)."""

    def _mut(cfg: dict) -> None:
        section = dict(cfg.get("search") or {})
        if name:
            section["default_provider"] = name
        else:
            section.pop("default_provider", None)
        if section:
            cfg["search"] = section
        else:
            cfg.pop("search", None)

    update_config(_mut)


# Default port. Uncommon 5-digit value in the registered-port range
# (< 49152, so it never collides with the OS ephemeral range); the
# 18xxx block is rarely used by mainstream services. Single-port
# architecture: the FastAPI worker serves the API, /ws AND the frontend
# static export on one port, stored under the ``web_port`` pref.
DEFAULT_WEB_PORT = 18100


def read_ui_prefs() -> dict[str, Any]:
    cfg = _read_config()
    ui = cfg.get("ui", {}) or {}
    return {
        "web_port": int(ui.get("web_port") or DEFAULT_WEB_PORT),
        "open_browser": bool(ui.get("open_browser", True)),
    }


def set_ui_ports(
    *,
    web_port: int | None = None,
    open_browser: bool | None = None,
) -> dict[str, Any]:
    """Persist UI prefs to the config. Only the keys passed are
    changed; the rest keep their stored values. Returns the resulting
    ``read_ui_prefs()`` dict. Takes effect on the next ``openprogram web``
    / ``worker`` start — nothing live is rebound here.
    """

    def _mut(cfg: dict) -> None:
        ui = cfg.setdefault("ui", {})
        if web_port is not None:
            ui["web_port"] = int(web_port)
        if open_browser is not None:
            ui["open_browser"] = bool(open_browser)

    update_config(_mut)
    return read_ui_prefs()


def prompt_schema_group(group: str) -> int:
    """Interactively prompt every setting in a config-schema ``group`` and
    persist each through ``config_schema.set_setting``.

    The wizard renders from the same schema the TUI panel and
    ``openprogram config`` use, so a new ``SettingSpec`` in this group
    shows up here automatically — no hand-coded prompt. Returns 0, or 1 if
    the user cancels. (config_schema is imported lazily: it imports this
    module, so a top-level import would cycle.)
    """
    from openprogram.config_schema import get_settings, set_setting

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
    probe in :mod:`openprogram._compat` catches that case so the
    plain-``input()`` fallback below kicks in instead.
    """
    try:
        import questionary  # noqa: F401
    except ImportError:
        return False
    from openprogram._compat import prompt_toolkit_usable

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


# --- Sections ---------------------------------------------------------------


# ---------------------------------------------------------------------------
# Section runners + wizard orchestrator are owned by the Python CLI app.
# Re-exported here under the names cli.py and tests import directly off
# ``openprogram.setup``.
# ---------------------------------------------------------------------------

from openprogram.cli.setup_sections.sections import (  # noqa: E402,F401
    _ensure_default_agent,
    run_providers_section,
    run_model_section,
    run_tools_section,
    run_agent_section,
    run_skills_section,
    run_ui_section,
    run_memory_section,
    run_profile_section,
    run_search_section,
    run_tts_section,
)
from openprogram.cli.setup_sections.channels import (  # noqa: E402,F401
    run_channels_section,
)
from openprogram.cli.setup_sections.backend import (  # noqa: E402,F401
    run_backend_section,
)
from openprogram.cli.setup_sections.wizard import (  # noqa: E402,F401
    run_full_setup,
    run_configure_menu,
)
