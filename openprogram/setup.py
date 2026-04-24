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

    Also honours ``memory.backend == "none"`` by hiding the ``memory``
    tool, since it has no backing store in that mode.
    """
    cfg = _read_config()
    disabled = set(cfg.get("tools", {}).get("disabled", []) or [])
    if (cfg.get("memory", {}) or {}).get("backend") == "none":
        disabled.add("memory")
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


def read_ui_prefs() -> dict[str, Any]:
    cfg = _read_config()
    ui = cfg.get("ui", {}) or {}
    return {
        "port": int(ui.get("port") or 8765),
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

def run_providers_section() -> int:
    """Provider setup — always interactive. Imports Claude Code /
    Codex / Gemini / GH CLI logins, adds API-key pasted entries, or
    launches OAuth flows. Same flow in both modes — QuickStart doesn't
    skip this because at least one provider is required.

    We call ``run_interactive_setup`` directly (not _cmd_setup) so
    KeyboardInterrupt propagates up to ``run_full_setup``'s
    top-level try/except and cancels the whole wizard instead of
    being converted to return-code 130 by _cmd_setup's wrapper.
    """
    from openprogram.auth.interactive import run_interactive_setup
    return run_interactive_setup()


def _ensure_default_agent():
    """Return the default agent, creating an empty ``main`` if none
    exists. Setup sections mutate this agent's spec as the user
    picks model / effort / etc.
    """
    from openprogram.agents import manager as _agents
    spec = _agents.get_default()
    if spec is not None:
        return spec
    return _agents.create("main", name="Main", make_default=True)


def run_model_section() -> int:
    """Pick the default agent's chat model across enabled providers."""
    from openprogram.webui import _model_catalog as mc
    from openprogram.agents import manager as _agents
    from openprogram.agents import runtime_registry as _runtimes
    enabled = mc.list_enabled_models()
    if not enabled:
        print("No enabled models yet. Enable a provider in "
              "`openprogram providers setup`, then rerun "
              "`openprogram config model`.")
        return 1

    agent = _ensure_default_agent()
    labels = [f"{m['provider']}/{m['id']}  ({m.get('name', m['id'])})"
              for m in enabled]
    values = [f"{m['provider']}/{m['id']}" for m in enabled]
    label_to_value = dict(zip(labels, values))

    current_label = None
    if agent.model.provider and agent.model.id:
        target = f"{agent.model.provider}/{agent.model.id}"
        for lbl, val in label_to_value.items():
            if val == target:
                current_label = lbl
                break

    picked = _choose_one(
        f"Default chat model for agent `{agent.id}`:",
        labels, current_label,
    )
    if picked is None:
        print("Cancelled.")
        return 1
    provider, model = label_to_value[picked].split("/", 1)
    _agents.update(agent.id, {"model": {"provider": provider, "id": model}})
    # Drop any cached runtime so the new model takes effect next turn.
    _runtimes.invalidate(agent.id)
    print(f"Agent {agent.id}: default model set to {provider}/{model}")
    return 0


def run_tools_section() -> int:
    """Pick which tools the default agent can use. Advanced-only —
    QuickStart leaves the default (all enabled)."""
    from openprogram.tools import ALL_TOOLS
    from openprogram.agents import manager as _agents
    agent = _ensure_default_agent()
    disabled = set((agent.tools or {}).get("disabled") or [])
    names = sorted(ALL_TOOLS.keys())
    items = [(n, n not in disabled) for n in names]

    picked = _checkbox(f"Tools for agent `{agent.id}`:", items)
    if picked is None:
        print("Cancelled.")
        return 1
    new_disabled = sorted(set(names) - set(picked))
    _agents.update(agent.id, {"tools": {"disabled": new_disabled}})
    print(f"Enabled: {len(picked)} / {len(names)} tools")
    if new_disabled:
        print(f"Disabled: {', '.join(new_disabled)}")
    return 0


