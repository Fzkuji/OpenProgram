"""
Meta Function Demo — LLM creates new agentic functions at runtime.

Usage:
    PYTHONPATH=. python3 examples/meta_demo.py
"""

import subprocess
import json
from agentic import agentic_function, Runtime
from agentic.meta import create


# ── LLM Provider: Claude Code CLI ───────────────────────────────

def claude_call(content, model="sonnet", response_format=None):
    parts = [b["text"] for b in content if b["type"] == "text"]
    prompt = "\n".join(parts)
    if response_format:
        prompt += f"\n\nRespond with ONLY valid JSON matching: {json.dumps(response_format)}"

    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude error: {result.stderr[:200] or result.stdout[:200]}")
    return result.stdout.strip()


runtime = Runtime(call=claude_call, model="sonnet")


# ── Demo ────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🧬 Meta Function Demo\n")

    # 1. Create a function from description
    print("📝 Creating 'explain_concept' from description...")
    explain = create(
        description="Explain a technical concept in simple terms, using an analogy. Take a 'concept' parameter.",
        runtime=runtime,
        name="explain_concept",
    )
    print(f"   ✅ Created: {explain.__name__}\n")

    # 2. Use the generated function
    print("🔧 Using the generated function...")
    result = explain(concept="prompt caching in LLM APIs")
    print(f"   Result: {result[:200]}\n")

    # 3. Create another function
    print("📝 Creating 'rate_idea' from description...")
    rate = create(
        description="Rate a business idea on a scale of 1-10 with brief reasoning. Take an 'idea' parameter.",
        runtime=runtime,
        name="rate_idea",
    )
    print(f"   ✅ Created: {rate.__name__}\n")

    # 4. Use it
    result2 = rate(idea="A subscription service for AI-generated bedtime stories for kids")
    print(f"   Result: {result2[:200]}\n")

    # 5. Show the full context tree
    print("🌳 Context Tree:")
    print(rate.context.tree())
