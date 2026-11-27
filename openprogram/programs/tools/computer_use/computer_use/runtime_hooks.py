"""What the conversation runtime asks the screen tool, answered.

A turn keeps the screen tool while a target is set up, lists the devices it
can operate beside the message, asks for the call that looks at the
conversation's own Pages, and releases what a run kept when it ends. The
runtime sits below Programs and never imports this tool: it owns the
questions (``openprogram.runtime.conversation.screen_tool``) and the moment
a run's resources are released (``turn_hooks``' ``execution_released``),
and this module answers them when the tool loads. Each answer reads the
tool's module when it is called, so a test that replaces a function there
is what the turn sees.
"""
from __future__ import annotations


def _available_targets() -> list[str]:
    from openprogram.programs.tools.computer_use.computer_use import devices

    return devices.available_targets()


def _devices(session_id: str) -> dict[str, str]:
    from openprogram.programs.tools.computer_use.computer_use import devices

    return devices.devices(session_id)


def _devices_note(previous, current) -> str:
    from openprogram.programs.tools.computer_use.computer_use import devices

    return devices.devices_note(previous, current)


def _page_surface_call() -> dict | None:
    from openprogram.programs.tools.computer_use.computer_use import tool

    return tool.page_surface_call()


def _execution_released(session_id: str, execution_id: str) -> None:
    """What computer use kept for the run (its code interpreter, an agent's tabs and element ids) ends."""
    from openprogram.programs.tools.computer_use.computer_use import tool

    tool.run_ended(session_id, execution_id)


def register() -> None:
    from openprogram.runtime.conversation import screen_tool, turn_hooks

    screen_tool.register(
        available_targets=_available_targets,
        devices=_devices,
        devices_note=_devices_note,
        page_surface_call=_page_surface_call,
    )
    turn_hooks.register("execution_released", "computer_use", _execution_released)
