"""Per-section runners: providers / model / tools / agent / skills / ui /
memory / profile / tts. Each ``run_*_section`` returns 0 on success, 1
on user-cancel or failure.

Prompts (``_choose_one``, ``_checkbox``, ``_text``, ``_confirm``,
``_password``) and config storage (``_read_config``, ``_write_config``)
come from ``openprogram.setup``.
"""
from __future__ import annotations

import os
from typing import Any


def _ensure_default_agent():
    """Return the default agent, creating an empty ``main`` if none exists."""
    from openprogram.agents import manager as _agents
    spec = _agents.get_default()
    if spec is not None:
        return spec
    return _agents.create("main", name="Main", make_default=True)


def run_providers_section() -> int:
    """Provider setup — always interactive. Imports CLI logins, OAuth, or
    pasted API keys. QuickStart can't skip — at least one provider required.
    """
    from openprogram.auth.interactive import run_interactive_setup
    return run_interactive_setup()


def run_model_section() -> int:
    """Pick the default agent's chat model across enabled providers."""
    from openprogram.setup import _choose_one
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
    _runtimes.invalidate(agent.id)
    print(f"Agent {agent.id}: default model set to {provider}/{model}")
    return 0


def run_tools_section() -> int:
    """Pick which tools the default agent can use."""
    from openprogram.setup import _checkbox
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
    from openprogram.setup import _choose_one
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


def run_skills_section() -> int:
    """Pick which skills (SKILL.md entries) are enabled."""
    from openprogram.setup import _checkbox
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
    """Web UI preferences: port + auto-open browser."""
    from openprogram.setup import _confirm, _read_config, _text, _write_config
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
    """Memory backend for the ``memory`` tool. local | none."""
    from openprogram.setup import _choose_one, _read_config, _write_config
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
        print("(The memory tools (memory_note / memory_recall / memory_reflect / "
              "memory_get) will no-op until a backend is selected.)")
    return 0


def run_profile_section() -> int:
    """Named profile (active config slot). Only persists the name; per-
    profile isolation lives in the ``--profile`` launch flag."""
    from openprogram.setup import _read_config, _text, _write_config
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
    """Text-to-speech backend + credentials."""
    from openprogram.setup import _choose_one, _password, _read_config, _write_config
    cfg = _read_config()
    tts = cfg.get("tts", {}) or {}
    cur_prov = tts.get("provider") or "none"

    providers = [
        "none",
        "openai",
        "elevenlabs",
        "edge-tts",
        "playht",
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
