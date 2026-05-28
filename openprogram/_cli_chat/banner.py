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

    Respects ``skills.disabled`` in ``~/.agentic/config.json``.
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
    """Concise welcome banner.

    Earlier versions splashed a two-row 18-line panel listing 50 tool
    names, 9 function names, 1 skill name, etc. — useful as an
    inventory dump, but TMI for a user who just wants to chat. The
    detailed inventory is still one slash-command away (``/tools``,
    ``/skills``, ``/functions``, ``/apps``); the banner now shows
    only what the user actually needs at the prompt: which model is
    active, which agent owns the session, and where to look for
    everything else.
    """
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box

    # The model id is sometimes returned with the provider prefix
    # already baked in (``openai-codex:gpt-5.5``). Strip it so the
    # banner doesn't render the provider twice.
    pretty_model = model
    if isinstance(model, str) and ":" in model:
        head, _, tail = model.partition(":")
        if head == provider:
            pretty_model = tail

    # Counts only — names are now available via slash commands.
    tool_count, _ = _tool_inventory()
    skill_count, _ = _skill_inventory()
    fn_count, _ = _function_inventory()
    app_count, _ = _application_inventory()

    body = Table.grid(padding=(0, 1))
    body.add_column(style="dim", justify="right")
    body.add_column()

    body.add_row("Model:", Text(f"{provider} / {pretty_model}", style="bold cyan"))
    if agent_id:
        body.add_row("Agent:", Text(agent_id, style="green"))
    if session_id:
        body.add_row("Session:", Text(session_id, style="dim"))

    inv = Text()
    inv.append(f"{tool_count}", style="cyan")
    inv.append(" tools  ", style="dim")
    inv.append(f"{skill_count}", style="magenta")
    inv.append(" skills  ", style="dim")
    inv.append(f"{fn_count}", style="green")
    inv.append(" functions", style="dim")
    if app_count:
        inv.append("  ")
        inv.append(f"{app_count}", style="yellow")
        inv.append(" apps", style="dim")
    body.add_row("Loaded:", inv)

    hint = Text()
    hint.append("Type your message — or ", style="dim")
    hint.append("/help", style="yellow bold")
    hint.append(" for commands.", style="dim")

    panel_body = Table.grid(padding=(1, 0))
    panel_body.add_row(body)
    panel_body.add_row(hint)

    console.print()
    console.print(Panel(
        panel_body,
        title=Text("OpenProgram", style="bold bright_blue"),
        border_style="bright_blue",
        box=box.ROUNDED,
        padding=(1, 2),
    ))
