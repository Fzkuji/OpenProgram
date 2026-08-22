# Write a Workflow package

This page is the authoring contract for reusable Workflow packages created by a person or by OpenProgram's author Agent. Both paths use the same static validator.

## Required layout

The project directory name, project name, entry-point name, and Python package name must be the same lowercase Python identifier.

```text
weekly_report/
├── pyproject.toml
├── README.md
├── __init__.py
├── workflow.py
├── steps/
│   └── prepare.py
└── tests/
    └── test_workflow.py
```

`goals/` and `helpers/` are also valid helper directories. At least one non-`__init__.py` helper module is required. Python source outside these locations is rejected.

## Metadata

```toml
[project]
name = "weekly_report"
version = "0.1.0"
description = "Prepare an evidence-based weekly report."
keywords = ["weekly report", "status update"]

[tool.openprogram]
display-name = "weekly_report"

[project.entry-points."openprogram.workflows"]
weekly_report = "workflows.weekly_report:weekly_report"
```

Names must begin with a lowercase letter and contain only lowercase letters, digits, and underscores. A summary is required and limited to 500 characters. The `keywords`/tags array is required but may be empty; it accepts at most 20 strings and 60 characters per entry.

## Public entry point

`workflow.py` must define exactly one public function whose name matches the project. It uses the existing `@agentic_function` decorator and accepts exactly one positional `task` argument.

```python
from openprogram.agentic_programming import agentic_function

from .steps.prepare import prepare


@agentic_function
def weekly_report(task: str):
    return prepare(task)
```

`__init__.py` re-exports that function:

```python
from .workflow import weekly_report

__all__ = ["weekly_report"]
```

## Allowed Python

Top-level package code may contain module docstrings, allowed `from ... import ...` statements, an optional `__all__`, and function definitions. Classes, ordinary `import x` statements, mutable module constants, arbitrary top-level calls, and redefining managed names such as `llm`, `agent`, or `goal` are rejected.

Absolute imports are limited to:

- `openprogram.agentic_programming`
- `openprogram.programs.workflow.*`
- `openprogram.programs.tools.*`
- one `workflows.<name>` import at a time, with the imported function matching the package name

Normal relative imports inside the package are allowed. `tests/test_workflow.py` may import `workflows.<project_name>`. Static directory validation checks only the import shape; create/revise publication resolves each Workflow dependency, rejects missing or cyclic dependencies, and pins the selected Git revision.

## Static validation

```bash
openprogram workflows validate ./weekly_report
openprogram workflows validate ./weekly_report --json
```

The command checks the directory boundary, metadata, required files, Python syntax, top-level statements, imports, decorators, entry-point signature, helper presence, and re-export. It is read-only: it does not initialize Git, write files, import the package, or execute its tests.

Python-generated `__pycache__` directories are ignored so a package remains valid after import. Other files still follow the package path contract; validation does not delete cache files.

A successful JSON result includes `ok`, `workflow_id`, normalized metadata, the validated Python file list, and `executed_tests: false`. An invalid package exits with status 1 and reports `error_type` and `error`.

## Current integration boundary

Static validation alone does not publish a package. OpenProgram currently publishes generated packages through `create_workflow` and explicit updates through `revise_workflow`. A manual publish command will require a forced-sandbox behavior-test gate first, so untrusted Python cannot read credentials, write outside its candidate directory, use the network, or run indefinitely.

The legacy `entry.py` format is read-only compatibility for historical revisions and runs. Do not use it for new packages.
