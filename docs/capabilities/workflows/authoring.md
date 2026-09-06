# Write, test, and publish a Workflow

A Workflow is a Python package with one public `@agentic_function` entry point. People and OpenProgram's author Agent use the same package validator and Git publication format. Functions compose through ordinary Python imports and calls.

## Define the contract before writing code

Record the following in the package's `README.md`:

| Item | Required information |
| --- | --- |
| Purpose | The concrete task and the evidence needed to complete it |
| Input | Meaning of `task`, required context, and invalid-input behavior |
| Output | Return type, files produced, and how success is verified |
| Effects | Model, tool, file, network, and external-submission behavior |
| Failure and cancellation | What remains after a failed or interrupted run; whether retry is safe |
| Dependencies | Imported Workflows, required tools and services |
| Tests | Behavior checks, mocked external work, and any separate live checks |
| Usage | One reproducible invocation and the expected result |

Keep the public entry point small. Put preparation, checks, or separate stages in helpers when they have a distinct responsibility. Use `llm`, `agent`, `goal`, tools, and other Workflows directly; do not implement another dispatcher or execution engine. Handle expected invalid input explicitly and let runtime cancellation propagate.

## Package layout and identity

The directory, project name, entry-point name, and Python package name must be the same lowercase Python identifier.

```text
project_report/
├── pyproject.toml
├── README.md
├── __init__.py
├── workflow.py
├── steps/
│   ├── __init__.py
│   └── prepare.py
└── tests/
    └── test_workflow.py
```

`goals/` and `helpers/` are also supported. At least one non-`__init__.py` helper module is required. Python source outside the package entry, helper directories, and tests is rejected. Bytecode caches are ignored; they are not package source.

Names begin with a lowercase letter and contain only lowercase letters, digits, and underscores, up to 80 characters. The summary is required and limited to 500 characters. Tags are required but may be empty, with at most 20 strings of 60 characters each.

`pyproject.toml`:

```toml
[project]
name = "project_report"
version = "0.1.0"
description = "Prepare a draft report from verified project evidence."
keywords = ["report", "evidence"]

[tool.openprogram]
display-name = "project_report"

[project.entry-points."openprogram.workflows"]
project_report = "workflows.project_report:project_report"
```

## Implement the entry point

`workflow.py` defines exactly one public function, named after the package, with exactly one positional `task` argument:

```python
from openprogram.agentic_programming import agentic_function
from .steps.prepare import prepare


@agentic_function
def project_report(task: str) -> str:
    return prepare(task)
```

`__init__.py` re-exports it:

```python
from .workflow import project_report

__all__ = ["project_report"]
```

Leave `steps/__init__.py` empty. In `steps/prepare.py`:

```python
from openprogram.programs.workflow.goal import goal


def prepare(task: str) -> str:
    request = task.strip()
    if not request:
        raise ValueError("A report request is required")
    return goal(
        "Prepare a draft report using only supplied or verified evidence. "
        "Do not submit it to an external service. Request: " + request,
        max_rounds=2,
    )
```

The README for this example states that input describes the period, evidence and format; output is a draft string; the Goal may use configured models and tools to inspect evidence; external submission is excluded. Live provider behavior needs separate verification. A canceled or failed run is not a completed report, and retries may repeat evidence collection.

## Write behavior tests

Test expected output, invalid input, and the effects the Workflow is allowed to request. Mock external work in the publication tests: network access is disabled. Do not treat a test that only checks `callable(entrypoint)` as proof of behavior.

For the example, `tests/test_workflow.py` verifies the draft-only request and rejects empty input before any Goal call:

```python
from workflows.project_report import project_report


def test_draft_request(monkeypatch):
    calls = []

    def fake_goal(prompt, **kwargs):
        calls.append(prompt)
        return "verified draft"

    monkeypatch.setattr("workflows.project_report.steps.prepare.goal", fake_goal)
    result = project_report.__wrapped__("  this week's verified commits  ")
    assert result == "verified draft"
    assert len(calls) == 1
    assert "this week's verified commits" in calls[0]
    assert "Do not submit" in calls[0]


def test_empty_request(monkeypatch):
    def unexpected_goal(*args, **kwargs):
        raise AssertionError("invalid input reached the Goal")

    monkeypatch.setattr("workflows.project_report.steps.prepare.goal", unexpected_goal)
    try:
        project_report.__wrapped__("   ")
    except ValueError:
        return
    raise AssertionError("empty input was accepted")
```

`__wrapped__` is the original decorated Python function used here for isolated behavior tests. Normal users call the public function or submit its chat form. These tests do not claim to verify model quality, Runtime integration, or successful external actions.

## Allowed imports and composition

