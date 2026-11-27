"""Handing an app its import link: checking first that something here opens it.

After yetone/magpie ``internal/agent/applink.go``, ``applink_darwin.go``,
``applink_other.go`` and ``applink_windows.go``, with the desktop-entry
reading of ``lody.go`` (MIT License, Copyright (c) yetone). The system
starts the opener and never says whether it found a program, so without
the check a click on the row's link does nothing at all when the app never
registered its scheme or was moved since. A check that cannot be read is
unknown, and the link is handed over anyway.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

#: The app behind each import link's scheme.
LINK_APPS = {"cindy": "Cindy", "lody": "Lody", "aionui": "AionUi", "cherrystudio": "Cherry Studio"}


class NoLinkHandler(Exception):
    """Nothing on this computer opens the app's links (magpie ``NoLinkHandlerError``)."""

    def __init__(self, app: str, scheme: str, exe: str = ""):
        self.app, self.scheme, self.exe = app, scheme, exe
        if exe:
            message = f"{app}'s links ({scheme}://) open {exe}, which isn't there"
        else:
            message = f"nothing on this computer opens {app}'s links ({scheme}://)"
        super().__init__(message)


class Unknown(Exception):
    """Which program opens a scheme could not be read."""


def system_open(link: str) -> None:
    """Hand ``link`` to the system's opener (not waited on)."""
    if sys.platform == "darwin":
        argv = ["open", link]
    elif sys.platform == "win32":
        argv = ["rundll32", "url.dll,FileProtocolHandler", link]
    else:
        argv = [shutil.which("xdg-open") or "xdg-open", link]
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=sys.platform != "win32")


def open_app_link(link: str, open_: Callable[[str], None] = system_open,
                  handler: Optional[Callable[[str], tuple[str, bool]]] = None) -> None:
    """Hand an app's import link to ``open_`` after checking that something
    here opens it (magpie ``OpenAppLink``). ``handler(scheme)`` is
    ``(program, found)``, raising :class:`Unknown` when it can't be read."""
    scheme, sep, _ = link.partition("://")
    if not sep:
        scheme = link.partition(":")[0]
    scheme = scheme.lower()
    app = LINK_APPS.get(scheme) or scheme
    try:
        exe, found = (handler or link_handler)(scheme)
    except Unknown:
        open_(link)
        return
    if not found:
        raise NoLinkHandler(app, scheme)
    if exe and gone(exe):
        raise NoLinkHandler(app, scheme, exe)
    open_(link)


def gone(exe: str) -> bool:
    """``exe``, a full path, isn't there. A bare name is found on PATH by the
    system and isn't judged; a failed look is unknown, not gone."""
    if not os.path.isabs(exe):
        return False
    try:
        os.stat(exe)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def command_program(cmd: str) -> str:
    """The program a Windows ``shell\\open\\command`` value runs."""
    cmd = cmd.strip()
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        return cmd[1:end] if end >= 0 else cmd[1:]
    for i, char in enumerate(cmd):
        if char in " \t":
            return cmd[:i]
    return cmd


def link_handler(scheme: str) -> tuple[str, bool]:
    """Which program opens ``scheme``'s links here, by this system's own register."""
    if sys.platform == "darwin":
        return _mac_handler(scheme)
    if sys.platform == "win32":
        return _windows_handler(scheme)
    return _xdg_handler(scheme)


# macOS: Launch Services (applink_darwin.go)

def _mac_handler(scheme: str) -> tuple[str, bool]:
    """Ask Launch Services which app opens ``scheme://x``, without opening it.
    The app's path isn't judged: Launch Services forgets an app that is gone."""
    try:
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        cs = ctypes.CDLL(ctypes.util.find_library("CoreServices"))
    except (OSError, TypeError) as exc:
        raise Unknown(str(exc)) from exc
    cf.CFURLCreateWithBytes.restype = ctypes.c_void_p
    cf.CFURLCreateWithBytes.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32, ctypes.c_void_p]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFErrorGetCode.restype = ctypes.c_long
    cf.CFErrorGetCode.argtypes = [ctypes.c_void_p]
    cs.LSCopyDefaultApplicationURLForURL.restype = ctypes.c_void_p
    cs.LSCopyDefaultApplicationURLForURL.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    raw = f"{scheme}://x".encode("utf-8")
    url = cf.CFURLCreateWithBytes(None, raw, len(raw), 0x08000100, None)  # kCFStringEncodingUTF8
    if not url:
        raise Unknown("launch services: bad link")
    error = ctypes.c_void_p()
    try:
        app = cs.LSCopyDefaultApplicationURLForURL(url, 0xFFFFFFFF, ctypes.byref(error))  # kLSRolesAll
    finally:
        cf.CFRelease(url)
    if app:
        cf.CFRelease(app)
        return "", True
    if error.value:
        code = cf.CFErrorGetCode(error.value)
        cf.CFRelease(error.value)
        if code == -10814:  # kLSApplicationNotFoundErr
            return "", False
    raise Unknown("launch services: unknown")


# Windows: the registry (applink_windows.go)

