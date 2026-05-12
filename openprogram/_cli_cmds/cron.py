"""``openprogram cron-worker`` handler."""
from __future__ import annotations


def _cmd_cron_worker(once: bool, show_list: bool) -> None:
    """Dispatch cron-worker subcommand: --list, --once, or run forever."""
    from openprogram.tools.cron import list_next, run_forever, run_once

    if show_list:
        list_next()
        return
    if once:
        fired = run_once()
        print(f"Fired {fired} entr{'y' if fired == 1 else 'ies'}.")
        return
    run_forever()
