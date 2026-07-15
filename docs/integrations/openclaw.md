# OpenClaw

## What Is This?

This guide shows how to use **Agentic Programming** within [OpenClaw](https://github.com/nicepkg/openclaw) — as a skill, a utility library, or an MCP tool provider.

Agentic Programming and OpenClaw solve different problems:
- **OpenClaw** orchestrates agents, manages sessions, routes messages
- **Agentic Programming** gives individual functions the ability to think (LLM-in-the-loop)

They compose naturally: OpenClaw's skills can use agentic functions internally.

## Setup

```bash
# In your OpenClaw workspace
cd ~/.openclaw/workspace

# Clone OpenProgram
git clone https://github.com/Fzkuji/OpenProgram.git

# Install it
cd OpenProgram
pip install -e .
```

## Usage Pattern 1: Agentic Functions Inside a Skill

The simplest integration — use agentic functions as building blocks within an OpenClaw skill.

**Skill structure:**
```
~/.openclaw/workspace/skills/my-agentic-skill/
├── SKILL.md
└── scripts/
    └── analyze.py
```

**`scripts/analyze.py`:**
```python
#!/usr/bin/env python3
"""
OpenClaw skill script that uses Agentic Programming internally.
Called by the agent via the exec tool.
"""
import sys
import os

# Add OpenProgram to the path (not needed if pip install -e was run)
sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/OpenProgram"))

from openprogram import agentic_function
from openprogram.providers import ClaudeCodeRuntime

runtime = ClaudeCodeRuntime(model="haiku")


@agentic_function
def decompose(task):
    """Break a complex task into actionable steps."""
    return runtime.exec(content=[
        {"type": "text", "text": f"Break this task into 3-5 concrete, actionable steps:\n{task}\n\nNumber each step. Be specific."},
    ])


@agentic_function
def assess(step):
    """Assess difficulty and time estimate for a step."""
    return runtime.exec(content=[
        {"type": "text", "text": f"For this step, give: difficulty (easy/medium/hard) and time estimate.\nFormat: [difficulty] ~Xh\n\nStep: {step}"},
    ])


@agentic_function
def plan(task):
    """Create a detailed plan for a task."""
    steps_text = decompose(task=task)

    lines = [l.strip() for l in steps_text.split("\n") if l.strip() and l.strip()[0].isdigit()]
    assessments = []
    for line in lines[:5]:
        a = assess(step=line)
        assessments.append(f"{line}\n   → {a}")

    return "\n\n".join(assessments)


if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Build a REST API with authentication"
    result = plan(task=task)
    print(result)
```

**`SKILL.md`:**
```markdown
# my-agentic-skill

Plan and decompose tasks using Agentic Programming with automatic context tracking.

## Usage

When the user asks to plan, decompose, or break down a task, run:

\`\`\`bash
python3 ~/.openclaw/workspace/skills/my-agentic-skill/scripts/analyze.py "the task description"
\`\`\`
```

## Usage Pattern 2: As a Python Library in Agent Scripts

If your OpenClaw agent runs Python scripts, you can import agentic functions directly:

```python
"""
Code review script called by an OpenClaw agent.
"""
from openprogram import agentic_function
from openprogram.providers import ClaudeCodeRuntime

runtime = ClaudeCodeRuntime(model="haiku")


@agentic_function
def review_code(code, language="python"):
    """Review code for bugs, style issues, and improvements."""
    return runtime.exec(content=[
        {"type": "text", "text": f"Review this {language} code. List:\n1. Bugs (if any)\n2. Style issues\n3. Suggested improvements\n\n```{language}\n{code}\n```"},
    ])


@agentic_function
def suggest_tests(code):
    """Suggest test cases for the given code."""
    return runtime.exec(content=[
        {"type": "text", "text": f"Suggest 3 test cases for this code. For each, give: test name, input, expected output.\n\n```python\n{code}\n```"},
    ])


@agentic_function
def code_analysis(code):
    """Full code analysis: review + test suggestions."""
    review = review_code(code=code)
    tests = suggest_tests(code=code)
    return f"## Code Review\n{review}\n\n## Suggested Tests\n{tests}"
```

## Usage Pattern 3: MCP Tool Wrapper

Wrap agentic functions as MCP tools that OpenClaw can call:

```python
#!/usr/bin/env python3
"""
MCP-compatible tool server that exposes agentic functions.
"""
import json
import sys

from openprogram import agentic_function
from openprogram.providers import ClaudeCodeRuntime

runtime = ClaudeCodeRuntime(model="haiku")


@agentic_function
def summarize_text(text, style="bullet_points"):
    """Summarize text in the specified style."""
    style_instructions = {
        "bullet_points": "Summarize as 3-5 bullet points.",
        "one_paragraph": "Summarize in one paragraph.",
        "eli5": "Explain like I'm 5.",
    }
    instruction = style_instructions.get(style, style_instructions["bullet_points"])

    return runtime.exec(content=[
        {"type": "text", "text": f"{instruction}\n\nText:\n{text}"},
    ])


if __name__ == "__main__":
    request = json.loads(sys.stdin.read())
    tool = request.get("tool")
    args = request.get("args", {})

    if tool == "summarize":
        result = summarize_text(**args)
        print(json.dumps({"result": result}))
    else:
        print(json.dumps({"error": f"Unknown tool: {tool}"}))
```

## Why Use Agentic Programming in OpenClaw?

| Without Agentic Programming | With Agentic Programming |
|---|---|
| Agent does all reasoning in one LLM call | Reasoning is split into focused function calls |
| Context grows unboundedly | Context is a structured DAG, scoped per function |
| Hard to debug what the agent "thought" | Every call is recorded as a session DAG node, reviewable in the Web UI or the session files |
| Retry = retry the entire agent turn | Retry = retry just the failed function |

## Tips

1. **Start with `ClaudeCodeRuntime`** — no extra API key needed; a Claude Code login is enough, and it runs on your subscription. See [Claude Code integration](claude-code.md).
2. **Pick the runtime by billing** — `ClaudeCodeRuntime` runs on your Claude subscription, `AnthropicRuntime` bills against an Anthropic API key.
3. **Review execution traces** — every function call is recorded in the session DAG; find the session with the Web UI or `openprogram sessions list` and review it there.
4. **Keep functions small and focused** — each `@agentic_function` should do one thing; let Python compose them.
