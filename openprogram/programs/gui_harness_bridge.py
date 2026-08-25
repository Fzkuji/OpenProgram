"""Optional adapter from the installed GUI Agent Harness to web_use."""
from __future__ import annotations

from typing import Callable


def install_gui_harness_web_use(original: Callable | None = None):
    """Replace the registered gui_agent entry with a backend-aware wrapper."""
    if original is None:
        from gui_harness.main import gui_agent as original

    from openprogram.agentic_programming.function import agentic_function

    @agentic_function(
        name="gui_agent",
        as_tool=True,
        toolset=("harness",),
        input={
            "task": {
                "source": "llm",
                "description": "What to do",
                "multiline": True,
            },
            "max_steps": {
                "description": "Maximum number of actions",
                "hidden": True,
            },
            "app_name": {
                "description": "Desktop app or browser",
                "hidden": True,
            },
            "backend": {
                "description": "Optional built-in Page web_use backend",
                "options": [
                    "playwright_mcp", "chrome_devtools_mcp",
                    "open_claude_chrome",
                ],
                "hidden": True,
            },
            "max_seconds": {
                "description": "Wall-clock limit",
                "hidden": True,
            },
            "allow_general": {"hidden": True},
            "runtime": {"hidden": True},
        },
        parameters={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "What to do",
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Maximum number of actions",
                },
                "app_name": {
                    "type": "string",
                    "description": "Desktop app or browser",
                },
                "backend": {
                    "type": "string",
                    "description": "Optional built-in Page web_use backend",
                    "enum": [
                        "playwright_mcp", "chrome_devtools_mcp",
                        "open_claude_chrome",
                    ],
                },
                "max_seconds": {
                    "type": "integer",
                    "description": "Wall-clock limit",
                },
                "allow_general": {"type": "boolean"},
            },
            "required": ["task"],
        },
    )
    def gui_agent(
        task: str,
        max_steps: int = 15,
        app_name: str = "desktop",
        backend: str = "",
        max_seconds: int = 300,
        runtime=None,
        allow_general: bool = False,
    ) -> dict:
        """Run the installed GUI harness or its exact-Page web_use mode."""
        if not backend:
            return original(
                task=task,
                max_steps=max_steps,
                app_name=app_name,
                runtime=runtime,
                allow_general=allow_general,
            )
        from openprogram.programs.workflow.browser import (
            _run_browser_task_commands,
        )
        return _run_browser_task_commands(
            task=task,
            backend=backend,
            max_steps=max_steps,
            max_seconds=max_seconds,
            runtime=runtime,
        )

    return gui_agent


__all__ = ["install_gui_harness_web_use"]
