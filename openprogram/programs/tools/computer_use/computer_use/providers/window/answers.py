"""What a step's result says: images, coded failures, recordings, and how far a run of steps got."""
from __future__ import annotations

from dataclasses import dataclass

from openprogram.runtime.agent.tool_execution import ToolReturn

from ... import errors
from ...contract import Call, Steps
from .platforms import _base, normalized_app


def _images(observation, call: Call) -> list[bytes]:
    return [observation.image] if observation.image and call.images else []


def _coded(exc: Exception) -> errors.CallError | None:
    """The coded error a backend failure stands for, or None for one that keeps its own sentence."""
    base = _base()
    if isinstance(exc, base.StaleElement):
        return errors.ElementGone(str(exc))
    if isinstance(exc, base.BadArgument):
        return errors.BadArgument(str(exc))
    if isinstance(exc, base.PermissionMissing):
        return errors.PermissionMissing(str(exc))
    return None


def _failed(exc: Exception) -> ToolReturn:
    """A backend failure of a call that read or listed: coded, or ``Error:`` with whether anything was sent."""
    coded = _coded(exc)
    if coded is not None:
        raise coded from None
    untouched = isinstance(exc, _base().NotDispatched)
    return ToolReturn(text=f"Error: {exc}", is_error=True, json_data={"action_dispatched": False} if untouched else None)


def _line(exc: Exception) -> str:
    coded = _coded(exc)
    return coded.text() if coded is not None else f"Error: {exc}"


def _clip_text(clip, *, transcribe: bool) -> str:
    """What the model learns of a recording: where it is, how long, and what was said."""
    head = f"Recorded {clip.seconds:g} seconds of {clip.app}, saved to {clip.path}."
    if clip.silent:
        return head + " It is silent: the app played no sound while recording."
    if not transcribe:
        return head
    from openprogram.runtime import speech_link
    try:
        spoken = speech_link.transcribe(clip.data)
    except speech_link.TranscriptionError as exc:
        return f"{head} Not transcribed: {exc}"
    text = spoken.get("text", "").strip()
    if not text:
        return head + f" No speech was recognized ({spoken.get('engine')})."
    return f"{head}\nTranscript ({spoken.get('engine')}, {spoken.get('model')}):\n{text}"


@dataclass
class _Run:
    """What the steps of one call leave for the state read after them."""
    full: bool = False


class _Partial(Exception):
    """A step made of several backend steps failed after some of them acted: ``done`` names those."""

    def __init__(self, done: str, exc: Exception) -> None:
        super().__init__(str(exc))
        self.done, self.exc = done, exc


def _ran_out(done: list[str], changed: bool, number: int, action: str, when: str) -> errors.Timeout:
    """``TIMEOUT:`` naming the steps that took effect; sent when any of them acted."""
    progress = f"Steps done: {', '.join(done)}. " if done else "Nothing was sent. "
    error = errors.Timeout(f"{progress}The call ran out of time {when} step {number} ({action}); it and the steps "
                           "after it did not run. Read the state before going on.")
    error.sent = changed
    return error


def _named(values: dict) -> str:
    return str(values.get("app") or "").strip() if isinstance(values, dict) else ""


def _application(subject: dict, steps: Steps, strict: bool = True) -> str:
    """The one application a call operates: the one its args name, or the one its steps name."""
    named = _named(subject)
    for number, (_action, step) in enumerate(steps, 1):
        other = _named(step)
        if strict and other and named and normalized_app(other) != normalized_app(named):
            raise errors.BadArgument(f"step {number} names another app; these steps drive {named} only. Operate "
                                     "another application with a call of its own.")
        named = named or other
    return named
