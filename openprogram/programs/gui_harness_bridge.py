"""Optional adapter from the installed GUI Agent Harness to web_use."""
from __future__ import annotations

from typing import Callable

DEFAULT_MAX_STEPS = 150


def _normalize_gui_result(result):
    if not isinstance(result, dict):
        return result
    normalized = dict(result)
    status = str(normalized.get("status") or "")
    if normalized.get("infeasible_declared"):
        status = "infeasible"
    elif not status:
        if isinstance(normalized.get("success"), bool):
            status = "succeeded" if normalized["success"] else "failed"
    if not status:
        return normalized
    normalized["status"] = status
    normalized["success"] = status == "succeeded"
    reason_code = str(normalized.get("reason_code") or "").strip()
    if not reason_code or (
        status == "infeasible"
        and reason_code in {"completed", "succeeded", "verified"}
    ):
        normalized["reason_code"] = (
            "completed" if status == "succeeded" else status
        )
    if status == "infeasible":
        normalized["infeasible_declared"] = True
        if not str(normalized.get("handoff_instruction") or "").strip():
            normalized["handoff_instruction"] = str(
                normalized.get("summary") or ""
            )
    else:
        normalized.setdefault("infeasible_declared", False)
        normalized.setdefault("handoff_instruction", "")
    return normalized


def install_gui_harness_web_use(original: Callable | None = None):
    """Replace the registered gui_agent entry with a backend-aware wrapper."""
    if original is None:
        from gui_harness.main import gui_agent as original
    original_impl = getattr(original, "__wrapped__", original)

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
                "advanced": True,
            },
            "app_name": {
                "description": "Desktop app name used for visual memory",
                "hidden": True,
                "advanced": True,
            },
            "surface": {
                "description": "Execute on the desktop or the selected built-in browser Page",
                "options": ["desktop", "browser"],
            },
            "backend": {
                "description": "Optional built-in Page web_use backend",
                "options": [
                    "playwright_mcp", "chrome_devtools_mcp",
                    "open_claude_chrome",
                ],
                "hidden": True,
                "advanced": True,
            },
            "max_seconds": {
                "description": "Wall-clock limit",
                "hidden": True,
                "advanced": True,
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
                "surface": {
                    "type": "string",
                    "enum": ["desktop", "browser"],
                    "description": "Use browser for OpenProgram's selected built-in Page",
                    "default": "desktop",
                },
            },
            "required": ["task"],
        },
    )
    def gui_agent(
        task: str,
        max_steps: int | None = None,
        app_name: str = "desktop",
        surface: str = "desktop",
        backend: str = "",
        max_seconds: float | None = None,
        runtime=None,
        allow_general: bool = False,
    ) -> dict:
        """Run the installed GUI harness or its exact-Page web_use mode."""
        if max_steps is None:
            steps: int | None = DEFAULT_MAX_STEPS
        else:
            n = int(max_steps)
            steps = n if n > 0 else None
        seconds = (
            None
            if max_seconds is None or float(max_seconds) <= 0
            else float(max_seconds)
        )
        selected_surface = str(surface or "desktop").strip().lower()
        if selected_surface not in {"desktop", "browser"}:
            return _normalize_gui_result({
                "status": "failed",
                "reason_code": "invalid_surface",
                "summary": f"Unknown GUI surface: {surface}",
            })
        # ``surface`` is the public routing key. An explicit backend keeps
        # compatibility with callers that predate the surface parameter.
        browser_mode = bool(backend) or selected_surface == "browser"
        if not browser_mode:
            result = original_impl(
                task=task,
                max_steps=steps if steps is not None else 0,
                app_name=app_name,
                runtime=runtime,
                allow_general=allow_general,
            )
            return _normalize_gui_result(result)
        from openprogram.programs.workflow.browser import (
            _run_browser_task_commands,
        )
        from openprogram.programs.workflow.browser.web_use_runtime import (
            DEFAULT_BACKEND,
        )
        selected_backend = backend or DEFAULT_BACKEND
        return _normalize_gui_result(_run_browser_task_commands(
            task=task,
            backend=selected_backend,
            max_steps=steps,
            max_seconds=seconds,
            runtime=runtime,
        ))

    # ``programs run`` resolves a registered function's module and then looks
    # up the public function name on that module.  The wrapper is defined here
    # so it can close over the installed harness implementation; publish that
    # same decorated callable instead of adding a second execution wrapper.
    globals()["gui_agent"] = gui_agent
    return gui_agent


__all__ = ["DEFAULT_MAX_STEPS", "install_gui_harness_web_use"]
