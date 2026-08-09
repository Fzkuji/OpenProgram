"""send_file — hand a file the agent has to the person it is talking to.

The tool does NOT deliver. It validates the path, reads its size, and
registers one marker in the turn's outbound list; the dispatcher folds
that list into the assistant message text, and each surface delivers it
its own way (web chat renders a chip that opens the file; a chat channel
uploads the bytes with the platform's own file API). The tool therefore
knows nothing about channels, and a new channel needs no change here.

Why a tool rather than a bare ``MEDIA:/path`` line in the reply prose
(openclaw / hermes) or an implicit scan for absolute paths (weclaw): the
failure mode of a prose convention is that an imperfect extraction leaks
the raw line to the user as text — a bill this repo has already paid
once, on inbound channel attachments. A tool call has somewhere to put
an honest "that file isn't there" and cannot be triggered by the model
merely mentioning a path.

SECURITY. This is the direction where a model names a path and the
system reads it out to a chat platform, so the path check is the whole
boundary — note in particular that ``openprogram/sandbox/`` does NOT
cover it: the sandbox wraps SHELL commands, and this tool is in-process
Python, so nothing else stands between a bad path and the wire. The
allowed set is :func:`openprogram.attachments.sendable_roots` — the
session's own workdir plus the project the session is explicitly bound
to — and symlinks are fully resolved BEFORE containment is tested, so a
link planted inside an allowed root cannot point outside it. A rejected
path returns a spelled-out error naming the roots; it never fails
silently.
"""
from __future__ import annotations

from contextvars import ContextVar

from openprogram.functions._runtime import function


_DESCRIPTION = (
    "Send a file to the user as a real attachment — an image, a PDF, a "
    "generated chart, a log, anything on disk. Use it whenever you have "
    "produced or found a file the user should actually see, instead of "
    "only writing its path in your reply.\n"
    "\n"
    "- `path` MUST be absolute, and must live in the session working "
    "directory or the project bound to this session.\n"
    "- Web chat shows it as a chip the user can click open; on a chat "
    "platform the file is uploaded as an attachment.\n"
    "- Still describe the file in your reply — this tool attaches it, it "
    "does not speak for you."
)

#: Per-turn outbound list: ``[{path, name, size}, …]``. A ContextVar
#: holding a mutable list, so a tool body running in a copied context
#: (``run_in_executor``) still appends to the same object the dispatcher
#: reads back afterwards.
_PENDING: ContextVar[list] = ContextVar("_send_file_pending", default=[])

#: Upper bound on one outbound file. Separate knob from the inbound
#: ``MAX_ATTACH_BYTES`` even though the number matches today: this one
#: is about what chat platforms accept on upload, that one is about not
#: committing giant blobs into a session's git history.
MAX_SEND_BYTES = 32 * 1024 * 1024


def begin_turn() -> None:
    """Start a fresh outbound list for this turn."""
    _PENDING.set([])


def drain() -> list[dict]:
    """Take everything registered this turn, leaving the list empty."""
    pending = _PENDING.get()
    if not pending:
        return []
    out = list(pending)
    pending.clear()
    return out


def markers_for(entries: list[dict]) -> str:
    """The entries as attachment markers, one per line ("" when empty)."""
    from openprogram.attachments import format_marker
    return "\n".join(
        format_marker(e["name"], e["path"], int(e.get("size") or 0))
        for e in entries
    )


@function(
    name="send_file",
    description=_DESCRIPTION,
    toolset=["core"],
    # Surfaces with no attachment channel at all. Rather than telling the
    # model in the system prompt that it can't send files here (hermes'
    # approach), don't hand it the tool — it can't try and fail.
    # WeChat's iLink has no file-upload API; the CLI/TUI is a terminal.
    unsafe_in=["cli", "tui", "wechat", "plan"],
)
def send_file(path: str) -> str:
    """Attach a file to your reply so the user receives it.

    Args:
        path: Absolute path of the file to send.
    """
    return _send_file_impl(path)


def _send_file_impl(path: str) -> str:
    """Implementation body — kept apart from the @function binding so
    tests can call it directly (the binding object is not callable)."""
    from openprogram import attachments as _att
    from openprogram.agent.run_control import get_current_session_id

    raw = (path or "").strip()
    if not raw:
        return "Error: path is required."
    session_id = get_current_session_id()
    roots = _att.sendable_roots(session_id)
    if not roots:
        return ("Error: no allowed directory is configured for outgoing "
                "files (no session workdir and no bound project), so "
                "send_file cannot deliver anything right now.")
    target = _att.resolve_within(raw, roots)
    if target is None:
        listed = ", ".join(str(r) for r in roots)
        return (f"Error: {raw!r} is outside the directories this session "
                f"may send files from. Allowed (symlinks resolved): "
                f"{listed}. Copy the file into the working directory "
                f"first, then send that copy.")
    if not target.is_file():
        return f"Error: no such file: {target}"
    try:
        size = target.stat().st_size
    except OSError as e:
        return f"Error: cannot stat {target}: {type(e).__name__}: {e}"
    if size > MAX_SEND_BYTES:
        return (f"Error: {target.name} is {size / 1024 / 1024:.1f} MB, over "
                f"the {MAX_SEND_BYTES // 1024 // 1024} MB limit for an "
                f"outgoing file.")
    entry = {"path": str(target), "name": target.name, "size": size}
    pending = _PENDING.get()
    if any(e["path"] == entry["path"] for e in pending):
        return f"Already attached to this reply: {target.name}"
    pending.append(entry)
    return (f"Attached {target.name} ({size} bytes). It goes out with your "
            f"reply — describe it in your message.")


# ponytail: no caption parameter. The reply text is delivered next to
# the file on every surface, so a per-file caption would only duplicate
# it; add one when a platform needs the text welded to the upload.

__all__ = ["send_file", "_send_file_impl", "begin_turn", "drain",
           "markers_for", "MAX_SEND_BYTES"]
