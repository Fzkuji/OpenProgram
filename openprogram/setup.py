"""First-run setup + per-section config commands.

Four sections, each runnable standalone:

    openprogram setup                 # full walk-through
    openprogram providers setup       # section 1 alone
    openprogram config model          # section 2 alone
    openprogram config tools          # section 3 alone
    openprogram config agent          # section 4 alone

UI layer: uses ``questionary`` for arrow-key navigation when the
dep is present, falls back to plain ``input()`` otherwise so a
minimal install still gets a usable wizard.

Storage lives under ``~/.agentic/config.json`` alongside the
existing provider / api_keys config. Keys written here:
    default_provider   str
    default_model      str
    tools.disabled     list[str]
    agent.thinking_effort  str  (low/medium/high/xhigh)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from openprogram.paths import get_config_path


# Back-compat export — many readers used to import CONFIG_PATH directly.
# Kept as a property-like getter: evaluating it returns the current
# profile's config path rather than a value frozen at import time.
class _ConfigPathProxy:
    def __fspath__(self) -> str:
        return str(get_config_path())
    def __str__(self) -> str:
        return str(get_config_path())
    def __repr__(self) -> str:
        return f"ConfigPath({get_config_path()!s})"
    @property
    def parent(self) -> Path:
        return get_config_path().parent
    def read_text(self, *a, **kw):
        return get_config_path().read_text(*a, **kw)
    def write_text(self, *a, **kw):
        return get_config_path().write_text(*a, **kw)


CONFIG_PATH: Any = _ConfigPathProxy()


# --- storage helpers --------------------------------------------------------

def _read_config() -> dict[str, Any]:
    try:
        return json.loads(get_config_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_config(cfg: dict[str, Any]) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n")


def read_disabled_tools() -> set[str]:
    """Public helper consumed by openprogram.tools to filter list_available.

    Kept in this module so the tools package doesn't import config from
    deeper webui modules and drag in FastAPI at tool-registry import time.

    Also honours ``memory.backend == "none"`` by hiding every memory
    tool (note / recall / reflect / get / browse / lint / ingest),
    since they have no backing store in that mode.
    """
    cfg = _read_config()
    disabled = set(cfg.get("tools", {}).get("disabled", []) or [])
    if (cfg.get("memory", {}) or {}).get("backend") == "none":
        disabled.update({
            "memory_note", "memory_recall", "memory_reflect",
            "memory_get", "memory_browse", "memory_lint",
            "memory_ingest", "memory_backlinks",
        })
    return disabled


def read_disabled_skills() -> set[str]:
    """Skills the default agent opts out of.

    In the multi-agent model, skill enablement is per-agent. Callers
    that still think in global terms (the CLI chat banner, for
    example) read the default agent's list here.
    """
    from openprogram.agents import manager as _agents
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
    cfg = _read_config()
    section = dict(cfg.get("search") or {})
    if name:
        section["default_provider"] = name
    else:
        section.pop("default_provider", None)
    if section:
        cfg["search"] = section
    else:
        cfg.pop("search", None)
    _write_config(cfg)


def read_ui_prefs() -> dict[str, Any]:
    cfg = _read_config()
    ui = cfg.get("ui", {}) or {}
    return {
        "port": int(ui.get("port") or 8110),
        "open_browser": bool(ui.get("open_browser", True)),
    }


def read_agent_prefs() -> dict[str, Any]:
    """Back-compat shim for callers that want a loose "what are the
    agent defaults?" dict. Pulls from the default agent record."""
    from openprogram.agents import manager as _agents
    agent = _agents.get_default()
    effort = (agent.thinking_effort if agent else None) or "medium"
    return {"thinking_effort": effort}


# --- UI primitives (questionary w/ input() fallback) ------------------------

def _have_questionary() -> bool:
    try:
        import questionary  # noqa: F401
        return True
    except ImportError:
        return False


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
    return Style([
        ("qmark",        "fg:ansicyan bold"),
        ("question",     "bold"),
        ("answer",       "fg:ansicyan bold"),
        ("pointer",      "fg:ansicyan bold"),
        ("highlighted",  "fg:ansicyan bold"),
        ("selected",     "fg:ansicyan"),
        ("separator",    "fg:ansibrightblack"),
        ("instruction",  "fg:ansibrightblack"),
        ("disabled",     "fg:ansibrightblack italic"),
    ])


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


def _choose_one(prompt: str, choices: list[str],
                default: str | None = None) -> str | None:
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
        raw = input(f"? [{(choices.index(default) + 1) if default in choices else 1}] ").strip()
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
            selected = set(names); continue
        if raw == "none":
            selected = set(); continue
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
# Section runners + wizard orchestrator live in openprogram/_setup_sections/.
# Re-exported here under the names cli.py and tests import directly off
# ``openprogram.setup``.
# ---------------------------------------------------------------------------

from openprogram._setup_sections.sections import (  # noqa: E402,F401
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
from openprogram._setup_sections.channels import (  # noqa: E402,F401
    run_channels_section,
)
from openprogram._setup_sections.backend import (  # noqa: E402,F401
    run_backend_section,
)
from openprogram._setup_sections.wizard import (  # noqa: E402,F401
    run_full_setup,
    run_configure_menu,
)
