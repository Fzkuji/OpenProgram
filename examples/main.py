"""
Agentic Programming — Example entry point.

This example shows a simple GUI automation flow:
observe → click → verify, orchestrated by a top-level login_flow.

Usage:
    GEMINI_API_KEY=your_key python examples/main.py
"""

import os
import google.generativeai as genai
from agentic import agentic_function, Runtime, get_root_context


# ── LLM Provider ────────────────────────────────────────────────

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))


def gemini_call(content, model="gemini-2.5-flash", response_format=None):
    """Convert content list → Gemini API call → reply text."""
    parts = []
    for block in content:
        if block["type"] == "text":
            parts.append(block["text"])
        elif block["type"] == "image":
            # For real usage: load image bytes and pass as PIL Image
            parts.append(f"[Image: {block['path']}]")

    response = genai.GenerativeModel(model).generate_content("\n".join(parts))
    return response.text


# ── Runtime ─────────────────────────────────────────────────────

rt = Runtime(call=gemini_call, model="gemini-2.5-flash")


# ── Agentic Functions ──────────────────────────────────────────

@agentic_function
def observe(task):
    """Look at the screen and describe what you see."""
    return rt.exec(content=[
        {"type": "text", "text": f"Describe what you see. Task: {task}"},
    ])


@agentic_function
def click(element):
    """Click an element on the screen."""
    return rt.exec(content=[
        {"type": "text", "text": f"Click the element: {element}. Describe the result."},
    ])


@agentic_function
def verify(expected):
    """Verify the current state matches expectations."""
    return rt.exec(content=[
        {"type": "text", "text": f"Verify: are we on the {expected} page? Answer yes or no with reason."},
    ])


@agentic_function
def login_flow(username, password):
    """Complete login flow: observe, click login, verify dashboard."""
    observe(task="find the login form")
    click(element="login button")
    return verify(expected="dashboard")


# ── Entry Point ────────────────────────────────────────────────

if __name__ == "__main__":
    result = login_flow(username="admin", password="secret")

    print("\n── Result ──")
    print(result)

    print("\n── Context Tree ──")
    print(get_root_context().tree())