def run_agent_section() -> int:
    """Default reasoning effort for the default agent."""
    from openprogram.agents import manager as _agents
    from openprogram.agents import runtime_registry as _runtimes
    agent = _ensure_default_agent()
    current = agent.thinking_effort or "medium"

    levels = ["low", "medium", "high", "xhigh"]
    picked = _choose_one(
        f"Reasoning effort for agent `{agent.id}`:", levels, current,
    )
    if picked is None:
        print("Cancelled.")
        return 1
    _agents.update(agent.id, {"thinking_effort": picked})
    _runtimes.invalidate(agent.id)
    print(f"Agent {agent.id}: reasoning effort = {picked}")
    return 0


# --- Phase 2 sections: skills, ui, memory -----------------------------------

def run_skills_section() -> int:
    """Pick which skills (SKILL.md entries) are enabled. Advanced-only
    — QuickStart leaves all discovered skills enabled."""
    try:
        from openprogram.agentic_programming import (
            default_skill_dirs, load_skills,
        )
        skills = load_skills(default_skill_dirs())
    except Exception as e:
        print(f"Failed to scan skills: {e}")
        return 1
    if not skills:
        print("Skills: no skills discovered.")
        return 0

    from openprogram.agents import manager as _agents
    agent = _ensure_default_agent()
    disabled = set((agent.skills or {}).get("disabled") or [])
    names = sorted(s.name for s in skills)
    items = [(n, n not in disabled) for n in names]

    picked = _checkbox(f"Skills for agent `{agent.id}`:", items)
    if picked is None:
        print("Cancelled.")
        return 1
    new_disabled = sorted(set(names) - set(picked))
    _agents.update(agent.id, {"skills": {"disabled": new_disabled}})
    print(f"Enabled: {len(picked)} / {len(names)} skills")
    if new_disabled:
        print(f"Disabled: {', '.join(new_disabled)}")
    return 0


def run_ui_section() -> int:
    """Web UI preferences: port + auto-open browser. Advanced-only —
    QuickStart uses the default port 8765 with auto-open."""
    cfg = _read_config()
    ui = cfg.get("ui", {}) or {}
    cur_port = int(ui.get("port") or 8765)
    cur_open = bool(ui.get("open_browser", True))

    port_raw = _text("Web UI port:", default=str(cur_port))
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
    under ~/.agentic/memory). Advanced-only — QuickStart uses ``local``
    (the only real backend).
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
    """Named profile (active config slot). Advanced-only — QuickStart
    uses ``default`` which is what nearly everyone wants.

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
    """Text-to-speech backend + credentials. Advanced-only — QuickStart
    leaves TTS off.

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


_CHANNEL_LABELS = {
    "telegram": "Telegram",
    "discord":  "Discord",
    "slack":    "Slack (Socket Mode)",
    "wechat":   "WeChat (personal, QR login)",
}


def _add_telegram_account(account_id: str) -> None:
    from openprogram.channels import accounts as _accts
    if _accts.get("telegram", account_id) is None:
        _accts.create("telegram", account_id)
    tok = _password(f"Telegram bot token for account `{account_id}`:")
    if tok:
        _accts.update_credentials("telegram", account_id, {"bot_token": tok})


def _add_discord_account(account_id: str) -> None:
    from openprogram.channels import accounts as _accts
    if _accts.get("discord", account_id) is None:
        _accts.create("discord", account_id)
    tok = _password(f"Discord bot token for account `{account_id}`:")
    if tok:
        _accts.update_credentials("discord", account_id, {"bot_token": tok})


def _add_slack_account(account_id: str) -> None:
    from openprogram.channels import accounts as _accts
    if _accts.get("slack", account_id) is None:
        _accts.create("slack", account_id)
    bot = _password(f"Slack bot token (xoxb-...) for `{account_id}`:")
    app = _password(f"Slack app-level token (xapp-...) for `{account_id}`:")
    patch: dict[str, Any] = {}
    if bot:
        patch["bot_token"] = bot
    if app:
        patch["app_token"] = app
    if patch:
        _accts.update_credentials("slack", account_id, patch)


