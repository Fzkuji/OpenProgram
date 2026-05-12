#!/usr/bin/env python3
"""
Agentic Programming — Quickstart Example

This is a complete, runnable script that demonstrates the core concepts.
No API key needed — uses Claude Code CLI (requires `claude` to be installed).

Run:
    cd Agentic-Programming
    pip install -e .
    python examples/quickstart.py

Prerequisites:
    npm install -g @anthropic-ai/claude-code
    claude login
"""

from openprogram import agentic_function
from openprogram.providers.registry import ClaudeCodeRuntime

# ── Step 1: Create a runtime (no API key needed) ────────────────

runtime = ClaudeCodeRuntime(model="haiku")


# ── Step 2: Define agentic functions ─────────────────────────────

@agentic_function
def identify_concepts(topic):
    """Identify the 3 most important concepts in a topic."""
    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Identify the 3 most important concepts in: {topic}\n"
            "List them numbered 1-3, one per line. Just the concept name, no explanation."
        )},
    ])


@agentic_function
def explain_concept(concept):
    """Explain a concept in one clear, accessible sentence."""
    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Explain '{concept}' in exactly one clear sentence "
            "that a smart high-schooler would understand."
        )},
    ])


@agentic_function
def synthesize(topic):
    """Create a mini-lesson: identify key concepts, then explain each one."""

    # Python controls the flow: first identify, then explain each
    concepts_text = identify_concepts(topic=topic)
    print(f"📚 Key concepts in '{topic}':\n{concepts_text}\n")

    # Python controls the loop: iterate over concepts
    concepts = [
        line.strip().lstrip("0123456789.").strip()
        for line in concepts_text.split("\n")
        if line.strip() and line.strip()[0].isdigit()
    ]

    explanations = []
    for concept in concepts[:3]:
        explanation = explain_concept(concept=concept)
        explanations.append(f"  • {concept}: {explanation}")
        print(f"  💡 {concept}: {explanation}\n")

    # LLM sees full context automatically (what was identified + explained)
    return runtime.exec(content=[
        {"type": "text", "text": (
            "Based on the concepts and explanations above, write a 2-sentence "
            "takeaway that connects them together."
        )},
    ])


# ── Step 3: Run it ──────────────────────────────────────────────

if __name__ == "__main__":
    print("🧬 Agentic Programming — Quickstart\n")
    print("=" * 50)

    result = synthesize(topic="how neural networks learn")

    print("=" * 50)
    print(f"\n🎯 Takeaway:\n{result}\n")

    # Show the execution tree — this is what makes Agentic Programming special
    print("🌳 Execution tree (auto-tracked by @agentic_function):")
    print(synthesize.context.tree())

    print("\n✅ Done! You just ran your first agentic workflow.")
    print("   Python controlled the flow. The LLM did the reasoning.")
    print("   The context tree tracked everything automatically.")
