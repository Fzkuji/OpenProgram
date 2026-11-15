from __future__ import annotations

import ast
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]


def test_cli_parser_has_a_dedicated_module_and_public_import() -> None:
    from openprogram.interfaces.cli import application as cli
    from openprogram.interfaces.cli import parser

    assert cli.build_parser is parser.build_parser

    cli_tree = ast.parse(
        (ROOT / "openprogram/interfaces/cli/application.py").read_text(encoding="utf-8")
    )
    assert "build_parser" not in {
        node.name for node in cli_tree.body if isinstance(node, ast.FunctionDef)
    }

    parser = cli.build_parser()
    assert parser.prog == "openprogram"
    assert parser.parse_args(["programs", "list"]).programs_verb == "list"
    check_args = parser.parse_args([
        "programs", "check", "/tmp/demo", "--json",
    ])
    assert check_args.programs_verb == "check"
    assert check_args.directory == "/tmp/demo"
    assert check_args.json is True
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
        "/checkout/openprogram/interfaces/cli/__main__.py",
        "/venv/lib/python3.13/site-packages/openprogram/interfaces/cli/__main__.py",
    ),
)
def test_cli_entrypoint_processes_are_recognized(argv0, monkeypatch) -> None:
    from openprogram.interfaces.cli import application as cli

    monkeypatch.setattr(sys, "argv", [argv0])

    assert cli._is_cli_process()


def test_programs_is_the_one_command_group_for_programs():
    from openprogram.interfaces.cli.application import build_parser
    parser = build_parser()
    group = next(a for a in parser._subparsers._group_actions if a.dest == "command")
    assert "apps" not in group.choices and "workflows" not in group.choices
    programs_parser = group.choices["programs"]
    verbs = next(a for a in programs_parser._subparsers._group_actions).choices
    assert set(verbs) == {"list", "run", "status", "cancel", "check", "install",
                          "uninstall", "enable", "disable"}

    args = parser.parse_args(["programs", "install", "/tmp/software", "--trust", "--replace"])
    assert (args.source, args.trust, args.replace) == ("/tmp/software", True, True)
    assert parser.parse_args(["programs", "list", "--json"]).json is True
    operation = parser.parse_args(["programs", "run", "example.calculator", "evaluate",
                                   "--input", '{"x": 1}', "--project", "p1"])
    assert (operation.name, operation.operation, operation.project) == ("example.calculator", "evaluate", "p1")
    entry = parser.parse_args(["programs", "run", "page_tool", "--arg", "text=hi"])
    assert entry.operation is None and entry.arg == ["text=hi"]
    assert parser.parse_args(["programs", "disable", "example.pdf-editor"]).id == "example.pdf-editor"
    assert parser.parse_args(["programs", "status", "run-1"]).run_id == "run-1"


def test_dev_deploy_builds_the_dev_branch_unless_a_ref_is_named():
    from openprogram.interfaces.cli.parser import build_parser
    from openprogram.operations.worker import dev_instance

    parser = build_parser()
    # ``main`` is the published line; deploying it would serve the last release.
    assert parser.parse_args(["dev", "deploy"]).ref == dev_instance.DEV_BRANCH
    assert parser.parse_args(["dev", "deploy", "abc123"]).ref == "abc123"
