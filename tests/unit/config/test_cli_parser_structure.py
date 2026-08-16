from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_cli_parser_has_a_dedicated_module_and_compatible_public_import() -> None:
    from openprogram import _cli_parser, cli

    assert cli.build_parser is _cli_parser.build_parser

    cli_tree = ast.parse((ROOT / "openprogram" / "cli.py").read_text())
    assert "build_parser" not in {
        node.name for node in cli_tree.body if isinstance(node, ast.FunctionDef)
    }

    parser = cli.build_parser()
    assert parser.prog == "openprogram"
    assert parser.parse_args(["programs", "list"]).programs_verb == "list"
    assert parser.parse_args(["upgrade", "status"]).upgrade_verb == "status"
