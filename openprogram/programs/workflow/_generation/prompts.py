"""Prompt templates and constructors for workflow generation."""

from __future__ import annotations

from typing import Callable, Optional

PLANNER_TOOLS = ("read", "grep", "glob", "list")
PROJECT_AUTHOR_ATTEMPTS = 4
AUTO_DECISION_ATTEMPTS = 3

DELIVERY_INSTRUCTIONS = """Workflow delivery contract:
- Unless the task explicitly asks for the content in chat, save substantive deliverables
  such as reports, code, and tables in the current working directory.
- Return only a short handoff describing completed work and useful warnings or next steps.
- Do not return a report body as the workflow handoff.
"""

PLANNER_INSTRUCTIONS = """You write an executable self-programmed workflow.

First decide whether one free-form agent can finish the task in one pass. If
so, reply with exactly SINGLE and no code.

Otherwise return one Python code block. Every import statement is forbidden.
The module must define exactly one top-level def workflow(): with no parameters.
The execution environment contains only the registered functions in the catalog
below and:

    llm(prompt, model="", effort="", response_format=None, choices=None,
        web_search=False, timeout_s=None) -> str | dict
    agent(prompt, description="", agent_id="", start_from="clean",
          run_in_background=False, to="", archive_when_done=False) -> str
    goal(prompt, model="", effort="", max_rounds=None,
         timeout_s=None) -> str
    validate_and_retry(action: Callable, check: str, retry: Callable,
                       max_retries=2) -> str
    route(question: str, options: list[str], context="") -> str
    conditional(condition: str, context="", if_true: Callable,
                if_false: Callable) -> str

llm makes one model request without tools or a session branch. agent starts a
free-form agent with a tool loop; select its model and tool set through agent_id,
which names an agent profile. goal runs a judgment loop: repeatedly calls agent
and uses llm to judge whether the prompt's requested outcome is complete.

Control flow primitives use llm for judgment:
- validate_and_retry: execute action, check result with llm, retry if failed
- route: let llm choose one option from a list
- conditional: llm judges condition (YES/NO) and executes one branch

{delivery}

Define ordinary Python helper functions and compose them with plain Python calls,
if/for/try statements, and return values. Verification belongs in the program and
may raise an exception when it fails. There is no step DSL.

Example:

    def find_issues():
        return agent(\"Review the codebase for issues\", description=\"find issues\")

    def workflow():
        findings = find_issues()
        if not findings:
            raise RuntimeError(\"issue review returned no result\")
        return "Reviewed the codebase; report saved to review.md"

Example with control flow primitives:

    def workflow():
        files = validate_and_retry(
            action=lambda: agent(\"Find auth related files\"),
            check=\"File count >= 3 and includes oauth\",
            retry=lambda: agent(\"Expand search to include oauth and openid\")
        )
        strategy = route(
            question=\"Choose migration strategy\",
            options=[\"Direct migration\", \"Refactor then migrate\"],
            context=files
        )
        return agent(
            f\"Write {strategy} plan for: {files}. Save it to migration-plan.md \"
            \"and return only the completed actions and file path.\"
        )

Available registered agentic functions (name, signature, first docstring
line):
{catalog}
"""

AUTO_DECISION_INSTRUCTIONS = """Decide whether to reuse one catalog candidate
or create a new workflow. Reply with one JSON object and no prose.

- reuse: {"action":"reuse","workflow_id":"one candidate id"}
- create: {"action":"create","missing_capability":"specific capability absent from every candidate"}

Use reuse only when an existing candidate can perform this task without
source changes. Prefer reuse when a candidate already covers the requested
task class; differences in wording, language, output path, date range, or
ordinary input parameters do not justify a new workflow. Use create only when
the task requires a materially different execution structure or capability
that every candidate lacks. A zero retrieval score is not evidence of a
capability mismatch; inspect each candidate contract before deciding.
When candidates exist, create must name the concrete missing capability;
an unsupported create decision is rejected before authoring or publication.
reuse may name only an id in the supplied candidate list. Never revise a
published project from this entry. Never provide a revision or source
files in this decision.
"""

