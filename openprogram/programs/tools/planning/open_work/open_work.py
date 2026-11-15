"""open_work — what the agent still owes the user in this conversation.

Storage and the system-prompt block: :mod:`openprogram.runtime.conversation.open_work`.
"""
from __future__ import annotations

from openprogram.programs._host.runtime import function

_ACTIONS = ("list", "add", "update", "done", "remove")


def _current() -> str:
    """The current conversation; "" outside one."""
    from openprogram.runtime.execution.run_control import get_current_session_id
    return get_current_session_id() or ""


@function(
    name="open_work",
    description=(
        "This conversation's open work: what you still owe the user after the "
        "current task (a reply you wait on, a check you promised, a draft the "
        "user has not answered). It stays with the conversation and shows in "
        "your system prompt on every turn here. For planning the steps of the "
        "current task, use the todo tools instead.\n"
        "\n"
        "Args:\n"
        "  action: list | add | update | done | remove.\n"
        "  text: for add, or a new text for update: one line.\n"
        "  note: details or the latest state (optional).\n"
        "  id: the item, for update, done and remove."
    ),
    toolset=["core"],
)
def open_work(action: str, text: str = "", note: str = "", id: str = "") -> str:
    """Read or change this conversation's open work."""
    return _open_work_impl(action, text, note, id)


def _open_work_impl(action: str, text: str = "", note: str = "", id: str = "") -> str:
    from openprogram.runtime.conversation import open_work as store
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return f"[open_work error] action must be one of {', '.join(_ACTIONS)}"
    session_id = _current()
    if not session_id:
        return "[open_work error] no conversation in this context"
    try:
        if action == "list":
            items = store.load(session_id)
            if not items:
                return "(no open work)"
            lines = []
            for status in store.STATUSES:
                group = [i for i in items if i.get("status") == status]
                if group:
                    lines.append(f"{status}:")
                    lines += [f"- #{i['id']} {i['text']}" + (f" — {i['note']}" if i.get("note") else "")
                              for i in group]
            return "\n".join(lines)
        if action == "add":
            item = store.add(session_id, text, note=note)
            return f"Open work #{item['id']} added: {item['text']}"
        if not id.strip():
            return f"[open_work error] {action} needs id"
        if action == "remove":
            store.remove(session_id, id.strip())
            return f"Open work #{id.strip()} removed"
        item = store.update(
            session_id, id.strip(),
            text=text or None, note=note or None,
            status="done" if action == "done" else None,
        )
        return f"Open work #{item['id']} {'done' if action == 'done' else 'updated'}: {item['text']}"
    except (KeyError, ValueError) as exc:
        return f"[open_work error] {exc.args[0] if exc.args else exc}"
