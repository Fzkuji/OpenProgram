"""Source-checkout entry for the Python CLI application.

Installed distributions use the ``openprogram_cli`` package under
``apps/cli/python``. This module gives an uninstalled checkout the same import
and ``python -m openprogram_cli`` entry without copying application code into
the repository root.
"""

from pathlib import Path

_APP_PACKAGE_DIR = (
    Path(__file__).resolve().parent / "apps/cli/python/openprogram_cli"
)
__path__ = [str(_APP_PACKAGE_DIR)]

from openprogram.cli import build_parser, main  # noqa: E402

__all__ = ["build_parser", "main"]


if __name__ == "__main__":
    main()
