"""
Agentic Programming — a programming paradigm where LLM sessions are the compute units.

Core exports:
    function    Decorator to define LLM-executed functions
    Session     LLM call interface (Anthropic, OpenAI, Claude Code, Codex, OpenClaw)
    Scope       Context visibility rules
    Memory      Persistent execution log

Built-in functions:
    ask         Ask the LLM a question → plain text
    extract     Extract structured data from text → Pydantic model
    summarize   Summarize text → plain text
    classify    Classify text into categories → category name
    decide      Choose from options → chosen option
"""

from harness.function import (
    function,
    FunctionError,
    ask,
    extract,
    summarize,
    classify,
    decide,
)
from harness.session import Session
from harness.scope import Scope
from harness.memory import Memory, Event

__all__ = [
    # Decorator
    "function",
    "FunctionError",
    # Built-in functions
    "ask",
    "extract",
    "summarize",
    "classify",
    "decide",
    # Session
    "Session",
    # Scope
    "Scope",
    # Memory
    "Memory",
    "Event",
]
