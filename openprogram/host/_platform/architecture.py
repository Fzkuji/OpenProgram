"""The CPU architecture this Python runs as, in the release matrix's words.

Packages, wheels and bundled executables are picked for the interpreter that
will load them, never for the machine underneath it. The two differ under
emulation: x64 Python on Windows on Arm is told ARM64 by
``platform.machine()``, which reads the machine. The interpreter's own build
answers instead:

- Windows: ``sysconfig.get_platform()`` is ``win-amd64`` or ``win-arm64``,
  taken from the interpreter's build.
- macOS: the same call names the build's slice, except for a universal2 build,
  where ``platform.machine()`` names the slice that is running (``x86_64``
  under Rosetta, because macOS answers a translated process as an Intel one).
- Linux: the build's ``MULTIARCH`` triplet, else the kernel's answer.

Standard library only: the release scripts run it with a bare build Python.
"""
from __future__ import annotations

import platform
import struct
import sys
import sysconfig

_NAMES = {"amd64": "x86_64", "x86_64": "x86_64", "x64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}


def interpreter_architecture() -> str:
    """``"arm64"`` or ``"x86_64"``: the architecture this interpreter was built for."""
    build = sysconfig.get_platform()
    if sys.platform == "win32":
        machine = build.removeprefix("win-")
    elif sys.platform == "darwin":
        machine = build.rsplit("-", 1)[-1]
        if machine.startswith("universal") or machine in {"intel", "fat", "fat3", "fat64"}:
            machine = platform.machine()
    else:
        multiarch = sysconfig.get_config_var("MULTIARCH") or ""
        machine = multiarch.split("-", 1)[0] if multiarch else platform.machine()
    name = _NAMES.get(machine.lower())
    if name is None or struct.calcsize("P") != 8:
        raise RuntimeError(f"this Python ({build}, {struct.calcsize('P') * 8}-bit) is not a supported architecture")
    return name
