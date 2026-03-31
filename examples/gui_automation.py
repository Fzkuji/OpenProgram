"""
Example: GUI automation workflow using LLM Agent Harness

This example shows a 4-function workflow for GUI automation:
    observe() → learn() → act() → verify()

Each function is executed by an LLM Session.
The workflow guarantees each function returns a valid typed result
before the next function is called.
"""

from pydantic import BaseModel
from harness import Function, Workflow, FunctionCall
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
    body=open("skills/observe/SKILL.md").read(),
    return_type=ObserveResult,
    params=["task"],
)

learn = Function(
    name="learn",
    docstring="Analyze the observation and determine the best action to take.",
    body=open("skills/learn/SKILL.md").read(),
    return_type=LearnResult,
    params=["task", "observe"],
)

act = Function(
    name="act",
    docstring="Execute the determined action on the target UI element.",
    body=open("skills/act/SKILL.md").read(),
    return_type=ActResult,
    params=["task", "observe", "learn"],
)

verify = Function(
    name="verify",
    docstring="Take a screenshot and verify the action was completed successfully.",
    body=open("skills/verify/SKILL.md").read(),
    return_type=VerifyResult,
    params=["task", "act"],
)


# --- Run the workflow ---

def main():
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
        verify_result = result.context.get("verify", {})
        print(f"Final state: {verify_result.get('current_state')}")
        print(f"Action confirmed: {verify_result.get('action_confirmed')}")
    else:
        print(f"Workflow failed at: {result.failed_function}")
        print(f"Error: {result.error}")


if __name__ == "__main__":
    main()