PROJECT_AUTHOR_INSTRUCTIONS = """Write one complete reusable workflow project.
Reply with one JSON object and no prose:
{
  "project_metadata": {
    "name": "short_stable_python_name",
    "summary": "what class of tasks this project can perform",
    "tags": ["search terms"]
  },
  "readme": "Markdown describing applicability, outputs, and limits",
  "files": {
    "__init__.py": "from .workflow import short_stable_python_name\\n",
    "workflow.py": "from openprogram.agentic_programming import agentic_function\\n\\n@agentic_function\\ndef short_stable_python_name(task: str):\\n    ...\\n",
    "steps/__init__.py": "",
    "steps/example.py": "from openprogram.agentic_programming import agent\\n\\ndef example(task: str):\\n    ...\\n",
    "tests/test_workflow.py": "from workflows.short_stable_python_name import short_stable_python_name\\n"
  }
}

Return the complete project, not a patch. The project name must be a valid
lowercase Python identifier and is also the public function name. Export that
function from __init__.py. Define it in workflow.py with the existing
@agentic_function decorator and exactly one task parameter. Put reusable
responsibilities in separate steps/, goals/, or helpers/ modules and include
tests/test_workflow.py. Use ordinary relative imports inside the package.
Plain import statements such as `import json` are forbidden. Every Python
module top level may contain only a module docstring, allowed `from ... import
...` statements, an optional `__all__` assignment, and function definitions;
module-level constants or other assignments are forbidden. Put constants and
computed values inside functions. Absolute imports are allowed only from
`openprogram.agentic_programming`,
`openprogram.programs.workflow`, or one listed `workflows.<package>`.
Standard-library imports such as `pathlib`, `datetime`, `re`, and `json` are
forbidden; delegate filesystem, browser, and other external work to an existing
registered agentic function or to `agent()`.
Import llm, agent, goal, and control-flow helpers from
openprogram.agentic_programming. Import existing OpenProgram agentic functions
from their normal openprogram.programs.workflow module. Do not embed
the current task in source code; pass task into helpers. Reuse another listed
Workflow only with `from workflows.<package> import <package>`. Do not use dynamic
imports, classes, import hooks, a workflow decorator, or a workflow dispatcher.

{delivery}

Available registered functions:
{catalog}
"""


def _plan_prompt(task: str, functions: dict[str, Callable]) -> str:
    from .._generation import planner

    return (
        PLANNER_INSTRUCTIONS.replace("{delivery}", DELIVERY_INSTRUCTIONS).replace(
            "{catalog}", planner._function_catalog(functions)
        )
        + f"\n\n<task>\n{task}\n</task>"
    )


def _auto_decision_prompt(task: str, candidates: list[dict]) -> str:
    import json

    return (
        AUTO_DECISION_INSTRUCTIONS
        + "\n<workflow project candidates>\n"
        + json.dumps(candidates, ensure_ascii=False, indent=2)
        + "\n</workflow project candidates>"
        + f"\n\n<task>\n{task}\n</task>"
    )


def _author_prompt(
    task: str,
    functions: dict[str, Callable],
    *,
    base: Optional[dict] = None,
    error: str = "",
    state: Optional[dict] = None,
) -> str:
    import json

    from .._generation import planner

    prompt = (
        PROJECT_AUTHOR_INSTRUCTIONS.replace(
            "{delivery}", DELIVERY_INSTRUCTIONS
        ).replace("{catalog}", planner._function_catalog(functions))
        + "\n\n<reusable_workflows>\n"
        + planner._workflow_import_catalog()
        + "\n</reusable_workflows>"
        + f"\n\n<task>\n{task}\n</task>"
    )
    if base is not None:
        prompt += (
            "\n\n<base_project>\n"
            + json.dumps(
                base,
                ensure_ascii=False,
                indent=2,
            )
            + "\n</base_project>"
        )
    if error:
        prompt += f"\n\n<concrete_error>\n{error}\n</concrete_error>"
    if state is not None:
        prompt += (
            "\n\n<checkpoint_state>\n"
            + json.dumps(
                state,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            + "\n</checkpoint_state>"
        )
    return prompt


def _rewrite_prompt(
    task: str,
    source: str,
    state: dict,
    error: str,
    functions: dict[str, Callable],
) -> str:
    import json

    from .._generation import planner

    return (
        PLANNER_INSTRUCTIONS.replace("{delivery}", DELIVERY_INSTRUCTIONS).replace(
            "{catalog}", planner._function_catalog(functions)
        )
        + "\n\nRewrite the whole module to fix the concrete failure. Return one "
        "Python code block. Completed call records will replay automatically."
        + f"\n\n<task>\n{task}\n</task>"
        + f"\n\n<current_code>\n{source}\n</current_code>"
        + "\n\n<state_json>\n"
        + json.dumps(state, ensure_ascii=False, indent=2, default=str)
        + "\n</state_json>"
        + f"\n\n<concrete_error>\n{error}\n</concrete_error>"
    )