def _windows_handler(scheme: str) -> tuple[str, bool]:
    """``HKEY_CLASSES_ROOT\\<scheme>\\shell\\open\\command``, which Electron's
    setAsDefaultProtocolClient writes; else the app the user chose in Default
    apps. A key with no command of its own is handed to another program."""
    import winreg  # type: ignore[import-not-found]
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, scheme))
    except FileNotFoundError:
        return _user_choice(scheme)
    except OSError as exc:
        raise Unknown(str(exc)) from exc
    try:
        key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, scheme + r"\shell\open\command")
    except FileNotFoundError:
        return "", True
    except OSError as exc:
        raise Unknown(str(exc)) from exc
    try:
        value, kind = winreg.QueryValueEx(key, "")
    except FileNotFoundError:
        return "", True
    except OSError as exc:
        raise Unknown(str(exc)) from exc
    finally:
        winreg.CloseKey(key)
    if kind == winreg.REG_EXPAND_SZ:
        value = os.path.expandvars(value)
    return command_program(str(value)), True


def _user_choice(scheme: str) -> tuple[str, bool]:
    import winreg  # type: ignore[import-not-found]
    path = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations" + "\\" + scheme + r"\UserChoice"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        return "", False
    except OSError as exc:
        raise Unknown(str(exc)) from exc
    try:
        prog_id, _ = winreg.QueryValueEx(key, "ProgId")
    except FileNotFoundError:
        return "", False
    except OSError as exc:
        raise Unknown(str(exc)) from exc
    finally:
        winreg.CloseKey(key)
    return "", bool(prog_id)


# Linux and the BSDs: xdg-mime and desktop entries (applink_other.go, lody.go)

def _xdg_handler(scheme: str) -> tuple[str, bool]:
    """The desktop entry xdg-mime names for ``scheme``, and its program, as
    xdg-open finds them. KDE's empty answer is unknown: kde-open reads KDE's
    own service cache, which can know a handler xdg-mime's lookup doesn't."""
    kde = "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    tool = shutil.which("xdg-mime")
    if not tool:
        raise Unknown("no xdg-mime")
    try:
        out = subprocess.run([tool, "query", "default", f"x-scheme-handler/{scheme}"],
                             capture_output=True, text=True, timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unknown(str(exc)) from exc
    name = out.strip()
    if name:
        return desktop_handler(name, xdg_data_dirs())
    if kde:
        raise Unknown("xdg-mime: no answer under KDE")
    return "", False


def xdg_data_dirs() -> list[str]:
    """The folders whose applications/ hold desktop entries, the user's first."""
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    dirs = [data]
    for folder in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(os.pathsep):
        if os.path.isabs(folder):
            dirs.append(folder)
    return dirs


def entry_paths(entry_id: str) -> list[str]:
    """Where under applications/ an entry's id can be: the id itself, or with
    its dashes standing for subfolders (kde-foo.desktop as kde/foo.desktop)."""
    paths = [entry_id]
    for i, char in enumerate(entry_id):
        if char == "-":
            paths.append(os.path.join(entry_id[:i].replace("-", "/"), entry_id[i + 1:]))
    return paths


def desktop_args(entry: str, link: str) -> Optional[list[str]]:
    """The command line a desktop entry's Exec gives for ``link``, as the
    Desktop Entry Specification reads it: double-quoted arguments with
    backslash escapes, %u and %U the link, %% a %, other field codes dropped,
    the link put last when Exec takes none. ``None`` with no Exec."""
    line = ""
    inside = False
    for raw in entry.split("\n"):
        text = raw.strip()
        if text.startswith("["):
            inside = text == "[Desktop Entry]"
            continue
        key, sep, value = text.partition("=")
        if inside and sep and key.strip() == "Exec":
            line = value.strip()
            break
    argv: list[str] = []
    arg: list[str] = []
    has = quoted = gave = False
    i = 0
    while i < len(line):
        char = line[i]
        if quoted and char == "\\" and i + 1 < len(line):
            i += 1
            arg.append(line[i])
        elif char == '"':
            quoted, has = not quoted, True
        elif not quoted and char in " \t":
            if has:
                argv.append("".join(arg))
            arg, has = [], False
        elif char == "%" and i + 1 < len(line):
            i += 1
            code = line[i]
            if code == "%":
                arg.append("%")
                has = True
            elif code in "uU":
                arg.append(link)
                has = gave = True
        else:
            arg.append(char)
            has = True
        i += 1
    if has:
        argv.append("".join(arg))
    if not argv:
        return None
    if not gave:
        argv.append(link)
    return argv


def desktop_exec(entry: str, link: str) -> Optional[list[str]]:
    """The command a desktop entry runs for ``link``; ``None`` with no Exec,
    or with no program there to run (an AppImage moved since)."""
    argv = desktop_args(entry, link)
    if not argv:
        return None
    if not os.path.isabs(argv[0]):
        found = shutil.which(argv[0])
        if not found:
            return None
        argv[0] = found
    elif not (os.path.isfile(argv[0]) and os.access(argv[0], os.X_OK)):
        return None
    return argv


def desktop_handler(name: str, dirs: list[str]) -> tuple[str, bool]:
    """The program the desktop entry ``name`` runs, looked for in ``dirs``'
    applications/ as xdg-open does. With no entry, where the user's own would
    be (which isn't there: gone); with its program gone, that program."""
    if not name.endswith(".desktop") or "/" in name or "\\" in name:
        return "", True
    for folder in dirs:
        if not folder:
            continue
        for rel in entry_paths(name):
            path = os.path.join(folder, "applications", rel)
            try:
                text = Path(path).read_text(encoding="utf-8", errors="replace")
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise Unknown(str(exc)) from exc
            argv = desktop_args(text, "")
            if not argv:
                return path, True
            if os.path.isabs(argv[0]):
                return argv[0], True
            found = shutil.which(argv[0])
            return (found, True) if found else ("", False)
    if not dirs or not dirs[0]:
        return "", False
    return os.path.join(dirs[0], "applications", name), True
