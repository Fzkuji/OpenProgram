"""Reminders: a scheduled moment the user hears about, or the agent acts on.

The worker fires a due reminder task as one ``reminder.fired`` event on the
process bus and does nothing else. Delivery is the subscriber installed
here: every open client gets a ``reminder`` frame (an in-app toast; the
desktop App also shows a system notification and answers ``reminder_shown``),
a channel conversation (WeChat, Telegram, …) gets the text through its
outbound path, and a waking reminder starts an owner turn in the
conversation it was set in, so the agent acts on it there. When no desktop
App answers, the worker posts the system notification itself, so a reminder
still pops up with every App window closed (macOS, Linux with
``notify-send``, Windows through Windows PowerShell). Config ``hooks`` and proactive
policies can subscribe to the same event.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Any, Callable

_log = logging.getLogger(__name__)

EVENT = "reminder.fired"
# A waking reminder waits this long for a running turn in its conversation.
WAKE_WAIT_S = 600.0
_POLL_S = 2.0
# How long a desktop App has to answer that it showed the reminder.
SHOWN_WAIT_S = 6.0

_shown: dict[str, threading.Event] = {}
_shown_lock = threading.Lock()

_installed = False
_install_lock = threading.Lock()


def install() -> bool:
    """Subscribe delivery to the process bus once; True when this call did it."""
    global _installed
    with _install_lock:
        if _installed:
            return False
        _installed = True
    from openprogram.runtime.events import get_event_bus
    get_event_bus().subscribe(deliver, types={EVENT})
    return True


def fire(entry: dict[str, Any], spec: dict[str, Any], event: dict[str, Any] | None = None) -> dict[str, Any]:
    """Emit the event for a due reminder task from its verified spec.

    ``event`` is the outside event (webhook call, watched change) that
    started it; a wake then carries it and runs under approvals.
    """
    from openprogram.runtime.events import get_event_bus, make_event

    install()
    payload = {
        "task_id": str(entry.get("id") or ""),
        "title": str(entry.get("title") or ""),
        "message": spec["reminder"],
        "session_id": spec.get("session_id") or "",
        "agent_id": spec.get("agent_id") or "main",
        "wake": bool(spec.get("wake")),
        "scheduled_for": entry.get("run_at") or entry.get("cron") or "",
        "fired_at": time.time(),
    }
    if event:
        payload["event"] = {"source": str(event.get("source") or ""), "text": str(event.get("text") or "")}
    metadata = {"session": payload["session_id"]} if payload["session_id"] else {}
    get_event_bus().emit(make_event(EVENT, "system", payload, metadata))
    return payload


def shown(notice_id: str) -> None:
    """A desktop App showed this reminder or report; the worker need not."""
    with _shown_lock:
        answered = _shown.get(notice_id)
    if answered is not None:
        answered.set()


def _bundle_notify(title: str, message: str) -> bool:
    """Post through the app bundle this process runs in, so the notification
    carries OpenProgram's name and icon. False outside a bundle (a pip
    install), where macOS has no center for the process."""
    try:
        import Foundation
    except ImportError:
        return False
    center = Foundation.NSUserNotificationCenter.defaultUserNotificationCenter()
    if center is None:
        return False
    note = Foundation.NSUserNotification.alloc().init()
    note.setTitle_(title)
    note.setInformativeText_(message)
    note.setSoundName_("Glass")
    center.deliverNotification_(note)
    return True


# Windows shows a toast only for a registered application; Windows PowerShell
# is one on every install, so the toast is posted under its name, the way
# Script Editor posts the macOS one outside the app bundle.
_WINDOWS_TOAST_APP = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
_WINDOWS_TOAST_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "[void][Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime];"
    "[void][Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime];"
    "$xml=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    "$text=$xml.GetElementsByTagName('text');"
    "[void]$text.Item(0).AppendChild($xml.CreateTextNode($env:OPENPROGRAM_NOTICE_TITLE));"
    "[void]$text.Item(1).AppendChild($xml.CreateTextNode($env:OPENPROGRAM_NOTICE_MESSAGE));"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:OPENPROGRAM_NOTICE_APP)"
    ".Show([Windows.UI.Notifications.ToastNotification]::new($xml))"
)


def _windows_toast(title: str, message: str) -> bool:
    """Post a Windows toast through Windows PowerShell, which can load WinRT.

    The texts travel in the child's environment and enter the toast as text
    nodes, so nothing an Agent wrote is parsed as script or as XML.
    PowerShell 7 (``pwsh``) cannot load WinRT types, so it is not a fallback.
    """
    executable = shutil.which("powershell.exe")
    if executable is None:
        return False
    env = {**os.environ, "OPENPROGRAM_NOTICE_TITLE": title, "OPENPROGRAM_NOTICE_MESSAGE": message,
           "OPENPROGRAM_NOTICE_APP": _WINDOWS_TOAST_APP}
    command = [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_TOAST_SCRIPT]
    return subprocess.run(command, capture_output=True, timeout=10, env=env,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode == 0


def notification_support(platform: str | None = None) -> dict:
    """Whether the worker can post a system notification itself, and why not.

    The desktop App shows its own on every platform; this is the fallback
    for when no App window answers.
    """
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return {"available": True, "reason": ""}
    if platform == "win32":
        if shutil.which("powershell.exe"):
            return {"available": True, "reason": ""}
        return {"available": False, "reason": "Notifications from the worker need Windows PowerShell (powershell.exe)."}
    if platform.startswith("linux"):
        if shutil.which("notify-send"):
            return {"available": True, "reason": ""}
        return {"available": False, "reason": "Notifications from the worker need notify-send (the libnotify tools)."}
    return {"available": False, "reason": f"Notifications from the worker are not supported on {platform}."}


def system_notify(title: str, message: str) -> bool:
    """Post an operating-system notification from the worker itself.

    The texts can be anything an Agent wrote; ``--`` keeps one that starts
    with a dash from being read as a command option.
    """
    try:
        if sys.platform == "darwin":
            if _bundle_notify(title, message):
                return True
            # Script Editor posts it, under its own name and icon.
            script = ('on run argv\n'
                      'display notification (item 2 of argv) with title (item 1 of argv) '
                      'sound name "Glass"\nend run')
            command = ["osascript", "-e", script, "--", title, message]
        elif sys.platform.startswith("linux") and shutil.which("notify-send"):
            command = ["notify-send", "--urgency=critical", "--app-name=OpenProgram", "--", title, message]
        elif sys.platform == "win32":
            return _windows_toast(title, message)
        else:
            return False
        return subprocess.run(command, capture_output=True, timeout=10).returncode == 0
    except Exception as exc:  # noqa: BLE001 - the in-app paths still ran
        _log.warning("system notification failed: %s: %s", type(exc).__name__, exc)
        return False


def _wait_then_notify(notice_id: str, answered: threading.Event, title: str, message: str,
                      unless: Callable[[], bool] | None) -> None:
    try:
        if not answered.wait(SHOWN_WAIT_S) and not (unless is not None and unless()):
            system_notify(title, message)
    finally:
        with _shown_lock:
            _shown.pop(notice_id, None)


def notify_unless_shown(notice_id: str, title: str, message: str,
                        unless: Callable[[], bool] | None = None) -> None:
    """Post a system notification unless a desktop App answers :func:`shown`.

    Call it before the frame goes out, so an answer cannot come first.
    ``unless`` is asked when the wait ends; True means the user no longer
    needs telling.
    """
    answered = threading.Event()
    with _shown_lock:
        _shown[notice_id] = answered
    threading.Thread(target=_wait_then_notify, name="system-notify-fallback", daemon=True,
                     args=(notice_id, answered, title, message, unless)).start()


def _session(session_id: str) -> dict[str, Any] | None:
    if not session_id:
        return None
    try:
        from openprogram.runtime.context.session_db import default_db
        return default_db().get_session(session_id)
    except Exception:
        return None


def _send_to_channel(session: dict[str, Any] | None, text: str) -> None:
    """Send ``text`` to the channel conversation behind ``session``, if any."""
    if not session or not text or not session.get("channel") or not session.get("peer_id"):
        return
    from openprogram.runtime import channel_link
    try:
        sent = channel_link.send_message(str(session["channel"]), str(session.get("account_id") or ""),
                                         str(session["peer_id"]), text)
        if not sent.ok:
            _log.warning("channel send failed: %s %s", sent.error_kind, sent.error_detail or "")
    except Exception as exc:  # noqa: BLE001 - a failed send (no channels included) must not stop delivery
        _log.warning("channel send failed: %s: %s", type(exc).__name__, exc)


def wake_text(payload: dict[str, Any]) -> str:
    """The user message a waking reminder starts its turn with."""
    fired = datetime.fromtimestamp(payload["fired_at"]).astimezone()
    event = payload.get("event")
    if event:
        from openprogram.programs.tools.jobs.cron.worker import event_note
        return (f"⏰ {payload['message']}\n\n"
                f"(A task you set was started by a {event.get('source') or 'outside'} event at "
                f"{fired:%Y-%m-%d %H:%M %Z}. Act on it now.)" + event_note(event))
    return (f"⏰ {payload['message']}\n\n"
            f"(A reminder you set fired at {fired:%Y-%m-%d %H:%M %Z}. Act on it now.)")


def _wake(payload: dict[str, Any], session: dict[str, Any]) -> None:
    from openprogram.runtime.conversation.owner_turn import start_owner_turn_when_free

    session_id = payload["session_id"]

    def done(result: Any) -> None:
        # A channel conversation hears the agent's answer, not only the reminder.
        _send_to_channel(session, str(getattr(result, "final_text", "") or ""))

    # The wake waits for the conversation's current turn and then takes the
    # run slot itself; it never runs beside another turn. When the slot
    # stays taken past the wait, the reminder is delivered without a turn.
    started = start_owner_turn_when_free(
        session_id, str(session.get("agent_id") or payload["agent_id"]), wake_text(payload),
        wait_s=WAKE_WAIT_S, poll_s=_POLL_S, name=f"reminder-{payload['task_id']}",
        reason_code="reminder_activation_failed", on_done=done, automation="reminder")
    if started is None:
        _log.info("%s stayed busy; delivered without waking the agent", session_id)
        _send_to_channel(session, f"⏰ {payload['message']}")


def deliver(event: Any) -> None:
    """Bus subscriber: show the reminder everywhere, then wake the agent if asked."""
    from openprogram.runtime.events import emit_ws_frame

    payload = dict(event.payload)
    session = _session(payload.get("session_id") or "")
    # Nobody reads an archived conversation; the reminder itself still arrives.
    wake = bool(payload.get("wake") and session and not session.get("archived"))
    notify_unless_shown(event.id, "OpenProgram ⏰", payload["message"])
    emit_ws_frame({"type": "reminder", "data": {
        **payload, "id": event.id, "wake": wake,
        "session_id": payload.get("session_id") if session else "",
    }})
    if wake:
        target, name = (lambda: _wake(payload, session)), "reminder-wake"
    else:
        target, name = (lambda: _send_to_channel(session, f"⏰ {payload['message']}")), "reminder-send"
    threading.Thread(target=target, name=name, daemon=True).start()


__all__ = ["EVENT", "deliver", "fire", "install", "notification_support", "notify_unless_shown", "shown",
           "system_notify", "wake_text"]
