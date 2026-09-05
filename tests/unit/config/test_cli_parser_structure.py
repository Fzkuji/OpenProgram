from __future__ import annotations

import ast
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]


def test_cli_parser_has_a_dedicated_module_and_compatible_public_import() -> None:
    from openprogram import cli
    from openprogram.cli import parser

    assert cli.build_parser is parser.build_parser

    cli_tree = ast.parse(
        (ROOT / "openprogram" / "cli" / "__init__.py").read_text(encoding="utf-8")
    )
    assert "build_parser" not in {
        node.name for node in cli_tree.body if isinstance(node, ast.FunctionDef)
    }

    parser = cli.build_parser()
    assert parser.prog == "openprogram"
    assert parser.parse_args(["programs", "list"]).programs_verb == "list"
    workflow_args = parser.parse_args([
        "workflows", "validate", "/tmp/demo", "--json",
    ])
    assert workflow_args.workflows_verb == "validate"
    assert workflow_args.directory == "/tmp/demo"
    assert workflow_args.json is True
    assert parser.parse_args(["upgrade", "status"]).upgrade_verb == "status"
    assert parser.parse_args(["--no-alt-screen"]).no_alt_screen is True
    assert parser.parse_args(["tui", "--screen-reader"]).screen_reader is True
    assert parser.parse_args(["--screen-reader", "tui"]).screen_reader is True
    assert parser.parse_args(["--no-alt-screen", "tui"]).no_alt_screen is True
    assert parser.parse_args(["--resume", "local_before", "tui"]).resume == "local_before"
    assert parser.parse_args(["tui", "--resume", "local_after"]).resume == "local_after"


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
