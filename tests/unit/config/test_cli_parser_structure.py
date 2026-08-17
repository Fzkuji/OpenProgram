from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[3]


def test_cli_parser_has_a_dedicated_module_and_compatible_public_import() -> None:
    from openprogram import cli
    from openprogram.cli import parser

    assert cli.build_parser is parser.build_parser

    cli_tree = ast.parse(
        (ROOT / "openprogram" / "cli" / "__init__.py").read_text()
    )
    assert "build_parser" not in {
        node.name for node in cli_tree.body if isinstance(node, ast.FunctionDef)
    }

    parser = cli.build_parser()
    assert parser.prog == "openprogram"
    assert parser.parse_args(["programs", "list"]).programs_verb == "list"
    assert parser.parse_args(["upgrade", "status"]).upgrade_verb == "status"


@pytest.mark.parametrize(
    "argv0",
    (
        "openprogram",
        "/usr/local/bin/openprogram.exe",
        "/tmp/openprogram-script.py",
        "/checkout/openprogram/__main__.py",
        "/checkout/openprogram/cli/__main__.py",
    ),
)
def test_cli_entrypoint_processes_are_recognized(argv0, monkeypatch) -> None:
    from openprogram import cli

    monkeypatch.setattr(sys, "argv", [argv0])

    assert cli._is_cli_process()


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
