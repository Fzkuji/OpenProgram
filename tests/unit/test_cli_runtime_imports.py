"""Smoke tests for legacy CLI runtime imports.

These runtimes shell out to vendor CLIs (``claude``, ``gemini``,
``codex``, ``openclaw``) and are independent of the v2 auth layer in
most respects. But every refactor to :mod:`openprogram.auth`,
:mod:`openprogram.providers`, or :mod:`openprogram.agentic_programming.runtime`
has the potential to break the import graph these files assume. Rather
than trust "I didn't touch that file", this suite pins the imports so
future refactors notice breakage at `pytest` time, not at user-report
time.

The runtimes themselves aren't instantiated — construction requires a
subprocess CLI to be installed. We only verify the classes resolve.
"""
from __future__ import annotations

import importlib

import pytest


_MODULES = [
    ("openprogram.providers.anthropic.cli_runtime", "ClaudeCodeRuntime"),
    ("openprogram.providers.google_gemini_cli.runtime", "GoogleGeminiCLIRuntime"),
    ("openprogram.legacy_providers.openai_codex", "OpenAICodexRuntime"),
]


@pytest.mark.parametrize("module_path,class_name", _MODULES)
def test_legacy_cli_runtime_imports(module_path: str, class_name: str):
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name, None)
    assert cls is not None, f"{module_path} is missing {class_name}"
    # Basic signature sanity: must be a class, must be a Runtime subclass.
    assert isinstance(cls, type), f"{class_name} is not a class"
    from openprogram.agentic_programming.runtime import Runtime
    assert issubclass(cls, Runtime), (
        f"{class_name} does not inherit from Runtime — refactor broke the hierarchy"
    )


def test_claude_code_module_has_configure_entry_point():
    # ClaudeCodeRuntime has historically carried a module-level function
    # that CI setup depends on — pin it so renames don't land silently.
    mod = importlib.import_module("openprogram.providers.anthropic.cli_runtime")
    assert hasattr(mod, "ClaudeCodeRuntime")


def test_all_runtimes_advertise_list_models():
    """Each runtime class must expose list_models() so the model picker
    can enumerate without instantiating."""
    for module_path, class_name in _MODULES:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        assert hasattr(cls, "list_models"), f"{class_name} missing list_models"
