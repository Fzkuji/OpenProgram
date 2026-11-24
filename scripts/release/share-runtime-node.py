#!/usr/bin/env python3
"""Make Playwright's driver run on the runtime's own Node.

    share-runtime-node.py RUNTIME

The Playwright package carries a Node of its own (about 115 MB) beside its
driver, and a runtime already ships one in ``bin/node`` for the terminal UI.
The driver starts whatever sits at ``playwright/driver/node``, so that file
becomes a relative link to ``bin/node``: nothing has to set
``PLAYWRIGHT_NODEJS_PATH``, and the link stays right when the runtime moves.

Installing Playwright again puts its Node back, so every build runs this
after its packages and its Node are in place; a second run changes nothing.
Windows keeps both: a link there needs a privilege an install does not have.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys


def share(runtime: Path) -> list[Path]:
    """The driver directories whose Node was replaced by a link to ``bin/node``."""
    node = runtime / "bin" / "node"
    if os.name == "nt" or not node.is_file():
        return []
    changed = []
    for driver in sorted(runtime.glob("python/**/site-packages/playwright/driver")):
        own = driver / "node"
        target = os.path.relpath(node, driver)
        if own.is_symlink() and os.readlink(own) == target:
            continue
        if not own.is_symlink() and not own.is_file():
            continue  # no driver Node here: nothing this replaces
        own.unlink()
        own.symlink_to(target)
        changed.append(driver)
    return changed


def main(arguments: list[str]) -> int:
    if len(arguments) != 1:
        print(__doc__.split("\n\n")[1].strip(), file=sys.stderr)
        return 2
    runtime = Path(arguments[0])
    if not runtime.is_dir():
        print(f"not a runtime directory: {runtime}", file=sys.stderr)
        return 1
    for driver in share(runtime):
        print(f"Playwright's driver in {driver} runs on bin/node")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
