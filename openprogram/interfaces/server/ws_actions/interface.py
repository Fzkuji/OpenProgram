"""Authenticated interface presence and request-scoped operation receipts."""
from openprogram.programs._host.framework.interface import register_window, result


async def reminder_shown(ws, cmd: dict):
    """The desktop App showed a reminder's or report's system notification itself."""
    from openprogram.runtime.scheduler import reminders

    reminders.shown(str(cmd.get("id") or ""))


ACTIONS = {
    'framework_interface_register': register_window,
    'framework_interface_result': result,
    'reminder_shown': reminder_shown,
}