def _add_wechat_account(account_id: str) -> None:
    from openprogram.channels import accounts as _accts
    from openprogram.channels.wechat import login_account
    if _accts.get("wechat", account_id) is None:
        _accts.create("wechat", account_id)
    print(f"[wechat] logging in account `{account_id}` — scan the QR "
          f"with your phone")
    login_account(account_id)


_NEW_ACCOUNT_FN = {
    "telegram": _add_telegram_account,
    "discord": _add_discord_account,
    "slack": _add_slack_account,
    "wechat": _add_wechat_account,
}


def _ask_channel() -> str | None:
    labels = [_CHANNEL_LABELS[k] for k in ("telegram", "discord",
                                            "slack", "wechat")]
    keys = ["telegram", "discord", "slack", "wechat"]
    picked = _choose_one("Pick a channel:", labels, labels[0])
    if picked is None:
        return None
    return keys[labels.index(picked)]


def _manage_channel_account(channel: str, account_id: str) -> None:
    """Top-level action menu for an existing channel account."""
    from openprogram.channels import accounts as _accts
    from openprogram.channels import bindings as _bindings
    configured = _accts.is_configured(channel, account_id)
    enabled = _accts.is_enabled(channel, account_id)
    label = (f"{_CHANNEL_LABELS.get(channel, channel)}:{account_id} "
             f"({'enabled' if enabled else 'disabled'}"
             f", {'configured' if configured else 'needs credentials'})")
    options = [
        "Re-enter credentials",
        "Disable" if enabled else "Enable",
        "Delete this account (credentials + bindings)",
        "Back",
    ]
    pick = _choose_one(label, options, options[-1])
    if pick in (None, "Back"):
        return
    if pick == "Re-enter credentials":
        fn = _NEW_ACCOUNT_FN.get(channel)
        if fn is not None:
            fn(account_id)
        return
    if pick == "Disable":
        _accts.set_enabled(channel, account_id, False)
        print(f"{channel}:{account_id} disabled")
        return
    if pick == "Enable":
        _accts.set_enabled(channel, account_id, True)
        print(f"{channel}:{account_id} enabled")
        return
    if pick.startswith("Delete"):
        confirm = _choose_one(
            f"Delete {channel}:{account_id} and its bindings?",
            ["Keep", "Delete"], "Keep",
        )
        if confirm == "Delete":
            _bindings.remove_for_account(channel, account_id)
            _accts.delete(channel, account_id)
            print(f"{channel}:{account_id} removed")


def run_channels_section() -> int:
    """List every channel account and let the user add / edit one.

    Account-oriented: each row is a ``(channel, account_id)`` pair.
    Multiple accounts per channel work out of the box (e.g. two
    WeChat bot logins each bound to different agents).
    """
    from openprogram.channels import accounts as _accts
    while True:
        rows = _accts.list_all_accounts()
        options: list[str] = []
        mapping: list[tuple[str, str]] = []
        for acct in rows:
            enabled = _accts.is_enabled(acct.channel, acct.account_id)
            configured = _accts.is_configured(acct.channel, acct.account_id)
            tags = []
            if enabled:
                tags.append("enabled")
            else:
                tags.append("disabled")
            tags.append("configured" if configured else "needs credentials")
            options.append(
                f"{_CHANNEL_LABELS.get(acct.channel, acct.channel)}:"
                f"{acct.account_id}  ({', '.join(tags)})"
            )
            mapping.append((acct.channel, acct.account_id))
        options.append("+ Add a channel account")
        mapping.append(("__add__", ""))
        options.append("Finished")
        mapping.append(("__done__", ""))

        picked = _choose_one("Channel accounts:", options, options[-1])
        if picked is None:
            return 0
        channel, account_id = mapping[options.index(picked)]
        if channel == "__done__":
            return 0
        if channel == "__add__":
            new_channel = _ask_channel()
            if new_channel is None:
                continue
            new_id = _text(
                "Account id (letters/numbers/-_, e.g. personal, work):",
                default="default",
            )
            if not new_id:
                continue
            try:
                _accts.create(new_channel, new_id)
            except ValueError as e:  # already exists / bad id
                print(f"[warn] {e}")
                continue
            fn = _NEW_ACCOUNT_FN.get(new_channel)
            if fn is not None:
                fn(new_id)
            print(f"{new_channel}:{new_id} saved")
            continue
        _manage_channel_account(channel, account_id)


