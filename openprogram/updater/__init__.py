"""Auto-update for OpenProgram.

Strategy is install-method aware:

  * managed release runtime → formal GitHub Release updater
  * source checkout         → explicit gated source upgrade
  * anything else           → no product update path

Public API:

    detect_install_method()      — managed_release | source_checkout | unknown
    check_for_update()           — query upstream; returns UpdateInfo or None
    apply_update()               — download / pull / install the new version
    background_check_and_apply() — deprecated compatibility helper; never
                                    called by worker startup
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
