"""
agentic.meta_functions — LLM-powered code generation primitives.

Meta functions use LLMs to generate, fix, and scaffold agentic code:

    create()       — Generate a single @agentic_function from a description
    create_app()   — Generate a complete runnable app (runtime + functions + main)
    fix()          — Analyze and rewrite an existing function
    create_skill() — Write a SKILL.md for agent discovery
"""

from agentic.meta_functions.create import create
from agentic.meta_functions.create_app import create_app
from agentic.meta_functions.fix import fix
from agentic.meta_functions.create_skill import create_skill

__all__ = ["create", "create_app", "fix", "create_skill"]
