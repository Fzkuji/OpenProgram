"""Putting text on the system clipboard with the tool each platform ships."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping

_TIMEOUT_SECONDS = 5.0


def copy_command(platform: str | None = None, which: Callable[[str], str | None] | None = None,
                 environ: Mapping[str, str] | None = None) -> tuple[list[str], str] | None:
    """The command that reads the text on stdin, and the encoding it expects.

    ``None`` where this host has no clipboard to reach: a Linux session with
    neither Wayland nor X11, or none of ``wl-copy``, ``xclip``, ``xsel``.
    """
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    which = which or shutil.which
    if platform == "darwin":
        tool = which("pbcopy")
        return ([tool], "utf-8") if tool else None
    if platform == "win32":
        # clip.exe reads its input in the console code page unless it starts with a UTF-16 mark.
        tool = which("clip.exe")
        return ([tool], "utf-16") if tool else None
    if environ.get("WAYLAND_DISPLAY") and (tool := which("wl-copy")):
        return [tool], "utf-8"
    if environ.get("DISPLAY"):
        if tool := which("xclip"):
            return [tool, "-selection", "clipboard"], "utf-8"
        if tool := which("xsel"):
            return [tool, "--clipboard", "--input"], "utf-8"
    return None


def support(platform: str | None = None, which: Callable[[str], str | None] | None = None,
            environ: Mapping[str, str] | None = None) -> dict:
    """Whether text can be copied here, and what is missing when it cannot."""
    platform = sys.platform if platform is None else platform
    if copy_command(platform, which, environ) is not None:
        return {"available": True, "reason": ""}
    if platform == "darwin":
        return {"available": False, "reason": "pbcopy is not on PATH."}
    if platform == "win32":
        return {"available": False, "reason": "clip.exe is not on PATH."}
    return {"available": False,
            "reason": "Copying needs a Wayland session with wl-copy, or an X11 session with xclip or xsel."}


def copy(text: str) -> bool:
    """Copy ``text``; False when this host has no clipboard tool or the tool refused."""
    found = copy_command()
    if found is None:
        return False
    command, encoding = found
    env = dict(os.environ)
    if sys.platform == "darwin":
        # pbcopy decodes stdin by locale, and a service has none.
        env.setdefault("LC_CTYPE", "UTF-8")
    try:
        # The X11 and Wayland tools stay behind to serve the selection: they must not inherit a pipe.
        done = subprocess.run(command, input=text.encode(encoding), env=env, timeout=_TIMEOUT_SECONDS,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0
