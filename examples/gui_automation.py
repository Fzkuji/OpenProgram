"""
Example: GUI automation using Agentic Programming

Two modes:
    1. Static (Workflow) — fixed sequence: observe → learn → act → verify
    2. Dynamic (Programmer) — Programmer decides what to do based on results
"""

from pydantic import BaseModel
from harness import Function, Scope, Workflow, FunctionCall, Programmer, Runtime
from harness.session import AnthropicSession


# --- Return types ---

class ObserveResult(BaseModel):
    current_state: str
    elements_found: list[str]
    is_target_visible: bool


class LearnResult(BaseModel):
    app_type: str
    target_element: str
    recommended_action: str


class ActResult(BaseModel):
    action_taken: str
    coordinates: dict
    success: bool


class VerifyResult(BaseModel):
    action_confirmed: bool
    current_state: str
    notes: str


# --- Function definitions ---

observe = Function(
    name="observe",
    docstring="Observe the current screen state and identify UI elements.",
    body=open("examples/skills/observe/SKILL.md").read(),
    return_type=ObserveResult,
    params=["task"],
    scope=Scope.isolated(),       # pure observation, no prior context needed
)

learn = Function(
    name="learn",
    docstring="Analyze the observation and determine the best action to take.",
    body=open("examples/skills/learn/SKILL.md").read(),
    return_type=LearnResult,
    params=["task", "observe"],
    scope=Scope.chained(),        # sees observe's I/O summary
)

act = Function(
    name="act",
    docstring="Execute the determined action on the target UI element.",
    body=open("examples/skills/act/SKILL.md").read(),
    return_type=ActResult,
    params=["task", "observe", "learn"],
    scope=Scope.chained(),        # sees observe + learn I/O
)

verify = Function(
    name="verify",
    docstring="Take a screenshot and verify the action was completed successfully.",
    body=open("examples/skills/verify/SKILL.md").read(),
    return_type=VerifyResult,
    params=["task", "act"],
    scope=Scope.isolated(),       # independent verification, clean context
)


# --- Mode 1: Static Workflow ---

def run_static():
    """Fixed sequence, no decision-making."""
    session = AnthropicSession()

    workflow = Workflow(
        calls=[
            FunctionCall(function=observe),
            FunctionCall(function=learn),
            FunctionCall(function=act),
            FunctionCall(function=verify),
        ],
        default_session=session,
    )

    result = workflow.run(task="Click the login button")

    if result.success:
        print("Workflow completed successfully")
        print(f"Final state: {result.context.get('verify', {})}")
    else:
        print(f"Workflow failed at: {result.failed_function}")
        print(f"Error: {result.error}")


# --- Mode 2: Dynamic Programmer ---

def run_dynamic():
    """Programmer decides what to do based on results."""
    programmer = Programmer(
        session=AnthropicSession(model="claude-sonnet-4-6"),
        runtime=Runtime(
            session_factory=lambda: AnthropicSession(model="claude-haiku")
        ),
        functions=[observe, learn, act, verify],
    )

    result = programmer.run("Open Safari and search for 'hello world' on Google")

    if result.success:
        print(f"Task completed in {result.iterations} iterations")
        if result.reply:
            print(f"Programmer says: {result.reply}")
    else:
        print(f"Task failed: {result.failure_reason}")


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "static"
    if mode == "dynamic":
        run_dynamic()
    else:
        run_static()
