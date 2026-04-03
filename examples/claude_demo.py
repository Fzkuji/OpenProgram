"""
Agentic Programming — Real demo with Claude Code as LLM provider.

Shows a task decomposition + execution flow using actual LLM calls.

Usage:
    cd ~/Documents/LLM\ Agent\ Harness/llm-agent-harness
    python3 examples/claude_demo.py
"""

import subprocess
import json
from agentic import agentic_function, Runtime, get_root_context


# ── LLM Provider: Claude Code CLI ───────────────────────────────

def claude_call(content, model="sonnet", response_format=None):
    """Call Claude Code CLI in print mode."""
    # Combine all text blocks into one prompt
    parts = []
    for block in content:
        if block["type"] == "text":
            parts.append(block["text"])

    prompt = "\n".join(parts)

    # Add response format instruction if needed
    if response_format:
        prompt += f"\n\nRespond with ONLY valid JSON matching: {json.dumps(response_format)}"

    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True, text=True, timeout=60,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI error (exit {result.returncode}): {result.stderr[:200] or result.stdout[:200]}")

    return result.stdout.strip()


# ── Runtime ─────────────────────────────────────────────────────

runtime = Runtime(call=claude_call, model="sonnet")


# ── Agentic Functions ──────────────────────────────────────────

@agentic_function
def analyze(task):
    """Break a task into 3 concrete steps. Reply with one step per line, numbered."""
    return runtime.exec(content=[
        {"type": "text", "text": f"Break this task into exactly 3 steps. One line per step, numbered 1-3. Be specific.\n\nTask: {task}"},
    ])


@agentic_function
def execute_step(step):
    """Simulate executing a step. Describe what would happen."""
    return runtime.exec(content=[
        {"type": "text", "text": f"Simulate executing this step. Describe what happens in 1-2 sentences.\n\nStep: {step}"},
    ])


@agentic_function
def summarize(goal):
    """Summarize what was accomplished."""
    return runtime.exec(content=[
        {"type": "text", "text": "Based on the execution context above, summarize what was accomplished in 2 sentences."},
    ])


@agentic_function
def run_task(goal):
    """Plan and execute a task end-to-end."""
    plan = analyze(task=goal)
    print(f"\n📋 Plan:\n{plan}\n")

    # Execute each step
    steps = [line.strip() for line in plan.split("\n") if line.strip() and line.strip()[0].isdigit()]
    for step in steps[:3]:
        result = execute_step(step=step)
        print(f"  ✅ {step[:60]}...")
        print(f"     → {result[:100]}\n")

    return summarize(goal=goal)


# ── Entry Point ────────────────────────────────────────────────

if __name__ == "__main__":
    print("🚀 Running task with Claude Code as LLM provider...\n")

    result = run_task(goal="Set up a Python project with pytest and a CI pipeline")

    print(f"\n📝 Summary:\n{result}")
    print(f"\n🌳 Context Tree:")
    print(get_root_context().tree())
