"""Inventory helpers + Hermes-style welcome banner for the CLI chat REPL."""
from __future__ import annotations


def _tool_inventory() -> tuple[int, list[str]]:
    from openprogram.functions import list_available, list_registered_agent_tools
    names = list_available()  # only tools whose check_fn currently passes
    # Prefer the gated list; if the helper returns empty (no gating), fall
    # back to the full registry so the banner isn't misleadingly blank.
    if not names:
        names = list_registered_agent_tools()
    return len(names), names


def _skill_inventory() -> tuple[int, list[tuple[str, str]]]:
    """Return (count, [(name, description), ...]) for enabled skills.

    Respects ``skills.disabled`` in ``~/.openprogram/config.json``.
    """
    try:
        from openprogram.agentic_programming import (
            default_skill_dirs, load_skills,
        )
        from openprogram.setup import read_disabled_skills
        skills = load_skills(default_skill_dirs())
        disabled = read_disabled_skills()
        skills = [s for s in skills if s.name not in disabled]
    except Exception:
        return 0, []
    return len(skills), [(s.name, getattr(s, "description", "") or "") for s in skills]


def _function_inventory() -> tuple[int, list[str]]:
    """Return (count, [name, ...]) of agentic functions in functions/agentics/.

    Harness apps (the *-Agent-Harness symlinks) are reported separately
    by :func:`_application_inventory`.
    """
    import os
    import openprogram
    base = os.path.join(os.path.dirname(openprogram.__file__),
                        "functions", "agentics")
    if not os.path.isdir(base):
        return 0, []
    names: list[str] = []
    for entry in sorted(os.listdir(base)):
        if entry.startswith("_") or entry.startswith("."):
            continue
        full = os.path.join(base, entry)
        # Skip harness apps — those are listed under "applications".
        if entry.endswith("-Agent-Harness"):
            continue
        if os.path.isdir(full) and not entry.startswith("__"):
            names.append(entry)
        elif entry.endswith(".py") and entry != "__init__.py":
            names.append(entry[:-3])
    return len(names), names


def _application_inventory() -> tuple[int, list[str]]:
    """Return (count, [name, ...]) of harness apps in functions/agentics/.

    Harness apps are subdirectories whose name ends with
    ``-Agent-Harness`` (typically symlinks to external repos).
    """
    import os
    import openprogram
    base = os.path.join(os.path.dirname(openprogram.__file__),
                        "functions", "agentics")
    if not os.path.isdir(base):
        return 0, []
    names: list[str] = []
    for entry in sorted(os.listdir(base)):
        full = os.path.join(base, entry)
        if entry.startswith("_") or entry.startswith("."):
            continue
        if os.path.isdir(full) and entry.endswith("-Agent-Harness"):
            names.append(entry)
    return len(names), names


def _section_text(label: str, items: list[str], count: int, accent: str,
                  empty_msg: str = "none") -> "Text":  # noqa: F821
    from rich.text import Text
    t = Text()
    t.append(f"{label} ", style="bold")
    t.append(f"({count})\n", style="dim")
    if count == 0:
        t.append(empty_msg, style="dim italic")
        return t
    preview = items[:6]
    t.append(", ".join(preview), style=accent)
    if count > len(preview):
        t.append(f" (+{count - len(preview)} more)", style="dim")
    return t


def _print_banner(console, provider: str, model: str,
                  agent_id: str = "", session_id: str = "") -> None:
    """Welcome banner: session metadata on top, capability inventory below.

    Layout:

      ┌──── OpenProgram ────────────────────────┐
      │                                         │
      │    Model: openai-codex / gpt-5.5        │
      │    Agent: main                          │
      │  Session: local_xxx                     │
      │                                         │
      │  Tools (50)            Skills (1)       │
      │  tool_search, ...      agentic-programming
      │                                         │
      │  Functions (9)         Applications (0) │
      │  ask_user, ...         no applications  │
      │                                         │
      │  50 tools · 1 skills · 9 functions ·    │
      │  /help for commands                     │
      └─────────────────────────────────────────┘

    The detailed inventory rows are preserved (the user explicitly
    asked for them — they're how you discover what tools / skills /
    functions are loaded without leaving the prompt). The Model /
    Agent / Session header rows are added on top because the user
    needs to know which agent owns the session and which model
    backs it — that info used to only appear in the panel title and
    was easy to miss.
    """
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box

    # Strip the redundant ``provider:`` prefix from a model id like
    # ``openai-codex:gpt-5.5`` so the banner doesn't say
    # ``openai-codex / openai-codex:gpt-5.5``.
    pretty_model = model
    if isinstance(model, str) and ":" in model:
        head, _, tail = model.partition(":")
        if head == provider:
            pretty_model = tail

    tool_count, tool_names = _tool_inventory()
    skill_count, skill_items = _skill_inventory()
    fn_count, fn_names = _function_inventory()
    app_count, app_names = _application_inventory()

    # --- Header rows: Model / Agent / Session ---------------------
    header = Table.grid(padding=(0, 1))
    header.add_column(style="dim", justify="right")
    header.add_column()
    header.add_row("Model:", Text(f"{provider} / {pretty_model}", style="bold cyan"))
    if agent_id:
        header.add_row("Agent:", Text(agent_id, style="green"))
    if session_id:
        header.add_row("Session:", Text(session_id, style="dim"))

    # --- Inventory grid (Tools/Skills then Functions/Applications) ---
    grid = Table.grid(padding=(0, 2), expand=True)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)

    grid.add_row(
        _section_text("Tools", tool_names, tool_count, "cyan"),
        _section_text("Skills", [n for n, _ in skill_items], skill_count,
                      "magenta", empty_msg="no skills loaded"),
    )
    grid.add_row(Text(""), Text(""))  # spacer
    grid.add_row(
        _section_text("Functions", fn_names, fn_count, "green",
                      empty_msg="no functions registered"),
        _section_text("Applications", app_names, app_count, "yellow",
                      empty_msg="no applications registered"),
    )

    # --- Footer: totals + /help hint -----------------------------
    footer = Text()
    footer.append(f"{tool_count} tools", style="cyan")
    footer.append(" · ")
    footer.append(f"{skill_count} skills", style="magenta")
    footer.append(" · ")
    footer.append(f"{fn_count} functions", style="green")
    footer.append(" · ")
    footer.append(f"{app_count} apps", style="yellow")
    footer.append(" · /help for commands", style="dim")

    panel_body = Table.grid(padding=(1, 0))
    panel_body.add_row(header)
    panel_body.add_row(grid)
    panel_body.add_row(footer)

    console.print()
    console.print(Panel(
        panel_body,
        title=Text("OpenProgram", style="bold bright_blue"),
        border_style="bright_blue",
        box=box.ROUNDED,
        padding=(1, 2),
    ))
