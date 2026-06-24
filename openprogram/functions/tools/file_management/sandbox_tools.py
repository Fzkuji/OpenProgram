"""Agent tools for system-level sandbox management."""
from __future__ import annotations

from openprogram.functions._runtime import function


@function(
    name="sandbox_status",
    description=(
        "Check the system-level sandbox status: whether it's available "
        "on this platform (macOS Seatbelt / Linux bubblewrap) and "
        "whether it's currently enabled for this session."
    ),
    toolset=["core"],
)
def sandbox_status() -> str:
    from openprogram.sandbox import sandbox_enabled, is_available

    available = is_available()
    enabled = sandbox_enabled.get(False)

    import sys
    platform = "macOS (Seatbelt)" if sys.platform == "darwin" else "Linux (bubblewrap)"

    if not available:
        return (
            f"[sandbox_status] NOT available on this system ({platform}). "
            f"Bash commands run without file-system restrictions."
        )

    state = "ON" if enabled else "OFF"
    return (
        f"[sandbox_status] {state} | platform={platform} | "
        f"When ON, bash commands can only write to the current project directory."
    )


@function(
    name="sandbox_toggle",
    description=(
        "Toggle the system-level sandbox on or off. When enabled, "
        "bash commands are restricted to reading/writing only the "
        "current project directory. Other file paths are blocked at "
        "the OS kernel level.\n\n"
        "Args:\n"
        "  enable: true to enable, false to disable. If omitted, "
        "toggles the current state."
    ),
    toolset=["core"],
)
def sandbox_toggle(enable: bool = None) -> str:
    from openprogram.sandbox import sandbox_enabled, is_available

    if not is_available():
        return (
            "[sandbox_toggle error] sandbox not available on this system. "
            "macOS needs sandbox-exec, Linux needs bubblewrap (bwrap)."
        )

    current = sandbox_enabled.get(False)
    if enable is None:
        enable = not current

    sandbox_enabled.set(enable)
    state = "ON" if enable else "OFF"
    return f"[sandbox_toggle] sandbox is now {state}"
