from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[3]


def test_interactive_library_import_does_not_redirect_stdio() -> None:
    script = textwrap.dedent(
        """
        import pathlib
        import sys

        events = []
        original_stdout = sys.stdout

        class InteractiveStdout:
            def isatty(self):
                return True

            def __getattr__(self, name):
                return getattr(original_stdout, name)

        def reject_mkdir(*args, **kwargs):
            events.append("mkdir")
            raise RuntimeError("library import attempted CLI redirection")

        sys.argv = ["library-host"]
        sys.stdout = InteractiveStdout()
        pathlib.Path.mkdir = reject_mkdir

        import openprogram.cli.chat  # noqa: F401

        raise SystemExit(0 if not events else 7)
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
