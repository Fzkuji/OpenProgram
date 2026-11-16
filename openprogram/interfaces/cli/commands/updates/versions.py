"""`openprogram versions`: the argument surface for the out-of-version tool.

The work is done by ``<root>/bootstrap/versions.py`` under the base's Python,
which ``bin/openprogram`` starts directly, so it works when this version is
broken. This module exists so the command is in ``--help`` and so that a Python
CLI invocation reaches the same tool.
"""
from __future__ import annotations

import subprocess
import sys


def _cmd_versions(args) -> int:
    from openprogram.operations.self_update.versions import layout
    from openprogram.state.paths import get_state_dir

    running = layout.current_runtime()
    root = running.layout.root if running is not None else layout.for_state(get_state_dir()).root
    tool = root / "bootstrap" / "versions.py"
    if not (root / "current").is_file() or not tool.is_file():
        print(
            "This install does not keep separate versions: it runs from a source "
            "checkout or from the runtime inside the App. Nothing to list or switch.",
            file=sys.stderr,
        )
        return 1
    command = [sys.executable, "-I", "-B", str(tool), "--root", str(root), args.versions_verb]
    if args.versions_verb == "use":
        command.append(args.version)
    elif args.versions_verb == "restore-data":
        command.append(args.backup)
    return subprocess.call(command)
