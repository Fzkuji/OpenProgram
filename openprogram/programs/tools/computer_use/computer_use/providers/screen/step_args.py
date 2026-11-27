"""What a screen step may touch (the owner's policy) and its arguments read the one way."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openprogram.runtime.agent.tool_execution import ToolReturn

from ... import errors


MAX_SECONDS = 10.0
MAX_TEXT_CHARS = 5000


@dataclass(frozen=True)
class Policy:
    """What the owner keeps the screen away from, read for each call."""
    refused: frozenset[str] = frozenset()
    bypass: bool = False
    mode: str = ""
    refuse_password_fields: bool = False

    def hides(self, owner) -> bool:
        """Whether a screenshot leaves this application out, and a step on it is refused."""
        return self.refused_app(owner) or (owner.own and not self.bypass)

    def refused_app(self, owner) -> bool:
        return bool({owner.bundle.lower(), owner.name.lower()} & self.refused)


def _read_policy() -> Policy:
    from openprogram.runtime.agent.turn_request import get_turn_request
    from openprogram.state.config import read_config
    settings = read_config().get("computer_use") or {}
    listed = settings.get("refused_apps")
    refused = frozenset(str(item).strip().lower() for item in listed) if isinstance(listed, list) else frozenset()
    mode = str(getattr(get_turn_request(), "permission_mode", None) or "")
    return Policy(refused, mode == "bypass", mode, settings.get("refuse_password_fields") is True)


class _Stopped(Exception):
    """A step that was not sent, for a reason with no code: the screen is locked, secure entry is on."""


def _number(step: dict, key: str) -> float:
    try:
        return float(step[key])
    except (KeyError, TypeError, ValueError):
        raise errors.BadArgument(f"{key} must be a number of screenshot pixels.") from None


def _seconds(value: Any, default: float) -> float:
    try:
        seconds = float(default if value in (None, "") else value)
    except (TypeError, ValueError):
        raise errors.BadArgument("seconds must be a number.") from None
    if not 0 <= seconds <= MAX_SECONDS:
        raise errors.BadArgument(f"seconds must be between 0 and {MAX_SECONDS:g}.")
    return seconds


def _text(step: dict) -> str:
    text = step.get("text")
    if not isinstance(text, str) or not text:
        raise errors.BadArgument("text is required.")
    if len(text) > MAX_TEXT_CHARS:
        raise errors.BadArgument(f"text holds at most {MAX_TEXT_CHARS} characters; send it in parts.")
    return text


def _click(step: dict) -> tuple[str, int]:
    button = str(step.get("mouse_button") or "left").lower()
    if button not in ("left", "right", "middle"):
        raise errors.BadArgument("mouse_button is left, right or middle.")
    try:
        count = int(step.get("click_count") or 1)
    except (TypeError, ValueError):
        count = 0
    if not 1 <= count <= 3:
        raise errors.BadArgument("click_count is 1, 2 or 3.")
    return button, count


def _scroll(step: dict) -> tuple[str, float, float]:
    direction = str(step.get("direction") or "down").lower()
    if direction not in ("up", "down", "left", "right"):
        raise errors.BadArgument("direction is up, down, left or right.")
    try:
        pages = float(step.get("pages") or 1)
        pixels = float(step.get("pixels") or 0)
    except (TypeError, ValueError):
        raise errors.BadArgument("pages and pixels are numbers.") from None
    if not 0 < pages <= 20 or not 0 <= pixels <= 20_000:
        raise errors.BadArgument("pages is more than 0 and at most 20; pixels at most 20000.")
    return direction, pages, pixels


def _stopped(exc: _Stopped) -> ToolReturn:
    return ToolReturn(text=f"Error: {exc}", is_error=True, json_data={"action_dispatched": False})


def _failure_text(exc: BaseException) -> str:
    if isinstance(exc, errors.CallError):
        return exc.text()
    return str(getattr(exc, "text", "") or exc)
