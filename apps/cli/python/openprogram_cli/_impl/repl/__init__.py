"""Internal Rich REPL implementation for :mod:`openprogram.cli.chat`.

``openprogram.cli.chat`` keeps the ``run_cli_chat`` entry point and small helpers
external callers import directly (``_get_chat_runtime`` etc.). All the
slash-command handlers, banner rendering, and per-turn execution live
in topic modules here:

    setup.py    — runtime detection + first-run wizard prompt
    banner.py   — tools/skills/functions/apps inventory + welcome panel
    handlers.py — every ``_handle_*`` slash command + dispatcher table
    turn.py     — ``_run_turn_with_history`` (one exec turn + persist)

``openprogram.cli.chat`` re-exports these at module level so external callers
(``scripts/profile_startup.py``, ``openprogram.setup``, ``openprogram.cli.commands.chat``,
tests) that import ``_get_chat_runtime`` / ``run_cli_chat`` etc. keep
working unchanged.
"""
