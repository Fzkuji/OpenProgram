"""Running checked steps on a target: the one path an action call and a step of code both take."""
from __future__ import annotations

from openprogram.runtime.agent.tool_execution import ToolReturn

from .. import errors
from . import locks
from ..actions import vocabulary
from ..contract import Call, Provider, Steps, refused


def run_steps(provider: Provider, subject: dict, steps: Steps, call: Call) -> ToolReturn:
    """Run checked steps on a target, holding the locks its provider asks for; a coded failure is the answer."""
    def waiting() -> None:
        try:
            call.check()
        except errors.CallError:
            raise
        except Exception as exc:
            # Code's broker stops a step with a plain error (deadline, stop, revoke); while it waits, nothing was sent.
            raise errors.Timeout(f"stopped while waiting for {provider.address()}: {exc}; nothing was sent") from exc

    try:
        with locks.held(provider.lock(subject, steps), cancelled=waiting):
            if len(steps) == 1:
                action, step = steps[0]
                if action in vocabulary.LISTS:
                    return provider.list(vocabulary.LISTS[action], subject, step, call)
                if action == "get_state":
                    return provider.get_state(subject, step, call)
            return provider.act(subject, steps, call)
    except locks.Busy as exc:
        return refused(errors.ScreenBusy(
            f"another agent has been operating {provider.address()} for {exc.seconds:g} s; nothing was sent. "
            "Try again when it is done."))
    except errors.CallError as exc:
        return refused(exc)


__all__ = ["run_steps"]
