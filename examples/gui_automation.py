"""
Example: GUI automation workflow using LLM Agent Harness

This example shows a 4-step workflow for GUI automation:
    OBSERVE → LEARN → ACT → VERIFY

Each step is a typed function executed by an LLM session.
The framework guarantees each step produces valid structured output
before the next step begins.
"""

from pydantic import BaseModel
from harness import Step, Workflow
from harness.session import AnthropicSession
from harness.workflow import StepConfig


# --- Output schemas (the "return types" of each Step) ---

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


# --- Step definitions ---

observe_step = Step(
    name="observe",
    description="Observe the current screen state and identify UI elements.",
    instructions="""
Take a screenshot and analyze what you see on the screen.
Identify all visible UI elements (buttons, input fields, menus, text).
Determine whether the target element for the task is currently visible.
Be precise about element locations and states.
""",
    output_schema=ObserveResult,
)

learn_step = Step(
    name="learn",
    description="Analyze the observation and determine the best action to take.",
    instructions="""
Based on the current screen state and the task, determine:
1. What type of application is being used
2. Which specific element should be interacted with
3. What action should be performed (click, type, scroll, etc.)

Use any prior knowledge about this application if available.
""",
    output_schema=LearnResult,
    reads=["task", "observe"],  # Only reads what it needs
)

act_step = Step(
    name="act",
    description="Execute the determined action on the target UI element.",
    instructions="""
Execute the recommended action on the target element.
Use the available GUI tools to perform the action.
Record exactly what was done and where.
""",
    output_schema=ActResult,
    reads=["task", "observe", "learn"],
)

verify_step = Step(
    name="verify",
    description="Take a screenshot and verify the action was completed successfully.",
    instructions="""
Take a new screenshot and compare with the previous state.
Confirm whether the intended action was completed successfully.
Note any changes in the UI state.
""",
    output_schema=VerifyResult,
    reads=["task", "act"],
)


# --- Run the workflow ---

def main():
    session = AnthropicSession()

    workflow = Workflow(
        steps=[
            StepConfig(step=observe_step),
            StepConfig(step=learn_step),
            StepConfig(step=act_step),
            StepConfig(step=verify_step),
        ],
        default_session=session,
    )

    result = workflow.run(task="Click the login button")

    if result.success:
        print("Workflow completed successfully")
        print(f"Final state: {result.context.get('verify', {}).get('current_state')}")
    else:
        print(f"Workflow failed at step: {result.failed_step}")
        print(f"Error: {result.error}")


if __name__ == "__main__":
    main()
