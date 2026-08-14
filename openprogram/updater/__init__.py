"""Shared helpers for OpenProgram's explicit update commands.

Strategy is install-method aware:

  * managed release runtime → formal GitHub Release updater
  * source checkout         → explicit gated source upgrade
  * anything else           → no product update path

The command implementation lives in ``openprogram._cli_cmds.upgrade``.
Nothing in this package applies an update during worker startup.
"""
from .detect import detect_install_method, InstallMethod

__all__ = [
    "InstallMethod",
    "detect_install_method",
]
