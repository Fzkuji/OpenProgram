"""Which platform-dependent features the computer this worker runs on has.

One row per feature whose implementation differs by operating system:
``available`` and, when it is not, a ``reason`` a person can read. A client
reads this before it offers a control, so a feature this computer lacks is
shown as unavailable with the reason instead of failing when used. A row
with ``setting`` names the configuration key its control writes.

Reading it asks the operating system for no permission and changes nothing.
Permission grants (Screen Recording, Accessibility) are a different
question, answered by :mod:`openprogram.host.system_access`.
"""
from __future__ import annotations

import sys
from typing import Any, Callable

Probe = Callable[[], dict]


def _sandbox() -> dict:
    from openprogram.host import sandbox
    reason = sandbox.unavailable_reason()
    return {"available": reason is None, "reason": reason or ""}


def _sandbox_network_domains() -> dict:
    if sys.platform != "darwin":
        return {"available": False, "reason": "Per-domain network rules in the sandbox are enforced on macOS only."}
    return _sandbox()


def _computer_use_app() -> dict:
    from openprogram.programs.tools.computer_use.computer_use_agent.desktop.backends import support
    state = support()
    return {"available": bool(state["available"]), "reason": str(state.get("reason") or "")}


def _computer_use_code() -> dict:
    from openprogram.programs.tools.computer_use.computer_use_agent import code
    if code.available():
        return {"available": True, "reason": ""}
    if sys.platform != "darwin":
        return {"available": False, "reason": "computer_use code runs in the macOS sandbox only."}
    return {"available": False, "reason": _sandbox()["reason"]}


def _application(name: str):
    from openprogram.programs._host import services
    return services.lookup(name)


def _workflow_recording() -> dict:
    recorder = _application("workflow_recording")
    if recorder is None:
        return {"available": False, "reason": "Workflow recording is not enabled."}
    return recorder.support()


def _meeting_system_audio() -> dict:
    meetings = _application("meeting")
    if meetings is None:
        return {"available": False, "reason": "Meetings is not enabled."}
    return meetings.system_audio_support()


def _keep_awake() -> dict:
    from openprogram.host._platform import power
    return power.support()


def _notifications() -> dict:
    from openprogram.runtime.scheduler.reminders import notification_support
    return notification_support()


def _reveal() -> dict:
    from openprogram.host._platform import file_manager
    try:
        file_manager.reveal_command("")
    except file_manager.NoFileManager as exc:
        return {"available": False, "reason": str(exc)}
    return {"available": True, "reason": ""}


def _clipboard() -> dict:
    from openprogram.host._platform import clipboard
    return clipboard.support()


def _background_browser() -> dict:
    from openprogram.host.resources import background_browser
    if background_browser.available():
        return {"available": True, "reason": ""}
    return {"available": False,
            "reason": "No browser to start: install Playwright's Chromium, Google Chrome or Microsoft Edge."}


def _worker_service() -> dict:
    from openprogram.host._platform.environment import worker_service_backend
    if worker_service_backend() is not None:
        return {"available": True, "reason": ""}
    return {"available": False, "reason": f"No login service for the worker on {sys.platform}."}


def _conversational_update() -> dict:
    from openprogram.host._platform.environment import conversational_update_backend
    if conversational_update_backend() is not None:
        return {"available": True, "reason": ""}
    # A runtime in the version store updates through its own controller on
    # every platform (a Scheduled Task on Windows): install the new version,
    # move the pointer, keep the previous one for rollback.
    from openprogram.operations.self_update.versions.layout import managed
    if managed():
        return {"available": True, "reason": ""}
    return {"available": False,
            "reason": "Updating from a conversation needs the version-store runtime, or the App on macOS; "
                      "use `openprogram upgrade` or the App's updater."}


def _command_shell() -> dict:
    from openprogram.host._platform.command_shell import resolve_shell
    return {"available": True, "reason": "", "shell": resolve_shell().as_dict()}


#: ``(id, label, label_zh, setting, probe)``. The order is the order shown.
FEATURES: tuple[tuple[str, str, str, str | None, Probe], ...] = (
    ("command_sandbox", "Command sandbox", "命令沙箱", "sandbox.mode", _sandbox),
    ("sandbox_network_domains", "Sandbox network rules by domain", "沙箱按域名的网络规则",
     "sandbox.network_domains", _sandbox_network_domains),
    ("computer_use_app", "Computer use: application windows", "电脑操作：应用窗口", None, _computer_use_app),
    ("computer_use_code", "Computer use: code", "电脑操作：代码", None, _computer_use_code),
    ("workflow_recording", "Workflow recording", "工作流录制", None, _workflow_recording),
    ("meeting_system_audio", "Meetings: the computer's sound", "会议：电脑声音", None, _meeting_system_audio),
    ("keep_awake", "Prevent idle system sleep", "阻止空闲睡眠", "execution.prevent_idle_sleep", _keep_awake),
    ("system_notifications", "Notifications from the worker", "Worker 系统通知", None, _notifications),
    ("reveal_in_file_manager", "Show in file manager", "在文件管理器中显示", None, _reveal),
    ("clipboard", "Copy to clipboard", "复制到剪贴板", None, _clipboard),
    ("background_browser", "Background browser", "后台浏览器", None, _background_browser),
    ("worker_service", "Worker login service", "Worker 登录服务", None, _worker_service),
    ("conversational_update", "Update from a conversation", "对话内更新", None, _conversational_update),
    ("command_shell", "Command shell", "命令 Shell", None, _command_shell),
)


def _row(feature: tuple[str, str, str, str | None, Probe]) -> dict[str, Any]:
    identifier, label, label_zh, setting, probe = feature
    state: dict = {}
    try:
        state = probe()
        available, reason = bool(state["available"]), str(state.get("reason") or "")
    except Exception as exc:  # noqa: BLE001 - one failed probe must not hide the other rows
        available, reason = False, f"This feature could not be checked ({type(exc).__name__}: {exc})."
    row = {"id": identifier, "label": label, "label_zh": label_zh, "setting": setting,
           "available": available, "reason": "" if available else reason}
    if "shell" in state:
        # The command shell row also says which shell it is and what it keeps.
        row["shell"] = state["shell"]
    return row


def report() -> dict:
    """Every platform-dependent feature of this worker's computer, checked now."""
    platform = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else \
        "linux" if sys.platform.startswith("linux") else sys.platform
    return {"schema": 1, "platform": platform, "features": [_row(feature) for feature in FEATURES]}
