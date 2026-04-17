"""
agentic.meta_functions — LLM-powered code generation primitives.

Meta functions use LLMs to generate, edit, and scaffold agentic code:

    create()       — Generate a single @agentic_function from a description
    create_app()   — Generate a complete runnable app (runtime + functions + main)
    edit()         — Analyze and rewrite an existing function
    improve()      — Optimize an existing function based on a goal
    create_skill() — Write a SKILL.md for agent discovery

All code-generation meta functions delegate to generate_code() in _helpers.py,
which contains the complete Agentic Programming design specification.
"""

from agentic.meta_functions.create import create
from agentic.meta_functions.create_app import create_app
from agentic.meta_functions.edit import edit
from agentic.meta_functions.improve import improve
from agentic.meta_functions.create_skill import create_skill

# Backward-compatible alias used throughout the docs and examples.
fix = edit

__all__ = ["create", "create_app", "edit", "fix", "improve", "create_skill"]
