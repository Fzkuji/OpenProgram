"""What can be operated now (``<devices>``), beside the conversation's messages rather than in the tool.

The tool's description never changes, so connecting a device leaves the
prompt cache alone. The whole list goes on a turn with no earlier note of it
in view, then what was added, changed and removed (``devices`` and
``devices_note``), the way the deferred-tool catalog goes. Another computer
that its connection checks cannot reach says so in its block
(``openprogram.host.machines.status.unreachable``); its return is a Changed
entry.
"""
from __future__ import annotations

import platform
from typing import Optional

from .routing import registry
from .contract import Provider
from .type_guides import _register_guides
from . import context


THIS_COMPUTER = "this computer"
_DEVICES_TAG = "devices"
# The line a device's block carries while it cannot be reached; the line its return adds to its Changed entry.
_UNREACHABLE = "  Cannot be reached right now"
_BACK = "  Reachable again: calls to it go through."
_REASON_CHARS = 80


def _unreachable_line(machine) -> str:
    """For another computer the debounced checks call unreachable, the line saying so; otherwise "".

    Reads what the existing checks found and never waits on the network: a failing device whose last
    check is stale is checked again behind the answer, so its return is noticed.
    """
    try:
        from openprogram.host.machines import status
        reason = status.unreachable(machine, refresh=True)
    except Exception:
        return ""
    if reason is None:
        return ""
    reason = " ".join(str(reason).split())
    if len(reason) > _REASON_CHARS:
        reason = reason[:_REASON_CHARS - 1].rstrip() + "…"
    return (f"{_UNREACHABLE} (last check: {reason}); calls to it will most likely fail. A later message says "
            "when it is back.")


def _this_computer() -> str:
    return {"Darwin": "the user's Mac", "Windows": "the user's Windows PC"}.get(platform.system(),
                                                                              "the user's Linux computer")


def _target_line(provider: Provider, environment: str = "", there: frozenset[str] = frozenset()) -> str:
    said = provider.describe()
    line = f"  - {provider.address()}: {said.line}"
    if environment:
        line += provider.environment_note(environment, set(there))
    if not said.missing:
        return line
    # The missing permission is a sentence of its own after the target's line.
    end = "" if line.rstrip().endswith((".", "!", "?")) else "."
    return f"{line.rstrip()}{end} Not usable yet: {said.missing}"


def _device_block(row: registry.Listed, environment: str, there: frozenset[str], code: bool) -> str:
    machine = row.machine
    if machine.is_local:
        what = [f"{_this_computer()}, where OpenProgram runs"]
        head = f"- {THIS_COMPUTER} (device left out)"
    else:
        reached = machine.describe()
        named = machine.name if machine.name and machine.name != machine.id else ""
        what = [", ".join(part for part in (named, reached) if part) or machine.id]
        if machine.id == environment:
            what.append("this conversation's shell and files are here")
        head = f'- {machine.id} (device="{machine.id}")'
    if code and machine.is_local:
        what.append("calls may send code")
    lines = [f"{head}: {'; '.join(what)}."]
    if not machine.is_local:
        offline = _unreachable_line(machine)
        if offline:
            lines.append(offline)
    if not row.providers:
        lines.append("  nothing on it can be operated with computer_use.")
    for provider in row.providers:
        lines.append(_target_line(provider, environment if machine.is_local else "", there))
    return "\n".join(lines)


def devices(conversation: str = "") -> dict[str, str]:
    """What can be operated now, by device: ``{device: its lines}``, this computer first.

    A device with nothing to operate is listed only when the conversation's shell runs there.
    """
    try:
        rows = registry.listing(conversation or context._session_id())
    except Exception:
        return {}
    _register_guides([provider for row in rows for provider in row.providers])
    current = context._environment()
    environment = current.id if current is not None and not current.is_local else ""
    there = frozenset(provider.target for row in rows if not row.machine.is_local for provider in row.providers)
    code = context._code_available()
    found: dict[str, str] = {}
    # The conversation's environment comes right after this computer, then the rest.
    for row in sorted(rows, key=lambda row: (not row.machine.is_local, row.machine.id != environment)):
        if not row.machine.is_local and not row.providers and row.machine.id != environment:
            continue
        found[THIS_COMPUTER if row.machine.is_local else row.machine.id] = _device_block(row, environment, there,
                                                                                        code)
    return found


def devices_note(previous: Optional[dict[str, str]], current: dict[str, str]) -> str:
    """What a turn tells the model about the devices, or "".

    With no ``previous`` (the conversation's first turn, the first after a
    summary) the whole list; afterwards each device that was added, changed
    or removed since, and nothing when nothing changed.
    """
    if previous is not None and previous == current:
        return ""
    if previous is None:
        lines = ["What computer_use can operate now, by device:", *current.values()] if current else [
            "Nothing can be operated now."]
        lines.append("A target not listed here is refused. A later message says what changes.")
    else:
        added = [block for name, block in current.items() if name not in previous]
        changed = [_changed(previous[name], block) for name, block in current.items()
                   if name in previous and previous[name] != block]
        removed = [f"- {name}" for name in previous if name not in current]
        lines = []
        if added:
            lines += ["Added devices:", *added]
        if changed:
            lines += ["Changed devices, as they are now:", *changed]
        if removed:
            lines += ["Removed devices (no longer operated):", *removed]
        if not lines:
            return ""
    return f"<{_DEVICES_TAG}>\n" + "\n".join(lines) + f"\n</{_DEVICES_TAG}>"


def _changed(before: str, now: str) -> str:
    """A changed device's entry: its block as it is now, saying so when it is reachable again."""
    if _UNREACHABLE in before and _UNREACHABLE not in now:
        head, _, rest = now.partition("\n")
        return "\n".join(part for part in (head, _BACK, rest) if part)
    return now


def devices_text(conversation: str = "") -> str:
    """The whole list, for a prompt of its own (a turn that does not send it beside its message)."""
    return devices_note(None, devices(conversation))


def available_targets() -> list[str]:
    """The targets set up now, in the order <devices> lists them."""
    try:
        return list(registry.providers(context._session_id()))
    except Exception:
        return []
