# `openprogram/cli/`

> OpenProgram CLI.

## Overview

Single-verb model (openclaw / gh / docker style). The top-level grammar
is:

    openprogram                           launch the terminal UI
                                          (Ink on macOS/Linux, Rich on
                                          Windows — both are "TUI";
                                          platform decides which)
    openprogram tui                       same as bare openprogram
    openprogram chat                      alias for `openprogram tui`
    openprogram --print "prompt"          one-shot — send prompt,
                                          print reply, exit
    openprogram --resume <session-id>     resume a prior chat session

    openprogram <verb> ...                everything else (web, programs,
                                          skills, providers, ...)

Examples:

    openprogram
    openprogram tui
    openprogram chat
    openprogram tui --print "summarise this file"
    openprogram tui --resume local_a1b2c3

    openprogram web                       browser UI (frontend + backend)

    openprogram programs list
    openprogram programs run my_func --arg key=value

    openprogram skills list
    openprogram skills install --target claude

    openprogram sessions list
    openprogram sessions resume <id> "answer"

    openprogram providers list
    openprogram providers login anthropic

Note on retired flags: ``--tui`` / ``--no-tui`` / ``--web`` / ``--cli``
are gone. The chat mode is now implicit (``openprogram`` is chat); the
browser is a verb (``openprogram web``); the REPL is a Windows-only
silent fallback when Ink can't initialise. ``--no-tui`` had no good
analogue (the verb-based design wins where the flag would have lost),
so it's removed entirely.

## Files in this directory

- **`__main__.py`** — Module entry point for ``python -m openprogram.cli``
- **`chat.py`** — Terminal chat for bare ``openprogram``
- **`ink.py`** — Launch the Ink-based TUI front-end
- **`parser.py`** — Top-level OpenProgram CLI argument grammar

## Sub-packages

- **`commands/`** — Internal CLI subcommand handlers for :mod:`openprogram.cli`
- **`repl/`** — Internal Rich REPL implementation for :mod:`openprogram.cli.chat`
- **`setup_sections/`** — Setup-wizard section bodies used by :mod:`openprogram.setup`

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
