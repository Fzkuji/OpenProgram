"""Interactive CLI chat entry point."""
from __future__ import annotations


def _cmd_cli_chat(oneshot: str | None = None,
                  resume: str | None = None,
                  tui: bool = True) -> None:
    """Terminal chat entry point — delegates to openprogram.cli_chat.run_cli_chat."""
    from openprogram.cli_chat import run_cli_chat
    run_cli_chat(oneshot=oneshot, resume=resume, tui=tui)


