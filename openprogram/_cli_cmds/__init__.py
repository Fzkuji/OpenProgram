"""Internal CLI subcommand handlers, split out of openprogram/cli.py.

cli.py keeps the argparse setup, the dispatch chain, the TUI-tty
globals, and the public ``main`` entry point. All ``_cmd_<verb>`` and
``_dispatch_<group>_verb`` handler bodies live in topic modules here:

    programs.py  — programs list/new/edit/run/app, configure, runtime
    skills.py    — skills list/doctor/install, install_skills
    browser.py   — browser install/status/refresh/reset/list/rm
    sessions.py  — sessions list/resume
    agents.py    — agents list/add/rm/show/set-default
    channels.py  — accounts + bindings + login
    web.py       — web UI launcher
    chat.py      — interactive cli chat + deep_work
    cron.py      — cron-worker

cli.py re-exports these at module level so external callers
(``openprogram.cli_chat``, tests, ``openprogram.cli_ink``) that import
``_cmd_<name>`` from ``openprogram.cli`` keep working.
"""
