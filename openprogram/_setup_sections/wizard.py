"""Setup wizard orchestrator: intro / mode select / linear walk / summary
/ next-action picker. Plus the OpenClaw-style ``run_configure_menu`` loop.

Section lists are defined here because both ``run_full_setup`` (linear
walk) and ``run_configure_menu`` (pick-loop) need the same metadata.
"""
from __future__ import annotations

from openprogram._setup_sections.sections import (
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
from openprogram._setup_sections.channels import run_channels_section
from openprogram._setup_sections.backend import run_backend_section


# QuickStart = the things a user MUST answer to have a working chat:
#   provider login, default model, reasoning effort.
# Advanced   = detail knobs with sane defaults, plus channel bots which
#   are an opt-in "let external users talk to my agent" feature.
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
    ("search", "Web search backend",
     "Default backend for the `web_search` tool. 'auto' picks the "
     "highest-priority configured provider; pin one (Tavily / Brave / "
     "Google PSE / …) to force it.",
     run_search_section),
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
    from openprogram.setup import _read_config
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
    from openprogram.setup import _choose_one
    options = [
        "QuickStart   — provider / model / effort / channels",
        "Advanced     — QuickStart + tools / skills / tts / ui / memory / profile / backend",
    ]
    picked = _choose_one("Setup mode:", options, options[0])
    if picked is None:
        return None
    return "quickstart" if picked.startswith("QuickStart") else "advanced"


def _pick_next_action() -> str:
    """Returns: 'chat' | 'web' | 'later'."""
    from openprogram.setup import _choose_one
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


def _print_cancelled() -> None:
    try:
        from rich.console import Console
        Console().print("\n[yellow]Cancelled. Partial progress is saved — "
                        "run `openprogram setup` again to pick up.[/]")
    except ImportError:
        print("\nCancelled. Partial progress is saved — run "
              "`openprogram setup` again to pick up.")


def _run_setup_inner(mode: str) -> int:
    """Both QuickStart and Advanced walk _QUICKSTART_SECTIONS; Advanced
    then additionally walks _ADVANCED_EXTRA_SECTIONS.
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


def run_full_setup() -> int:
    """Linear onboarding: intro → mode select → sections → summary → next-action."""
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


def run_configure_menu() -> int:
    """OpenClaw-style pick-loop: pick a section, run it, repeat until 'Continue'."""
    from openprogram.setup import _choose_one
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
