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

    Also honours ``memory.backend == "none"`` by hiding the ``memory``
    tool, since it has no backing store in that mode.
    """
    cfg = _read_config()
    disabled = set(cfg.get("tools", {}).get("disabled", []) or [])
    if (cfg.get("memory", {}) or {}).get("backend") == "none":
        disabled.add("memory")
    return disabled


def read_disabled_skills() -> set[str]:
    """Skills the user opted out of in `openprogram config skills`."""
    cfg = _read_config()
    return set(cfg.get("skills", {}).get("disabled", []) or [])


def read_ui_prefs() -> dict[str, Any]:
    cfg = _read_config()
    ui = cfg.get("ui", {}) or {}
    return {
        "port": int(ui.get("port") or 8765),
        "open_browser": bool(ui.get("open_browser", True)),
    }


def read_agent_prefs() -> dict[str, Any]:
    cfg = _read_config()
    agent = cfg.get("agent", {}) or {}
    return {
        "thinking_effort": agent.get("thinking_effort") or "medium",
    }


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


def _text(prompt: str, default: str = "") -> str | None:
    if _have_questionary():
        import questionary
        ans = questionary.text(prompt, default=default).ask()
        return ans
    hint = f" [{default}]" if default else ""
    try:
        s = input(f"{prompt}{hint} ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return s or default


def _password(prompt: str) -> str | None:
    """Secret entry — masks input when questionary is available."""
    if _have_questionary():
        import questionary
        ans = questionary.password(prompt).ask()
        return ans
    try:
        import getpass
        return getpass.getpass(f"{prompt} ")
    except (EOFError, KeyboardInterrupt):
        return None


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


# --- Phase 2 sections: skills, ui, memory -----------------------------------

def run_skills_section() -> int:
    """Pick which skills (SKILL.md entries) are enabled."""
    try:
        from openprogram.agentic_programming import (
            default_skill_dirs, load_skills,
        )
        skills = load_skills(default_skill_dirs())
    except Exception as e:
        print(f"Failed to scan skills: {e}")
        return 1
    if not skills:
        print("No skills discovered. Nothing to configure.")
        return 0

    cfg = _read_config()
    disabled = set(cfg.get("skills", {}).get("disabled", []) or [])
    names = sorted(s.name for s in skills)
    items = [(n, n not in disabled) for n in names]

    picked = _checkbox("Enable these skills:", items)
    if picked is None:
        print("Cancelled.")
        return 1
    new_disabled = sorted(set(names) - set(picked))
    cfg.setdefault("skills", {})["disabled"] = new_disabled
    _write_config(cfg)
    print(f"Enabled: {len(picked)} / {len(names)} skills")
    if new_disabled:
        print(f"Disabled: {', '.join(new_disabled)}")
    return 0


def run_ui_section() -> int:
    """Web UI preferences: port + auto-open browser."""
    cfg = _read_config()
    ui = cfg.get("ui", {}) or {}
    cur_port = str(ui.get("port") or 8765)
    cur_open = bool(ui.get("open_browser", True))

    port_raw = _text("Web UI port:", default=cur_port)
    if port_raw is None:
        print("Cancelled.")
        return 1
    try:
        port = int(port_raw)
    except ValueError:
        print(f"Invalid port: {port_raw!r}")
        return 1

    open_browser = _confirm("Open browser automatically on `openprogram web`?",
                            default=cur_open)
    cfg.setdefault("ui", {}).update({
        "port": port,
        "open_browser": open_browser,
    })
    _write_config(cfg)
    print(f"UI: port={port}, open_browser={open_browser}")
    return 0


def run_memory_section() -> int:
    """Memory backend for the ``memory`` tool.

    OpenProgram currently has one native backend: ``local`` (JSON files
    under ~/.agentic/memory). Leaving the option in so hermes-style
    plugin backends (mem0 / honcho / ...) can slot in later without
    re-architecting this section.
    """
    cfg = _read_config()
    cur = (cfg.get("memory", {}) or {}).get("backend") or "local"
    choices = ["local", "none"]
    picked = _choose_one("Memory backend:", choices, cur)
    if picked is None:
        print("Cancelled.")
        return 1
    cfg.setdefault("memory", {})["backend"] = picked
    _write_config(cfg)
    print(f"Memory backend: {picked}")
    if picked == "none":
        print("(The `memory` tool will no-op until a backend is selected.)")
    return 0


# --- Phase 3 sections: profile, tts, channels, backend ----------------------

def run_profile_section() -> int:
    """Named profile (active config slot).

    For now only records the active profile name. Routing per-profile
    config-path / state dirs is a follow-up — but storing the name
    lets external tooling (and future runtime) honour it.
    """
    cfg = _read_config()
    cur = cfg.get("profile", "default") or "default"
    name = _text("Active profile name:", default=cur)
    if not name:
        print("Cancelled.")
        return 1
    cfg["profile"] = name
    _write_config(cfg)
    print(f"Active profile: {name}")
    print("[info] Per-profile config isolation is not wired yet — only "
          "the active-profile name is persisted.")
    return 0


def run_tts_section() -> int:
    """Text-to-speech backend + credentials.

    Wizard writes config; runtime hookup (spoken replies) is a separate
    follow-up. Providers mirror hermes' common set.
    """
    cfg = _read_config()
    tts = cfg.get("tts", {}) or {}
    cur_prov = tts.get("provider") or "none"

    providers = [
        "none",
        "openai",          # OPENAI_API_KEY
        "elevenlabs",      # ELEVENLABS_API_KEY
        "edge-tts",        # no key, uses Microsoft Edge free tier
        "playht",          # PLAYHT_USER_ID + PLAYHT_API_KEY
    ]
    picked = _choose_one("TTS provider:", providers, cur_prov)
    if picked is None:
        print("Cancelled.")
        return 1

    entry: dict[str, Any] = {"provider": picked}
    if picked in ("openai", "elevenlabs", "playht"):
        env_map = {
            "openai": "OPENAI_API_KEY",
            "elevenlabs": "ELEVENLABS_API_KEY",
            "playht": "PLAYHT_API_KEY",
        }
        entry["api_key_env"] = env_map[picked]
        if not os.environ.get(entry["api_key_env"]):
            key = _password(f"{entry['api_key_env']} (leave blank to set later):")
            if key:
                cfg.setdefault("api_keys", {})[entry["api_key_env"]] = key
    cfg["tts"] = entry
    _write_config(cfg)
    print(f"TTS: {picked}")
    if picked != "none":
        print("[info] Runtime hookup for spoken replies is not wired yet; "
              "the choice is stored for when it lands.")
    return 0


def run_channels_section() -> int:
    """Chat-channel bot integrations (Telegram / Discord / Slack).

    Each platform has an 'enabled' flag + minimal credential slot. The
    actual gateway / bot loop is separate infrastructure work — this
    section just captures intent + credentials.
    """
    cfg = _read_config()
    ch = cfg.get("channels", {}) or {}

    PLATFORMS = [
        ("telegram", "Telegram bot token", "TELEGRAM_BOT_TOKEN"),
        ("discord",  "Discord bot token",  "DISCORD_BOT_TOKEN"),
        ("slack",    "Slack bot token",    "SLACK_BOT_TOKEN"),
    ]
    items = [
        (p[0], bool((ch.get(p[0]) or {}).get("enabled", False)))
        for p in PLATFORMS
    ]
    picked = _checkbox("Enable channels:", items)
    if picked is None:
        print("Cancelled.")
        return 1

    new_ch: dict[str, Any] = {}
    for pid, _label, env in PLATFORMS:
        prev = ch.get(pid, {}) or {}
        enabled = pid in picked
        entry = {"enabled": enabled, "api_key_env": env}
        if enabled:
            have = prev.get("token") or cfg.get("api_keys", {}).get(env) \
                   or os.environ.get(env)
            if not have:
                tok = _password(f"{env} (leave blank to set later):")
                if tok:
                    cfg.setdefault("api_keys", {})[env] = tok
        new_ch[pid] = entry
    cfg["channels"] = new_ch
    _write_config(cfg)
    enabled_names = [p for p in picked]
    if enabled_names:
        print(f"Channels enabled: {', '.join(enabled_names)}")
    else:
        print("No channels enabled.")
    print("[info] Channel runtime (bot loops, gateway) is not wired yet; "
          "config is stored so future runtime can read it.")
    return 0


def run_backend_section() -> int:
    """Where shell-style tools (bash, execute_code, ...) actually run.

    Currently OpenProgram only has the 'local' in-process path. Wizard
    surfaces the full set so users can record intent; docker / ssh
    execution backends are separate runtime work.
    """
    cfg = _read_config()
    be = cfg.get("backend", {}) or {}
    cur_terminal = be.get("terminal") or "local"

    choices = ["local", "docker", "ssh"]
    picked = _choose_one("Terminal backend:", choices, cur_terminal)
    if picked is None:
        print("Cancelled.")
        return 1

    entry: dict[str, Any] = {"terminal": picked}
    if picked == "docker":
        image = _text("Container image:", default=be.get("docker_image", "ubuntu:24.04"))
        entry["docker_image"] = image or "ubuntu:24.04"
    elif picked == "ssh":
        host = _text("SSH host (user@host):", default=be.get("ssh_target", ""))
        entry["ssh_target"] = host or ""
    cfg["backend"] = entry
    _write_config(cfg)
    print(f"Terminal backend: {picked}")
    if picked != "local":
        print("[info] Only the 'local' backend is currently implemented at "
              "runtime. Your selection is stored for when other backends land.")
    return 0


# --- Orchestrator -----------------------------------------------------------

# Sections ordered so "required for the minimum useful install" runs
# first; anything that only captures intent (for future runtime work)
# is gated behind a confirm so first-run users aren't forced through it.
_CORE_SECTIONS = [
    ("providers", "Connect LLM provider(s)",        run_providers_section, False),
    ("model",     "Pick your default chat model",   run_model_section,     False),
    ("tools",     "Enable/disable tools",           run_tools_section,     True),
    ("agent",     "Default thinking effort",        run_agent_section,     False),
    ("skills",    "Enable/disable skills",          run_skills_section,    True),
    ("ui",        "Web UI port + auto-open",        run_ui_section,        True),
    ("memory",    "Memory backend",                 run_memory_section,    True),
]

_EXTRA_SECTIONS = [
    ("profile",   "Active profile name",            run_profile_section,   True),
    ("tts",       "Text-to-speech (stored for future)",       run_tts_section,      True),
    ("channels",  "Chat-channel bots (stored for future)",    run_channels_section, True),
    ("backend",   "Terminal exec backend (stored for future)", run_backend_section, True),
]


def run_full_setup() -> int:
    """Walk through every setup section.

    Sections split into two groups:
      * _CORE_SECTIONS — things the runtime actually consumes today
      * _EXTRA_SECTIONS — config stored for features whose runtime is
        pending (TTS, channels, docker/ssh backend, profiles). Gated
        behind a single "Configure advanced sections?" confirm so the
        first-run path stays short.

    Any section flagged ``skippable`` is wrapped with a per-section
    confirm so the user can breeze through the defaults.
    """
    print("=" * 60)
    print("  OpenProgram setup")
    print("=" * 60)
    print()
    print("We'll walk through the config sections. You can rerun any of")
    print("them individually with `openprogram config <name>`.")
    print()
    if not _confirm("Start?", default=True):
        return 0

    total = len(_CORE_SECTIONS)
    for i, (name, desc, fn, skippable) in enumerate(_CORE_SECTIONS, 1):
        print(f"\n--- {i}/{total}: {name} — {desc} ---")
        if skippable and not _confirm(f"Configure {name} now?", default=False):
            print(f"Skipped {name}.")
            continue
        rc = fn()
        if rc != 0:
            print(f"[warn] {name} exited with status {rc}; continuing.")

    print()
    if not _confirm(
        "Also configure advanced sections "
        "(profile / tts / channels / backend)?", default=False
    ):
        print("\nSetup complete. Run `openprogram` to start chatting.")
        return 0

    extra_total = len(_EXTRA_SECTIONS)
    for i, (name, desc, fn, skippable) in enumerate(_EXTRA_SECTIONS, 1):
        print(f"\n--- extra {i}/{extra_total}: {name} — {desc} ---")
        if skippable and not _confirm(f"Configure {name} now?", default=False):
            print(f"Skipped {name}.")
            continue
        rc = fn()
        if rc != 0:
            print(f"[warn] {name} exited with status {rc}; continuing.")

    print("\nSetup complete. Run `openprogram` to start chatting.")
    return 0