Package top-level code permits docstrings, allowed `from ... import ...` statements, optional `__all__`, and function definitions. Classes, plain `import x`, mutable top-level constants, arbitrary top-level calls, and replacing managed names such as `llm`, `agent`, or `goal` are rejected.

Absolute imports may reference `openprogram.agentic_programming`, `openprogram.programs.workflow.*`, `openprogram.programs.tools.*`, or one named Workflow:

```python
from workflows.project_report import project_report
```

The live catalog resolves that public name to the same authorized callable as `openprogram.programs.workflow.project_report`. Package-internal helpers use relative imports. Publication resolves named Workflow dependencies, rejects missing or cyclic dependencies, and records exact Git revisions. Snapshot execution loads those pinned dependencies, not the latest live package. Do not encode checkout locations in import strings.

## Validate, test, and publish

Use the same Python environment as OpenProgram. Pytest is a declared runtime dependency; behavior testing also requires an available OS sandbox. When using a source checkout, install the current project with:

```bash
python -m pip install -e .
```

Run the authoring commands against the package directory:

```bash
openprogram workflows validate ./project_report --json
openprogram workflows test ./project_report --json
openprogram workflows publish ./project_report --json
```

| Command | Observable result |
| --- | --- |
| `validate` | Checks metadata, boundaries, syntax, imports, entry signature, re-export, helpers, and tests without importing or executing the package; returns `executed_tests: false` |
| `test` | Copies validated source and pinned dependencies, executes pytest in a required sandbox, and returns `executed_tests: true`, `sandboxed: true`, and test output on success; does not publish |
| `publish` | Revalidates and retests the exact snapshot, commits it into the Workflow catalog, records the source, and returns `workflow_id` and the immutable Git `revision` |

Behavior testing currently supports macOS and Linux where the OS sandbox is available. It has a 60-second process deadline, disables network and automatic pytest plugins, removes credential environment variables, protects snapshot source from writes, uses an isolated home and temporary workspace, and prevents background processes from surviving the test. macOS denies subprocess creation; Linux confines descendants to a private PID namespace. Mock external process calls in portable publication tests. It does not fall back to unsandboxed execution. Failed tests, timeout, or unavailable sandbox prevent publication.

Publication does not change the authored directory. It rejects an existing destination unless replacement is explicit:

```bash
openprogram workflows publish ./project_report --replace --json
```

Replacement also requires a clean destination Git repository. Preserve or commit deliberate edits there before replacing it. The final publication operation rechecks the destination under its lock. A prior test report is never accepted in place of testing the snapshot being published.

The author Agent's `create_workflow` and `revise_workflow` remain separate generated-package entry points and run the same required sandbox tests before publication. Failed tests publish nothing; testing does not execute the user's real task. The manual commands above are the supported path for publishing files written by a person. The legacy `entry.py` format is for historical revisions and resume compatibility, not new packages.

## Portable paths and installed Apps

Published packages live at `openprogram/programs/workflow/<workflow_id>` relative to the OpenProgram project. Internal source records use a scoped POSIX relative path:

```json
{"scope": "programs", "path": "workflow/project_report", "kind": "workflow-publish", "source": "workflow:project_report"}
```

The scope resolves against `openprogram/programs/`, never the current conversation's working directory. Moving a source checkout preserves this identity. Traversal, absolute scoped paths, backslashes, external symlinks, and ambiguous catalog matches are rejected.

An installed App needs one explicit source-catalog binding to find a separate checkout. This installation setting is not repeated in each package. For local framework development, run `scripts/refresh-local-app.sh` from the checkout being installed, including after moving it. This rebuilds and restarts the default installation; it is not read-only validation.

Previously authorized Workflow records with obsolete checkout prefixes migrate when a structurally valid matching package exists in a known catalog. Existing external locations remain external. Migration does not authorize neighboring directories. Removing a source authorization remains effective if its directory is missing and later recreated.

## Favorites, Use, and troubleshooting

Open **Abilities → Programs**, refresh, and select the Workflow. Favorite saves its public function name. **Use** opens its parameter form in chat; nothing executes until you submit that form.

Programs and the sidebar share a callable catalog. Use requests a current catalog if the function is not cached, and displays a visible error when the function is unavailable or the request fails. Failed refreshes preserve the last successful catalog. Older responses cannot replace a newer catalog, and leaving the chat cancels a pending form opening.

If source is visible but Use is unavailable, verify the package, publication result, and source authorization, then refresh Programs. Source visibility alone does not prove Python import succeeded. If behavior tests fail, inspect the returned output; keep live provider checks separate from sandboxed publication tests. A dirty destination must be resolved before `--replace` can succeed.
