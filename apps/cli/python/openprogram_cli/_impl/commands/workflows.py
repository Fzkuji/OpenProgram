"""Workflow package authoring commands."""

from __future__ import annotations

import json
from pathlib import Path
import sys


def _cmd_workflows_validate(directory: str, *, as_json: bool = False) -> int:
    from openprogram.programs.workflow.errors import InvalidWorkflow
    from openprogram.programs.workflow._project.validation import (
        validate_workflow_directory,
    )

    try:
        report = validate_workflow_directory(Path(directory))
    except (InvalidWorkflow, OSError, UnicodeError, ValueError) as exc:
        report = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "executed_tests": False,
        }
        if as_json:
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        else:
            print(f"Workflow package is invalid: {report['error']}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"Workflow package is valid: {report['workflow_id']} "
            f"({len(report['files'])} Python files; tests not executed)"
        )
    return 0


def _cmd_workflows_action(directory: str, *, publish: bool = False, replace: bool = False, as_json: bool = False) -> int:
    from openprogram.programs.workflow.errors import InvalidWorkflow
    from openprogram.programs.workflow._project.authoring import (
        publish_workflow_directory,
        test_workflow_directory,
    )

    try:
        report = (publish_workflow_directory(directory, replace=replace)
                  if publish else test_workflow_directory(directory))
    except (InvalidWorkflow, OSError, UnicodeError, ValueError) as exc:
        report = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
        if as_json:
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        else:
            print(f"Workflow {'publication' if publish else 'tests'} failed: {exc}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    elif publish:
        print(f"Published {report['workflow_id']} at {report['revision']} after sandboxed tests")
    else:
        print(report["test_output"].rstrip())
        print(f"Workflow tests passed in the OS sandbox: {report['workflow_id']}")
    return 0
