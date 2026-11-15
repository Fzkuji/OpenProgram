"""Target ``vm``: a remote machine behind an OSWorld-compatible HTTP server.

``GET /screenshot`` returns the screen as PNG; ``POST /execute`` runs a
command there, which drives pyautogui. The address is the
``computer_use.vm_url`` setting. There is no element tree: pointer actions
take pixels of the last screenshot.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

from openprogram.programs._host.execution import ToolReturn

NAME = "vm"

ACTING_ACTIONS = ("click", "move", "drag", "scroll", "type_text", "press_key", "wait")
ACTIONS = ("screenshot", *ACTING_ACTIONS)
_TIMEOUT = 30
_CONSUMER = "computer_use.vm"

_GUIDE = (Path(__file__).parent.parent / "guides" / "vm.md").read_text(encoding="utf-8")


class VMError(RuntimeError):
    pass


def guide() -> str:
    return _GUIDE


def url() -> str:
    try:
        from openprogram.state.config import read_config
        value = (read_config().get("computer_use") or {}).get("vm_url")
    except Exception:
        return ""
    return str(value or "").strip().rstrip("/")


def available() -> bool:
    return bool(url())


def approval(steps: list[tuple[str, dict]], args: dict) -> str | bool:
    """Approval names the endpoint; reading the screen asks too, since it shows another machine."""
    return f"computer_use_agent controls vm:{url() or 'unconfigured'}"


def _client(base: str):
    """The configured VM, reached through the outbound policy. Setting the
    address is the owner's authorization to reach that private origin."""
    from openprogram.host.security.safe_http import configured_safe_client
    from openprogram.host.security.url_policy import OwnerURLException, normalize_origin

    origin = normalize_origin(base)
    return configured_safe_client(_CONSUMER, base, timeout=_TIMEOUT + 5,
                                  owner_exception=OwnerURLException(consumer=_CONSUMER, origin=origin))


def screenshot(base: str) -> bytes:
    try:
        with _client(base) as client:
            response = client.get(f"{base}/screenshot")
            response.raise_for_status()
            data = response.content
    except Exception as exc:
        raise VMError(f"the VM at {base} did not return a screenshot: {exc}") from exc
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise VMError(f"the VM at {base} returned something other than a PNG screenshot")
    return data


def _execute(base: str, script: str) -> None:
    body = json.dumps({"command": ["python3", "-c", script], "shell": False, "timeout": _TIMEOUT}).encode()
    try:
        with _client(base) as client:
            response = client.post(f"{base}/execute", content=body, headers={"Content-Type": "application/json"})
            response.raise_for_status()
            payload = json.loads(response.content or b"{}")
    except Exception as exc:
        raise VMError(f"the VM at {base} did not run the step: {exc}") from exc
    if not isinstance(payload, dict):
        raise VMError("the VM returned a malformed reply")
    if payload.get("error") or payload.get("returncode", 0) != 0:
        raise VMError(str(payload.get("error") or payload.get("stderr") or f"exit {payload.get('returncode')}").strip())


def _number(step: dict, key: str) -> int:
    try:
        return int(round(float(step[key])))
    except (KeyError, TypeError, ValueError) as exc:
        raise VMError(f"{key} must be a number of screenshot pixels") from exc


def _script(action: str, step: dict) -> str:
    """The pyautogui program for one step; every value is passed as a JSON literal."""
    lit = json.dumps
    if action == "click":
        button = str(step.get("mouse_button") or "left")
        if button not in {"left", "right", "middle"}:
            raise VMError("mouse_button is left, right or middle")
        clicks = max(1, min(3, int(step.get("click_count") or 1)))
        return (f"import pyautogui; pyautogui.click({_number(step, 'x')}, {_number(step, 'y')}, "
                f"button={lit(button)}, clicks={clicks}, interval=0.08)")
    if action == "move":
        return f"import pyautogui; pyautogui.moveTo({_number(step, 'x')}, {_number(step, 'y')}, duration=0.2)"
    if action == "drag":
        return (f"import pyautogui; pyautogui.moveTo({_number(step, 'x')}, {_number(step, 'y')}); "
                f"pyautogui.dragTo({_number(step, 'to_x')}, {_number(step, 'to_y')}, duration=0.5, button='left')")
    if action == "scroll":
        direction = str(step.get("direction") or "down")
        clicks = max(1, int(step.get("clicks") or 5))
        at = (f"pyautogui.moveTo({_number(step, 'x')}, {_number(step, 'y')}); "
              if step.get("x") is not None and step.get("y") is not None else "")
        if direction in {"up", "down"}:
            return f"import pyautogui; {at}pyautogui.scroll({clicks if direction == 'up' else -clicks})"
        if direction in {"left", "right"}:
            return f"import pyautogui; {at}pyautogui.hscroll({-clicks if direction == 'left' else clicks})"
        raise VMError("direction is up, down, left or right")
    if action == "type_text":
        text = str(step.get("text") or "")
        if not text:
            raise VMError("type_text needs text")
        return f"import pyautogui; pyautogui.write({lit(text)}, interval=0.02)"
    if action == "press_key":
        keys = [part.strip().lower() for part in str(step.get("key") or "").split("+") if part.strip()]
        if not keys:
            raise VMError("press_key needs key")
        return f"import pyautogui; pyautogui.hotkey(*{lit(keys)})"
    raise VMError(f"unknown vm action {action!r}; actions: {', '.join(ACTIONS)}")


def open_target(args: dict, session: str) -> ToolReturn:
    return run([("screenshot", {})], args, session)


def run(steps: list[tuple[str, dict]], args: dict, session: str) -> ToolReturn:
    base = url()
    if not base:
        return ToolReturn(text="Error: no virtual machine is configured; set one with openprogram config set computer_use.vm_url <url>.",
                          is_error=True)
    done: list[str] = []
    failure = None
    try:
        for number, (action, step) in enumerate(steps, 1):
            step = {**args, **step}
            if action == "screenshot":
                continue
            if action not in ACTING_ACTIONS:
                raise VMError(f"unknown vm action {action!r}; actions: {', '.join(ACTIONS)}")
            try:
                if action == "wait":
                    time.sleep(max(0.0, min(10.0, float(step.get("duration") or 1))))
                else:
                    _execute(base, _script(action, step))
            except VMError as exc:
                failure = f"step {number} ({action}) failed: {exc}" if len(steps) > 1 else str(exc)
                break
            done.append(f"{number}. {action}" if len(steps) > 1 else action)
        if done:
            time.sleep(0.5)
        image = screenshot(base)
    except VMError as exc:
        return ToolReturn(text=f"Error: {exc}", is_error=True)
    head = ("Done:\n" + "\n".join(done)) if len(done) > 1 else (f"Done: {done[0]}." if done else "Screenshot of the VM.")
    if failure:
        head = (head + "\n" if done else "") + f"Error: {failure}"
    return ToolReturn(text=head + " Coordinates are pixels of this screenshot.", images=[image], is_error=bool(failure))


__all__ = ["ACTIONS", "NAME", "approval", "available", "guide", "open_target", "run", "screenshot", "url"]
