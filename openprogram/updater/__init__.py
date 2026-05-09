"""Auto-update for OpenProgram.

Strategy is install-method aware:

  * git checkout (``pip install -e .`` from a clone) → ``git pull``
  * standalone binary (PyInstaller / similar)        → download + atomic swap
  * pip wheel install                                  → currently a no-op,
                                                         will become "pip install
                                                         --upgrade" once OpenProgram
                                                         ships to PyPI

Public API:

    detect_install_method()      — git_checkout | binary | pip_wheel | unknown
    check_for_update()           — query upstream; returns UpdateInfo or None
    apply_update()               — download / pull / install the new version
    background_check_and_apply() — fire-and-forget thread used at worker start
    is_disabled()                — environment / config kill switch

State files (under ``<state-dir>/``):

    update.last_check    — Unix timestamp of the last upstream query
    update.staged        — JSON: {"version": str, "applied_at": int} written
                           after a successful apply, read at next start to
                           show "updated to X" banner
"""
from .detect import detect_install_method, InstallMethod
from .runner import (
    apply_update,
    background_check_and_apply,
    check_for_update,
    is_disabled,
    pop_staged_notice,
    UpdateInfo,
)

__all__ = [
    "InstallMethod",
    "UpdateInfo",
    "apply_update",
    "background_check_and_apply",
    "check_for_update",
    "detect_install_method",
    "is_disabled",
    "pop_staged_notice",
]
