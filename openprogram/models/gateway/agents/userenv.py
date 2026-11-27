"""Environment variables set for the user's apps, for a GUI app that reads its
endpoint only from its environment (Cursor Private Inference).

After yetone/magpie ``internal/agent/cursorlocal_env_darwin.go``,
``cursorlocal_env_windows.go`` and ``cursorlocal_env_other.go`` (MIT
License, Copyright (c) yetone), so an app opened from the Dock, the Start
menu or the desktop from now on has them:

* macOS: ``launchctl setenv`` in launchd's user session (forgotten at a
  restart, when the agent sets them again);
* Windows: the user's ``Environment`` in the registry, then
  ``WM_SETTINGCHANGE`` to Explorer;
* Linux: an ``environment.d`` file systemd's user session reads at login,
  and ``systemctl --user set-environment`` for apps it starts from now on.

Under pytest only the environment.d file is written (under the test's
home); launchctl, the registry and systemctl are left alone unless a test
asks (:data:`ALLOW_IN_TESTS`).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

ALLOW_IN_TESTS = False


class EnvError(Exception):
    """The variables could not be set."""


def _testing() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) and not ALLOW_IN_TESTS


def env_d(name: str) -> Path:
    """The environment.d file ``name`` (``<name>.conf``) of the user's systemd."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    cfg = Path(xdg) if xdg and os.path.isabs(xdg) else Path.home() / ".config"
    return cfg / "environment.d" / f"{name}.conf"


def set_user_env(names: list[str], env: Optional[dict[str, str]], *, file: str) -> None:
    """Set each of ``names`` to its value in ``env`` for the user's apps from
    now on; ``env`` None unsets them. ``file`` names the environment.d file
    on Linux."""
    if sys.platform == "darwin":
        if _testing():
            return
        for name in names:
            args = ["launchctl", "setenv", name, env[name]] if env and name in env else ["launchctl", "unsetenv", name]
            try:
                done = subprocess.run(args, capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise EnvError(f"launchctl: {exc}") from exc
            if done.returncode != 0:
                raise EnvError(f"launchctl {' '.join(args[1:3])}: {(done.stderr or done.stdout).strip()}")
    elif sys.platform == "win32":
        if _testing():
            return
        _windows(names, env)
    else:
        _linux(names, env, env_d(file))


def _linux(names: list[str], env: Optional[dict[str, str]], path: Path) -> None:
    if env is None:
        path.unlink(missing_ok=True)
        if not _testing():
            subprocess.run(["systemctl", "--user", "unset-environment", *names], capture_output=True, timeout=10)
        return
    from ..edit import write_bytes_atomic
    # it holds a key: made private, never readable by others even for a moment
    write_bytes_atomic(path, "".join(f"{name}={env[name]}\n" for name in names).encode("utf-8"),
                       keep_mode=False, mode=0o600)
    if not _testing():
        try:
            subprocess.run(["systemctl", "--user", "set-environment", *(f"{n}={env[n]}" for n in names)],
                           capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass  # no systemd user session: the file is read at the next login


def _windows(names: list[str], env: Optional[dict[str, str]]) -> None:
    import ctypes
    import winreg  # type: ignore[import-not-found]
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
            for name in names:
                if env and name in env:
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, env[name])
                else:
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
    except OSError as exc:
        raise EnvError(f"the user's environment: {exc}") from exc
    result = ctypes.c_size_t()
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000,  # type: ignore[attr-defined]
                                             ctypes.byref(result))