def run_backend_section() -> int:
    """Where shell-style tools (bash, execute_code, ...) actually run.
    Advanced-only — QuickStart uses `local`, which is the only real
    runtime backend at the moment.

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

# Section spec: (key, title, description, fn)
#
# QuickStart = the things a user MUST answer to have a working chat:
#   provider login, default model, reasoning effort.
# Advanced   = detail knobs with sane defaults, plus channel bots which
#   are an opt-in "let external users talk to my agent" feature — not
#   part of getting chat working at all.
#
# The runtime reads each advanced knob with a fallback default
# (ui.port=8765, memory.backend=local, tools.disabled=[], etc.), so
# QuickStart skipping them writes the same effective state as
# explicitly accepting the defaults.
_QUICKSTART_SECTIONS = [
    ("providers", "Connect LLM provider(s)",
     "Import existing CLI logins (Claude Code / Codex / Gemini / GH CLI), "
     "or add API keys. At least one provider is required.",
     run_providers_section),
    ("model", "Pick your default chat model",
     "Choose which enabled model starts every new conversation.",
     run_model_section),
    ("agent", "Default reasoning effort",
     "How hard should the model think by default? "
     "low = fastest, xhigh = deepest.",
     run_agent_section),
]

_ADVANCED_EXTRA_SECTIONS = [
    ("tools", "Enable / disable tools",
     "Which of the built-in tools should the agent have access to. "
     "QuickStart enables everything.",
     run_tools_section),
    ("skills", "Enable / disable skills",
     "SKILL.md instruction packs the agent can load on demand. "
     "QuickStart enables everything discovered.",
     run_skills_section),
    ("channels", "Chat-channel bots (optional)",
     "Let Telegram / Discord / Slack / WeChat users talk to your agent. "
     "Leave for later if you only want local chat.",
     run_channels_section),
    ("tts", "Text-to-speech",
     "Spoken replies in CLI chat. Providers: openai / elevenlabs / "
     "edge-tts (free). QuickStart leaves TTS off.",
     run_tts_section),
    ("ui", "Web UI preferences",
     "Port and auto-open-browser for `openprogram web`. "
     "QuickStart uses 8765 with auto-open.",
     run_ui_section),
    ("memory", "Memory backend",
     "Local JSON store or 'none' (disables the memory tool). "
     "QuickStart uses local.",
     run_memory_section),
    ("profile", "Named profile",
     "Stored profile name. Per-profile state-dir isolation is done via "
     "`--profile <name>` at launch. QuickStart uses 'default'.",
     run_profile_section),
    ("backend", "Terminal exec backend",
     "Where the `bash` / `execute_code` / `process` tools actually "
     "run: local / ssh / docker. QuickStart uses local.",
     run_backend_section),
]


def _section_header(idx: int, total: int, title: str, desc: str) -> None:
    """Rich-aware section header; falls back to plain text."""
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text
        console = Console()
        console.print()
        header = Text(f"Step {idx}/{total}  ", style="bold bright_blue")
        header.append(title, style="bold")
        body = Text(desc, style="dim")
        console.print(Panel(body, title=header, border_style="bright_blue",
                            padding=(0, 1)))
    except ImportError:
        print()
        print(f"--- Step {idx}/{total}: {title} ---")
        print(f"    {desc}")


def _print_intro() -> None:
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text
        console = Console()
        body = Text()
        body.append("Welcome to OpenProgram.\n\n", style="bold bright_blue")
        body.append(
            "  ▸ QuickStart — provider login, default model, reasoning "
            "effort, optional chat channels\n"
            "  ▸ Advanced   — everything in QuickStart, plus tool toggles, "
            "skills, TTS, Web UI port, memory backend, profile, terminal "
            "backend\n\n",
            style="dim",
        )
        body.append(
            "All prompts use arrow keys + Enter. Ctrl+C exits; partial "
            "progress is saved. Rerun any section alone with "
            "`openprogram config <name>`.",
            style="dim italic",
        )
        console.print()
        console.print(Panel(body, title=Text("OpenProgram setup",
                                             style="bold bright_blue"),
                            border_style="bright_blue", padding=(1, 2)))
    except ImportError:
        print()
        print("=" * 60)
        print("  OpenProgram setup")
        print("=" * 60)
        print("  QuickStart — provider/model/effort/channels")
        print("  Advanced   — + tools/skills/tts/ui/memory/profile/backend")
        print("All prompts use arrow keys + Enter.")
        print("Ctrl+C to exit; partial progress is saved.")
        print()


def _print_summary() -> None:
    """Recap the stored config at the end of the wizard."""
    cfg = _read_config()
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        tbl = Table.grid(padding=(0, 2))
        tbl.add_column(style="bold")
        tbl.add_column()
        tbl.add_row("default model:",
                    f"{cfg.get('default_provider', '?')}/{cfg.get('default_model', '?')}")
        tbl.add_row("thinking effort:",
                    str((cfg.get("agent", {}) or {}).get("thinking_effort", "medium")))
        tools_disabled = (cfg.get("tools", {}) or {}).get("disabled", []) or []
        tbl.add_row("disabled tools:",
                    ", ".join(tools_disabled) if tools_disabled else "(none)")
        channels = cfg.get("channels", {}) or {}
        enabled_ch = [k for k, v in channels.items() if isinstance(v, dict) and v.get("enabled")]
        tbl.add_row("channels:",
                    ", ".join(enabled_ch) if enabled_ch else "(none)")
        tts = (cfg.get("tts") or {}).get("provider") or "none"
        tbl.add_row("tts:", tts)
        profile = cfg.get("profile", "default")
        tbl.add_row("profile:", profile)
        console.print()
        console.print("[bold green]Setup complete.[/]")
        console.print(tbl)
    except ImportError:
        print("\nSetup complete.")
        print(f"  default model:    {cfg.get('default_provider')}/{cfg.get('default_model')}")
        print(f"  thinking effort:  {(cfg.get('agent') or {}).get('thinking_effort', 'medium')}")


def _mode_select() -> str | None:
    """QuickStart: the essentials the user has to do (provider login,
    pick default model, reasoning effort, optional chat channels).
    Advanced: same plus the tuning knobs (tools, skills, TTS, Web UI
    port, memory backend, profile, terminal backend)."""
    options = [
        "QuickStart   — provider / model / effort / channels",
        "Advanced     — QuickStart + tools / skills / tts / ui / memory / profile / backend",
    ]
    picked = _choose_one("Setup mode:", options, options[0])
    if picked is None:
        return None
    return "quickstart" if picked.startswith("QuickStart") else "advanced"


def _pick_next_action() -> str:
    """After setup finishes, prompt for the next action.

    Returns one of: "chat" (open the terminal REPL), "web" (start
    the Web UI), or "later" (do nothing — user runs a command later).
    """
    options = [
        "Chat in terminal (recommended)",
        "Open the Web UI",
        "Do this later",
    ]
    picked = _choose_one("How do you want to start?", options, options[0])
    if picked is None or picked == "Do this later":
        return "later"
    if picked == "Open the Web UI":
        return "web"
    return "chat"


def run_full_setup() -> int:
    """Linear onboarding.

    QuickStart walks _QUICKSTART_SECTIONS — the essentials the user
    has to participate in. Advanced walks the same list plus
    _ADVANCED_EXTRA_SECTIONS — the detail knobs with sane defaults.

    Structure: intro → mode select → sections → summary → next-action
    select (chat / web / later). No extra "Start?" confirm — running
    `openprogram setup` is the start.
    """
    try:
        _print_intro()
        mode = _mode_select()
        if mode is None:
            _print_cancelled()
            return 0
        return _run_setup_inner(mode)
    except KeyboardInterrupt:
        _print_cancelled()
        return 130


def _print_cancelled() -> None:
    try:
        from rich.console import Console
        Console().print("\n[yellow]Cancelled. Partial progress is saved — "
                        "run `openprogram setup` again to pick up.[/]")
    except ImportError:
        print("\nCancelled. Partial progress is saved — run "
              "`openprogram setup` again to pick up.")


def _run_setup_inner(mode: str) -> int:
    """Both QuickStart and Advanced walk _QUICKSTART_SECTIONS — those
    are things the user must participate in (provider login, pick
    default model, reasoning effort, chat channels). Advanced then
    additionally walks _ADVANCED_EXTRA_SECTIONS — detail knobs with
    sane defaults (UI port, tool toggles, TTS, memory backend, etc.)
    that QuickStart silently leaves on the runtime fallbacks.
    """
    sections = list(_QUICKSTART_SECTIONS)
    if mode == "advanced":
        sections += list(_ADVANCED_EXTRA_SECTIONS)
    total = len(sections)

    for i, (name, title, desc, fn) in enumerate(sections, 1):
        _section_header(i, total, title, desc)
        rc = fn()
        if rc != 0:
            print(f"[warn] {name} exited with status {rc}; continuing.")

    _print_summary()

    next_action = _pick_next_action()
    if next_action == "chat":
        try:
            from openprogram.cli_chat import run_cli_chat
            run_cli_chat()
        except Exception as e:  # noqa: BLE001
            print(f"[setup] couldn't launch chat: {type(e).__name__}: {e}")
            print("Run `openprogram` manually.")
    elif next_action == "web":
        try:
            from openprogram.cli import _cmd_web
            _cmd_web(None, None)
        except Exception as e:  # noqa: BLE001
            print(f"[setup] couldn't launch web UI: {type(e).__name__}: {e}")
            print("Run `openprogram web` manually.")
    else:
        print("\nRun `openprogram` when ready.")
    return 0


# --- Configure command (section-menu loop, distinct from linear setup) -----

def run_configure_menu() -> int:
    """OpenClaw-style configure loop: pick a section, come back, pick
    again, until 'Continue'. Distinct from ``run_full_setup`` which is
    a linear first-run walk.

    Ctrl-C at any point exits the whole menu cleanly — no traceback,
    no bouncing back to the section picker.
    """
    all_sections = list(_QUICKSTART_SECTIONS) + list(_ADVANCED_EXTRA_SECTIONS)
    section_map = {s[0]: s for s in all_sections}

    try:
        while True:
            labels = []
            values = []
            for key, title, _desc, _fn in all_sections:
                labels.append(f"{title}")
                values.append(key)
            labels.append("Continue (done)")
            values.append("__done__")

            picked = _choose_one("Select a section to configure:", labels,
                                 labels[-1])
            if picked is None:
                return 0
            key = values[labels.index(picked)]
            if key == "__done__":
                return 0
            _, _, desc, fn = section_map[key]
            print()
            print(desc)
            rc = fn()
            if rc != 0:
                print(f"[warn] {key} exited with status {rc}.")
    except KeyboardInterrupt:
        _print_cancelled()
        return 130
