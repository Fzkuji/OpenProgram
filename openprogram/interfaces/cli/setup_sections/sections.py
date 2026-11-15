"""Per-section runners: providers / model / tools / agent / skills / ui /
memory / profile / tts. Each ``run_*_section`` returns 0 on success, 1
on user-cancel or failure.

Prompts (``_choose_one``, ``_checkbox``, ``_text``, ``_confirm``,
``_password``) come from ``prompts``; config storage comes from
``openprogram.state.config`` and ``openprogram.state.preferences``.
"""
from __future__ import annotations

import os
from typing import Any


def _ensure_default_agent():
    """Return the default agent, creating an empty ``main`` if none exists."""
    from openprogram.runtime.agent_registry.builtin_agents import ensure_default_agent
    return ensure_default_agent()


def run_providers_section() -> int:
    """Provider setup — always interactive. Imports CLI logins, OAuth, or
    pasted API keys. QuickStart can't skip — at least one provider required.
    """
    from openprogram.interfaces.cli.commands.providers.interactive import run_interactive_setup
    return run_interactive_setup()


def run_model_section() -> int:
    """Pick the model new chats start on; recorded as the last used model."""
    from openprogram.interfaces.cli.setup_sections.prompts import _choose_one
    from openprogram.models import listing as mc
    from openprogram.models.providers.catalog.last_used import read_last_used_model, record_last_used_model
    enabled = mc.list_enabled_models()
    if not enabled:
        print("No enabled models yet. Enable a provider in "
              "`openprogram providers setup`, then rerun "
              "`openprogram config model`.")
        return 1

    labels = [f"{m['provider']}/{m['id']}  ({m.get('name', m['id'])})"
              for m in enabled]
    values = [f"{m['provider']}/{m['id']}" for m in enabled]
    label_to_value = dict(zip(labels, values))

    current_label = None
    last = read_last_used_model()
    if last:
        target = f"{last[0]}/{last[1]}"
        for lbl, val in label_to_value.items():
            if val == target:
                current_label = lbl
                break

    picked = _choose_one("Model for new chats:", labels, current_label)
    if picked is None:
        print("Cancelled.")
        return 1
    provider, model = label_to_value[picked].split("/", 1)
    record_last_used_model(provider, model)
    print(f"New chats start on {provider}/{model} until you pick another model.")
    return 0


def run_tools_section() -> int:
    """Pick which tools the default agent can use."""
    from openprogram.interfaces.cli.setup_sections.prompts import _checkbox
    from openprogram.programs import list_registered_agent_tools
    from openprogram.runtime.agent_registry import manager as _agents
    agent = _ensure_default_agent()
    disabled = set((agent.tools or {}).get("disabled") or [])
    names = sorted(list_registered_agent_tools())
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
    from openprogram.interfaces.cli.setup_sections.prompts import _choose_one
    from openprogram.runtime.agent_registry import manager as _agents
    from openprogram.runtime.agent_registry import runtime_registry as _runtimes
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
    from openprogram.interfaces.cli.setup_sections.prompts import _checkbox
    try:
        from openprogram.extensions.skills import list_skills
        skills = list_skills()
    except Exception as e:
        print(f"Failed to scan skills: {e}")
        return 1
    if not skills:
        print("Skills: no skills discovered.")
        return 0

    from openprogram.runtime.agent_registry import manager as _agents
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
    """Web UI settings — rendered from the config schema's Ports group, so
    this wizard, the TUI panel, and ``openprogram config`` never drift."""
    from openprogram.interfaces.cli.setup_sections.prompts import prompt_schema_group
    from openprogram.state.preferences import read_ui_prefs
    rc = prompt_schema_group("Ports")
    if rc != 0:
        return rc
    p = read_ui_prefs()
    print(f"UI: web_port={p['web_port']}, open_browser={p['open_browser']}")
    print("Takes effect on the next `openprogram web` / `openprogram worker` start.")
    return 0


def run_memory_section() -> int:
    """Memory backend for the ``memory`` tool. local | none."""
    from openprogram.interfaces.cli.setup_sections.prompts import _choose_one
    from openprogram.state.config import read_config_with_revision, update_config
    cfg, revision = read_config_with_revision()
    cur = (cfg.get("memory", {}) or {}).get("backend") or "local"
    choices = ["local", "none"]
    picked = _choose_one("Memory backend:", choices, cur)
    if picked is None:
        print("Cancelled.")
        return 1
    update_config(
        lambda current: current.setdefault("memory", {}).__setitem__("backend", picked),
        expected_revision=revision,
    )
    print(f"Memory backend: {picked}")
    if picked == "none":
        from openprogram.programs.tools.knowledge.memory import MEMORY_TOOL_NAMES
        print(f"({' / '.join(MEMORY_TOOL_NAMES)} are hidden from the model "
              "until a backend is selected.)")
    return 0


