"""Internal CLI subcommand handlers for :mod:`openprogram.interfaces.cli.application`.

The package entry point keeps the argparse setup, the dispatch chain, the TUI-tty
globals, and the public ``main`` entry point. All ``_cmd_<verb>`` and
``_dispatch_<group>_verb`` handler bodies live in topic modules here:

    programs.py  — programs list/new/edit/run/app, configure, runtime
    skills.py    — skills list/doctor/install, install_skills
    sessions.py  — sessions list/resume
    agents.py    — agents list/add/rm/show/set-default
    channels.py  — accounts + bindings + login
    web.py       — web UI launcher
    chat.py      — interactive cli chat
    cron.py      — scheduler-worker

``openprogram.interfaces.cli.application`` re-exports these at module level so external callers
(``openprogram.interfaces.cli.chat``, tests, ``openprogram.interfaces.cli.ink``) that import
``_cmd_<name>`` from ``openprogram.interfaces.cli.application`` keep working.
"""
