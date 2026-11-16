"""Internal CLI subcommand handlers for :mod:`openprogram.interfaces.cli.application`.

The package entry point keeps the argparse setup, the dispatch chain, the TUI-tty
globals, and the public ``main`` entry point. All ``_cmd_<verb>`` and
``_dispatch_<group>_verb`` handler bodies live in topic modules, grouped by
what the commands are for:

    capabilities/   — Programs, skills, plugins, MCP servers
    conversations/  — chat, sessions, agents, sub-agents, executions, Jobs,
                      permissions, environments
    services/       — Web server, ACP, cron, channels and channel setup
    maintenance/    — backup, diagnostics, doctor, logs, rescue, trash
    updates/        — upgrade, self-update, versions, development instance
    providers/      — the credential command tree
    completion.py   — shell completion over the whole parser

``openprogram.interfaces.cli.application`` re-exports these at module level so external callers
(``openprogram.interfaces.cli.chat``, tests, ``openprogram.interfaces.cli.ink``) that import
``_cmd_<name>`` from ``openprogram.interfaces.cli.application`` keep working.
"""
