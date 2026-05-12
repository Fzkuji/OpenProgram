"""Inventory helpers + Hermes-style welcome banner for the CLI chat REPL."""
from __future__ import annotations


def _tool_inventory() -> tuple[int, list[str]]:
    from openprogram.tools import ALL_TOOLS, list_available
    names = list_available()  # only tools whose check_fn currently passes
    # Prefer the gated list; if the helper returns empty (no gating), fall
    # back to the full registry so the banner isn't misleadingly blank.
    if not names:
        names = list(ALL_TOOLS.keys())
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
    """Return (count, [name, ...]) of agentic functions in programs/functions/."""
    import os
    import openprogram
    base = os.path.join(os.path.dirname(openprogram.__file__),
                        "programs", "functions")
    names: list[str] = []
    for sub in ("buildin", "third_party", "meta"):
        d = os.path.join(base, sub)
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".py"):
                continue
            stem = fname[:-3]
            if stem.startswith("_") or stem == "__init__":
                continue
            names.append(stem)
    return len(names), names


def _application_inventory() -> tuple[int, list[str]]:
    """Return (count, [name, ...]) of applications in programs/applications/."""
    import os
    import openprogram
    d = os.path.join(os.path.dirname(openprogram.__file__),
                     "programs", "applications")
    if not os.path.isdir(d):
        return 0, []
    names: list[str] = []
    for entry in sorted(os.listdir(d)):
        full = os.path.join(d, entry)
        if entry.startswith("_") or entry.startswith("."):
            continue
        if os.path.isdir(full) and not entry.startswith("__"):
            names.append(entry)
        elif entry.endswith(".py") and entry != "__init__.py":
            names.append(entry[:-3])
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


def _print_banner(console, provider: str, model: str) -> None:
    """Two-row Hermes-style welcome panel: tools/skills + functions/apps."""
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box

    tool_count, tool_names = _tool_inventory()
    skill_count, skill_items = _skill_inventory()
    fn_count, fn_names = _function_inventory()
    app_count, app_names = _application_inventory()

    logo = Text("OpenProgram", style="bold bright_blue")
    subtitle = Text(f"  ·  {provider}/{model}", style="dim")
    header = logo + subtitle

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
    panel_body.add_row(grid)
    panel_body.add_row(footer)

    console.print()
    console.print(Panel(
        panel_body,
        title=header,
        border_style="bright_blue",
        box=box.ROUNDED,
        padding=(1, 2),
    ))
    console.print(
        Text("Tip: ", style="yellow bold")
        + Text("type your message, or /help to see commands.", style="dim")
    )
