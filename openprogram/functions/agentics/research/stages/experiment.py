"""
experiment — experiment design, execution, and monitoring stage.

Designs experiments, generates code, runs them, and monitors progress.
"""

from __future__ import annotations

import os
from typing import Optional

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime


@agentic_function(render_range={"callers": 0})
def design_experiments(idea: str, runtime: Runtime) -> str:
    """Design a complete experiment plan for a research idea.

    Design a rigorous experiment plan. It must include:
    1. Research Questions (RQ1, RQ2, RQ3...)
    2. Datasets: which ones, why, train/val/test splits
    3. Baselines: recent methods (within 2 years), justify each
    4. Evaluation Metrics: which metrics, why they're appropriate
    5. Ablation Study: which components to ablate
    6. Implementation Details: framework, hardware, hyperparameter ranges
    7. Expected Experiment Types:
       - Overall Performance (all datasets × all baselines)
       - Ablation Study (remove key modules)
       - Parameter Analysis (vary hyperparameters)
       - Efficiency Study (time/space)
       - Case Study / Visualization

    Each experiment should map to a specific research question.
    Be specific about what to measure and how to interpret results.

    Output: Structured markdown experiment plan.
    """
    return runtime.exec(content=[
        {"type": "text", "text": idea},
    ])


@agentic_function
def run_experiment(plan: str, step: str, runtime: Runtime) -> str:
    """Execute one step of the experiment plan.

    You have full freedom to write code, run commands, install packages,
    and manage files. Do whatever is needed to execute this step.

    After execution, report:
    - What you did
    - Results obtained (exact numbers)
    - Any issues encountered
    - What to do next

    Output: Execution report with results.
    """
    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Experiment plan:\n{plan}\n\n"
            f"Current step:\n{step}"
        )},
    ])


@agentic_function(render_range={"callers": 0})
def check_training(log: str, runtime: Runtime) -> str:
    """Check training logs for issues.

    Analyze the training log and report:
    - Is training progressing normally? (loss decreasing, metrics improving)
    - Any signs of overfitting? (train/val divergence)
    - Any NaN/Inf values?
    - Estimated time to completion?
    - Recommendation: continue / stop early / adjust hyperparameters?

    Output JSON:
    {"status": "healthy/warning/critical",
     "issues": ["list of issues"],
     "recommendation": "what to do next"}
    """
    return runtime.exec(content=[
        {"type": "text", "text": log},
    ])


def run_experiments(
    project_dir: str,
    runtime: Runtime,
) -> dict:
    """Run the experiment stage.

    Reads the idea report, designs experiments, and starts execution.

    Args:
        project_dir:  Project directory.
        runtime:      LLM runtime.

    Returns:
        dict with experiment plan and execution status.
    """
    project_dir = os.path.expanduser(project_dir)

    # Read idea
    idea_path = os.path.join(project_dir, "IDEA_REPORT.md")
    if os.path.exists(idea_path):
        with open(idea_path, "r", encoding="utf-8") as f:
            idea = f.read()
    else:
        import warnings
        warnings.warn(
            f"IDEA_REPORT.md not found at {idea_path}. "
            "Run the 'idea' stage first for better experiment design.",
            stacklevel=2,
        )
        idea = "No idea report found. Design experiments based on project context."
        # Fallback to outline
        outline_path = os.path.join(project_dir, "outline", "outline.md")
        if os.path.exists(outline_path):
            with open(outline_path, "r", encoding="utf-8") as f:
                idea = f.read()

    # Design
    plan = design_experiments(idea=idea, runtime=runtime)

    # Save plan
    exp_dir = os.path.join(project_dir, "experiments")
    os.makedirs(exp_dir, exist_ok=True)
    with open(os.path.join(exp_dir, "EXPERIMENT_PLAN.md"), "w", encoding="utf-8") as f:
        f.write(f"# Experiment Plan\n\n{plan}")

    return {"plan": plan, "status": "planned"}
