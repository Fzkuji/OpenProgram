"""Where a process's ``logging`` records go.

Library code logs through ``logging`` and never configures it; the process
entry points do, once, by calling :func:`configure_process_logging`:

* the worker (``operations/worker/runner.py`` ``run_foreground``) and the
  scheduler worker (``programs/tools/jobs/cron/worker.py``) at ``INFO``: their
  stdout and stderr are their log files, so records land next to the
  ``[worker +12.3s]`` status lines;
* the foreground Server (``python -m openprogram.interfaces.server``) at
  ``INFO``;
* the CLI (``interfaces/cli/application.py`` ``main``) at ``WARNING``: the
  interactive chat shares the terminal, so only warnings and errors may
  appear there.

``OPENPROGRAM_LOG_LEVEL`` (a level name, any case) overrides the default
level in every one of them. A host that configured the root logger first (an
embedding application, pytest's log capture) keeps its handlers; only the
override is applied there. The ACP server (``interfaces/acp/server.py``)
runs inside the CLI and calls this again with ``OPENPROGRAM_ACP_LOG``'s level,
which only moves the level of the handler the CLI attached.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import TextIO

ENV_LEVEL = "OPENPROGRAM_LOG_LEVEL"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"

# Dependencies that log every request or connection at INFO. They keep their
# warnings and errors; at DEBUG they speak in full.
QUIET_LOGGERS = ("httpx", "httpcore", "websockets", "asyncio", "mcp")
# Set on the handler this module attaches, so a later call recognises it.
_MARK = "_openprogram_process_logging"


def _level_from_env() -> int | None:
    name = os.environ.get(ENV_LEVEL, "").strip().upper()
    if not name:
        return None
    level = logging.getLevelName(name)
    return level if isinstance(level, int) else None


def _level(name: str) -> int:
    level = logging.getLevelName(name.strip().upper())
    return level if isinstance(level, int) else logging.INFO


def configure_process_logging(default_level: str = "INFO", *, stream: TextIO | None = None) -> None:
    """Send this process's log records to ``stream`` (stderr by default).

    The first call attaches one handler to the root logger. A later call in
    the same process (the CLI's ``main`` running ``worker run --foreground``
    and then the worker's own entry) adds nothing and only moves the level to
    the new default. When the root logger carries handlers somebody else
    attached, only an ``OPENPROGRAM_LOG_LEVEL`` override is applied.
    """
    root = logging.getLogger()
    env_level = _level_from_env()
    if root.handlers and not any(getattr(h, _MARK, False) for h in root.handlers):
        if env_level is not None:
            root.setLevel(env_level)
        return
    if not root.handlers:
        handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
        handler.setFormatter(logging.Formatter(FORMAT, DATE_FORMAT))
        setattr(handler, _MARK, True)
        root.addHandler(handler)
    root.setLevel(env_level if env_level is not None else _level(default_level))
    quiet = root.level > logging.DEBUG
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING if quiet else logging.NOTSET)


__all__ = ["configure_process_logging", "ENV_LEVEL", "FORMAT"]
