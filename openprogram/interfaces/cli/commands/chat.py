"""Interactive CLI chat entry point."""
from __future__ import annotations


def _cmd_cli_chat(oneshot: str | None = None,
                  resume: str | None = None,
                  tui: bool = True,
                  response_format=None,
                  no_alt_screen: bool = False,
                  screen_reader: bool = False,
                  **print_options) -> None:
    """Terminal chat entry point — delegates to openprogram.interfaces.cli.chat.run_cli_chat."""
    from openprogram.interfaces.cli.chat import run_cli_chat
    run_cli_chat(
        oneshot=oneshot,
        resume=resume,
        tui=tui,
        response_format=response_format,
        no_alt_screen=no_alt_screen,
        screen_reader=screen_reader,
        **print_options,
    )