def run_profile_section() -> int:
    """Named profile (active config slot). Only persists the name; per-
    profile isolation lives in the ``--profile`` launch flag."""
    from openprogram.interfaces.cli.setup_sections.prompts import _text
    from openprogram.state.config import read_config_with_revision, update_config
    cfg, revision = read_config_with_revision()
    cur = cfg.get("profile", "default") or "default"
    name = _text("Active profile name:", default=cur)
    if not name:
        print("Cancelled.")
        return 1
    update_config(
        lambda current: current.__setitem__("profile", name),
        expected_revision=revision,
    )
    print(f"Active profile: {name}")
    print("[info] Per-profile config isolation is not wired yet — only "
          "the active-profile name is persisted.")
    return 0


def run_search_section() -> int:
    """Pick the default web_search backend.

    "auto" means "use priority order, take the highest-priority available
    one at search time"; any explicit pick wins over priority if available.
    Each entry is annotated with its env var + configured/unconfigured
    status so users know which need an API key first.
    """
    from openprogram.interfaces.cli.setup_sections.prompts import _choose_one
    from openprogram.state.preferences import read_search_default_provider, write_search_default_provider
    # Import the registry the same way the tool does — populates the
    # builtin provider list as a side effect of importing `providers`.
    from openprogram.programs.tools.web.web_search.registry import registry as _wsr
    import openprogram.programs.tools.web.web_search.providers  # noqa: F401

    providers = list(_wsr.all())
    if not providers:
        print("No web_search providers registered.")
        return 1

    current = read_search_default_provider() or "auto"

    def _key_set(env_var: str | None) -> bool:
        if not env_var:
            return True
        if os.environ.get(env_var):
            return True
        try:
            from openprogram.state.config import read_config
            return bool((read_config().get("api_keys") or {}).get(env_var))
        except Exception:
            return False

    rows = ["auto  (highest-priority available)"]
    values = ["auto"]
    for p in providers:
        env_var = (list(getattr(p, "requires_env", ()) or []) or [None])[0]
        configured = _key_set(env_var)
        try:
            available = bool(p.is_available())
        except Exception:
            available = False
        if env_var:
            status = "available" if available else (
                "configured but inactive" if configured else f"needs {env_var}"
            )
        else:
            status = "no key needed"
        rows.append(f"{p.name}  ({status})")
        values.append(p.name)

    current_label = None
    for lbl, val in zip(rows, values):
        if val == current:
            current_label = lbl
            break

    picked = _choose_one("Default web search backend:", rows, current_label)
    if picked is None:
        print("Cancelled.")
        return 1
    name = values[rows.index(picked)]
    write_search_default_provider(None if name == "auto" else name)
    if name == "auto":
        print("Default search backend: auto (priority order)")
    else:
        print(f"Default search backend: {name}")

    # If the user picked a backend that isn't ready (its required env
    # var is missing or its is_available() returns False), surface the
    # catalog setup hint so the CLI flow tells them where to go next
    # instead of leaving them staring at "configured but inactive".
    # We don't try to handle key entry in the TUI — the webui Settings
    # page owns that. The TUI just hints the path forward.
    if name != "auto":
        picked_provider = next(
            (p for p in providers if p.name == name), None,
        )
        try:
            picked_available = bool(picked_provider and picked_provider.is_available())
        except Exception:
            picked_available = False
        if picked_provider and not picked_available:
            from openprogram.programs.tools.web.web_search import catalog as _wsc
            info = _wsc.get(name)
            if info and (info.signup_url or info.setup_steps):
                print()
                print(f"To enable {info.name}:")
                for i, step in enumerate(info.setup_steps or [], 1):
                    print(f"  {i}. {step}")
                if info.signup_url:
                    print(f"  Signup: {info.signup_url}")
                if info.docs_url:
                    print(f"  Docs:   {info.docs_url}")
                print(
                    "  Finish key entry in Settings → Web Search in the webui."
                )
    return 0


def run_tts_section() -> int:
    """Text-to-speech backend + credentials."""
    from openprogram.interfaces.cli.setup_sections.prompts import _choose_one, _password
    from openprogram.state.config import read_config_with_revision, update_config
    cfg, revision = read_config_with_revision()
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
    prompted_key: tuple[str, str] | None = None
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
                prompted_key = (entry["api_key_env"], key)

    def update(current: dict) -> None:
        if prompted_key is not None:
            current.setdefault("api_keys", {})[prompted_key[0]] = prompted_key[1]
        current["tts"] = entry

    update_config(update, expected_revision=revision)
    print(f"TTS: {picked}")
    if picked != "none":
        print("[info] Runtime hookup for spoken replies is not wired yet; "
              "the choice is stored for when it lands.")
    return 0
