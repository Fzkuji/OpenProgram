"""
create_skill() — Generate a SKILL.md for agent discovery from a function.
"""

from __future__ import annotations

import os

from agentic.function import agentic_function
from agentic.runtime import Runtime


@agentic_function
def create_skill(fn_name: str, description: str, code: str, runtime: Runtime) -> str:
    """Write a SKILL.md for an OpenClaw skill based on the given function.

    The SKILL.md must follow this exact format:
    ---
    name: <fn_name>
    description: "<one-line for agent discovery, include trigger words>"
    ---
    # <Title>
    ## Usage
    agentic run <fn_name> --arg key=value
    ## Parameters
    <Table of parameters>

    Rules:
    - Description must include trigger words (when should an agent use this?).
    - Usage must use `agentic run` CLI command, not Python code.
    - If the function uses LLM (runtime.exec), note that Claude Code CLI is needed.
    - Keep concise — agents read this every message.
    - Write ONLY the SKILL.md content, no explanation.

    Args:
        fn_name:      Function name.
        description:  What the function does.
        code:         Function source code.
        runtime:      Runtime for LLM calls.

    Returns:
        Path to the created SKILL.md.
    """
    response = runtime.exec(content=[
        {"type": "text", "text": f"Function: {fn_name}\nSource:\n```python\n{code}\n```"},
    ])

    # Extract content (strip markdown fences if any)
    skill_content = response.strip()
    if skill_content.startswith("```"):
        lines = skill_content.split("\n")
        skill_content = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    skill_dir = os.path.join(repo_root, "skills", fn_name)
    os.makedirs(skill_dir, exist_ok=True)

    filepath = os.path.join(skill_dir, "SKILL.md")
    with open(filepath, "w") as f:
        f.write(skill_content)

    return filepath
