"""First-run setup wizard + per-section config commands.

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


CONFIG_PATH = Path.home() / ".agentic" / "config.json"


# --- storage helpers --------------------------------------------------------

def _read_config() -> dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_config(cfg: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


def read_disabled_tools() -> set[str]:
    """Public helper consumed by openprogram.tools to filter list_available.

    Kept in this module so the tools package doesn't import config from
    deeper webui modules and drag in FastAPI at tool-registry import time.
    """
    cfg = _read_config()
    return set(cfg.get("tools", {}).get("disabled", []) or [])


# --- UI primitives (questionary w/ input() fallback) ------------------------

def _have_questionary() -> bool:
    try:
        import questionary  # noqa: F401
        return True
    except ImportError:
        return False


def _confirm(prompt: str, default: bool = True) -> bool:
    if _have_questionary():
        import questionary
        ans = questionary.confirm(prompt, default=default).ask()
        return bool(ans) if ans is not None else False
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
        ans = questionary.select(prompt, choices=choices,
                                 default=default or choices[0]).ask()
        return ans  # None on Ctrl-C
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
    """Multi-select. Returns list of selected names, or None if cancelled.

    ``items`` = [(name, initial_checked), ...] preserving caller order.
    """
    if not items:
        return []
    if _have_questionary():
        import questionary
        choices = [
            questionary.Choice(name, value=name, checked=enabled)
            for name, enabled in items
        ]
        ans = questionary.checkbox(prompt, choices=choices).ask()
        return ans  # None on Ctrl-C
    # input() fallback: toggle by number, 'all' / 'none' / blank to commit.
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


# --- Sections ---------------------------------------------------------------

def run_providers_section() -> int:
    """Delegate to the existing credential-import wizard."""
    from openprogram.auth.cli import _cmd_setup
    return _cmd_setup()


def run_model_section() -> int:
    """Pick the default chat model across enabled providers."""
    from openprogram.webui import _model_catalog as mc
    enabled = mc.list_enabled_models()
    if not enabled:
        print("No enabled models yet. After you enable a provider in "
              "`openprogram providers setup`, come back and run "
              "`openprogram config model`.")
        return 1

    labels = [f"{m['provider']}/{m['id']}  ({m.get('name', m['id'])})"
              for m in enabled]
    values = [f"{m['provider']}/{m['id']}" for m in enabled]
    label_to_value = dict(zip(labels, values))

    cfg = _read_config()
    cur_prov = cfg.get("default_provider")
    cur_model = cfg.get("default_model")
    current_label = None
    if cur_prov and cur_model:
        for lbl, val in label_to_value.items():
            if val == f"{cur_prov}/{cur_model}":
                current_label = lbl
                break

    picked = _choose_one("Default chat model:", labels, current_label)
    if picked is None:
        print("Cancelled.")
        return 1
    provider, model = label_to_value[picked].split("/", 1)
    cfg["default_provider"] = provider
    cfg["default_model"] = model
    _write_config(cfg)
    print(f"Default set: {provider}/{model}")
    return 0


def run_tools_section() -> int:
    """Pick which tools are enabled by default."""
    from openprogram.tools import ALL_TOOLS
    cfg = _read_config()
    disabled = set(cfg.get("tools", {}).get("disabled", []) or [])
    names = sorted(ALL_TOOLS.keys())
    items = [(n, n not in disabled) for n in names]

    picked = _checkbox("Enable these tools:", items)
    if picked is None:
        print("Cancelled.")
        return 1
    new_disabled = sorted(set(names) - set(picked))
    cfg.setdefault("tools", {})["disabled"] = new_disabled
    _write_config(cfg)
    print(f"Enabled: {len(picked)} / {len(names)} tools")
    if new_disabled:
        print(f"Disabled: {', '.join(new_disabled)}")
    return 0


def run_agent_section() -> int:
    """Default thinking effort and other agent-level defaults."""
    cfg = _read_config()
    current = (cfg.get("agent", {}) or {}).get("thinking_effort") or "medium"

    levels = ["low", "medium", "high", "xhigh"]
    picked = _choose_one("Default thinking effort:", levels, current)
    if picked is None:
        print("Cancelled.")
        return 1
    cfg.setdefault("agent", {})["thinking_effort"] = picked
    _write_config(cfg)
    print(f"Default thinking effort: {picked}")
    return 0


# --- Orchestrator -----------------------------------------------------------

def run_full_setup() -> int:
    """Walk through all four sections in order. Each section is still
    runnable standalone so users can rerun individual pieces later."""
    print("=" * 60)
    print("  OpenProgram first-run setup")
    print("=" * 60)
    print()
    print("Four sections:")
    print("  1) Providers  connect LLM provider(s)")
    print("  2) Model      pick your default chat model")
    print("  3) Tools      enable/disable individual tools")
    print("  4) Agent      default thinking effort, ...")
    print()
    print("Rerun any section alone with `openprogram config <name>`.")
    print()
    if not _confirm("Start?", default=True):
        return 0

    print("\n--- 1/4: providers ---")
    rc = run_providers_section()
    if rc != 0:
        print(f"Providers step exited with status {rc}; continuing.")

    print("\n--- 2/4: default model ---")
    run_model_section()

    print("\n--- 3/4: tools ---")
    if _confirm("Customize the tool list now?", default=False):
        run_tools_section()
    else:
        print("Skipped; all tools remain enabled.")

    print("\n--- 4/4: agent defaults ---")
    run_agent_section()

    print("\nSetup complete. Run `openprogram` to start chatting.")
    return 0
