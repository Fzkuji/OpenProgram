"""Showing a file in the file manager of the computer the worker runs on."""
from __future__ import annotations

import ntpath
import os
import shutil
import sys
from collections.abc import Callable, Mapping


class NoFileManager(OSError):
    """This host has no file manager to show a file in; the message says why."""


def reveal_command(target: str, platform: str | None = None,
                   which: Callable[[str], str | None] | None = None,
                   environ: Mapping[str, str] | None = None) -> list[str] | str:
    """The command that selects ``target`` in the file manager.

    Windows gets one command line, not an argument list: Explorer reads
    ``/select,"<path>"`` itself and takes a quoted ``"/select,<path>"``,
    which is what an argument list becomes for a path with a space, as a
    folder name. Explorer also needs backslashes. A Windows path cannot
    contain a quote, so quoting it is complete.

    Linux has no verb for "select this file" that every desktop honours, so
    the containing folder opens; without a graphical session or ``xdg-open``
    there is nothing to open it with.
    """
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    if platform == "darwin":
        return ["open", "-R", target]
    if platform == "win32":
        return f'explorer.exe /select,"{ntpath.normpath(target)}"'
    if not (environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY")):
        raise NoFileManager("This computer has no graphical session to show the file in.")
    opener = (which or shutil.which)("xdg-open")
    if opener is None:
        raise NoFileManager("Showing a file on Linux needs xdg-open (the xdg-utils package).")
    return [opener, target if os.path.isdir(target) else os.path.dirname(target)]
