"""send_file — hand a file the agent has to the person it is talking to.

The tool does NOT deliver. It validates the path and reports success;
when the turn ends, the turn runtime reads the turn's recorded
``send_file`` calls (:func:`with_attachments`), turns each into an
attachment marker on the end of the assistant message text, and each
surface delivers it its own way (web chat renders a file card that opens
the file; a chat channel uploads the bytes with the platform's own file
API). Because the record is the source, a turn that paused and resumed
keeps what it attached before the pause. The tool therefore
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
boundary — note in particular that ``openprogram/host/sandbox/`` does NOT
cover it: the sandbox wraps SHELL commands, and this tool is in-process
Python, so nothing else stands between a bad path and the wire. The
allowed set is :func:`openprogram.state.attachments.sendable_roots` — the
session's own workdir plus the project the session is explicitly bound
to, or for a chat with no project the folder it works in (guarded, and
only while its replies stay in the owner's App) — and symlinks are fully resolved BEFORE containment is tested, so a
link planted inside an allowed root cannot point outside it. A rejected
path returns a spelled-out error naming the roots; it never fails
silently.

PRODUCED FILES. A model that builds a deck with a python script and then
answers "done, it is at exports/plan.pptx" has delivered nothing, and in
practice that is the common case. So at turn end the runtime also
attaches what the turn itself produced (:func:`produced_files`): a path
named in the reply (or written by ``write``, or saved by
``image_generate``) is attached when it EXISTS, is a regular file inside
the same roots, was modified after the turn started, and is a
deliverable format. The mtime test is what keeps a file the model merely
read or mentioned from going out, and is also why this is not the
"mentioning a path sends it" design rejected above: a mention alone
sends nothing. Text formats the edit tools changed are left to the
turn's edit card. Only ``stat`` is used, the count is capped, and any
failure attaches nothing rather than reaching the turn.
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat as _stat
from pathlib import Path
from urllib.parse import unquote

from openprogram.programs._host.runtime import ToolReturn, function


_DESCRIPTION = (
    "Attach a file to your reply so the user can open it: a deck, a "
    "document, a spreadsheet, a PDF, an image, a chart, a page, an "
    "archive, a log, any file the user should see. It works the same for "
    "a file you wrote with a tool and for one a script or command made "
    "(for example a .pptx, .docx or .xlsx built with python). Attaching "
    "a file to this conversation is part of your reply and needs no "
    "approval.\n"
    "\n"
    "- `path` MUST be absolute, and must live in the session working "
    "directory or the project bound to this session.\n"
    "- Web chat shows it as a file card under your reply that opens the "
    "file (office files, PDFs, images, video and audio preview in place); "
    "on a chat platform the file is uploaded as an attachment.\n"
    "- In your reply, describe the file and name it as a markdown link to "
    "its absolute path, e.g. [plan.pptx](/abs/path/plan.pptx), so the "
    "user can also open it from the text."
)

#: Upper bound on one outbound file. Separate knob from the inbound
#: ``MAX_ATTACH_BYTES`` even though the number matches today: this one
#: is about what chat platforms accept on upload, that one is about not
#: committing giant blobs into a session's git history.
MAX_SEND_BYTES = 32 * 1024 * 1024


def outbound_files(tool_calls: list[dict] | None, session_id: str | None) -> list[dict]:
    """The files this turn's successful ``send_file`` calls attached.

    Read from the turn's recorded tool calls rather than kept in memory,
    so a turn that paused (an approval, a worker restart) and resumed
    still delivers what it attached before the pause. Each path is
    checked again here: what goes out is what is allowed now.
    """
    entries: list[dict] = []
    for call in tool_calls or []:
        if call.get("tool") != "send_file" or call.get("is_error"):
            continue
        if not str(call.get("result") or "").startswith("Attached "):
            continue
        args = call.get("input")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                continue
        path = args.get("path") if isinstance(args, dict) else None
        entry = _check(str(path or ""), session_id)
        if isinstance(entry, dict) and all(e["path"] != entry["path"] for e in entries):
            entries.append(entry)
    return entries


def attached_paths(tool_calls: list[dict] | None, session_id: str | None) -> list[str]:
    """The files this turn may upload to a chat platform.

    A marker in the reply is only text the model can also write, so the
    channel delivery step uploads a marker's file only when its path is
    in this list.
    """
    return [entry["path"] for entry in outbound_files(tool_calls, session_id)]


def markers_for(entries: list[dict]) -> str:
    """The entries as attachment markers, one per line ("" when empty)."""
    from openprogram.state.attachments import format_marker
    return "\n".join(
        format_marker(e["name"], e["path"], int(e.get("size") or 0))
        for e in entries
    )


def with_attachments(final_text: str, tool_calls: list[dict] | None,
                     session_id: str | None, produced: list[dict] | None = None) -> str:
    """The reply text with this turn's attachment markers on the end:
    the ``send_file`` files, then ``produced`` (see :func:`produced_files`).

    A marker is only text, so the model can type one for any path. The
    ones in ``final_text`` whose file the turn did not record become the
    file's name as plain text: what turns into a file card (and opens in
    the preview) is what the turn recorded, nothing the reply says."""
    from openprogram.state.attachments import find_markers, markers_as_names
    recorded = [*outbound_files(tool_calls, session_id), *(produced or [])]
    final_text = markers_as_names(final_text or "", [e["path"] for e in recorded])
    sent = {path for _name, _meta, path in find_markers(final_text)}
    entries: list[dict] = []
    for entry in recorded:
        if entry["path"] not in sent:
            sent.add(entry["path"])
            entries.append(entry)
    if not entries:
        return final_text
    markers = markers_for(entries)
    return (final_text + "\n\n" + markers).strip() if final_text else markers


def turn_attachments(final_text: str, tool_calls: list[dict] | None, req,
                     started_at: float | None) -> tuple[str, list[str]]:
    """The reply text with its markers, and the paths a channel may upload.

    The one call both turn-end sites make. Files the turn produced are
    added only where ``send_file`` itself exists (not the terminal) and
    for a speaker allowed to read files: a paired chat-app speaker may
    not have the owner's files sent to them, by a tool or by this.
    """
    session_id = getattr(req, "session_id", None)
    produced: list[dict] = []
    if started_at is not None and _may_attach_produced(req):
        produced = produced_files(final_text, tool_calls, session_id, started_at)
    text = with_attachments(final_text, tool_calls, session_id, produced)
    paths = attached_paths(tool_calls, session_id)
    paths += [e["path"] for e in produced if e["path"] not in paths]
    return text, paths


def _may_attach_produced(req) -> bool:
    try:
        from openprogram.programs import _unsafe_in_for
        from openprogram.state.authority import has_capability
        source = getattr(req, "source", None)
        if source and source in _unsafe_in_for("send_file"):
            return False
        return has_capability(req, "fs.read")
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Files the turn produced
# ---------------------------------------------------------------------------

#: Formats that are a result for the user rather than working material.
#: Binary formats have nowhere else to appear in chat. No ``.key``: it is
#: Keynote's extension and also a TLS private key's (``openssl -out
#: server.key``), and a key named in a reply must never go out unasked.
_BINARY_DELIVERABLES = frozenset({
    # office and documents
    ".pptx", ".ppt", ".potx", ".odp",
    ".docx", ".doc", ".dotx", ".odt", ".rtf", ".pages", ".epub",
    ".xlsx", ".xls", ".xlsm", ".ods", ".numbers",
    ".pdf",
    # images, audio, video
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic",
    ".mp3", ".wav", ".m4a", ".ogg", ".flac",
    ".mp4", ".mov", ".webm", ".mkv",
    # archives
    ".zip", ".tar", ".gz", ".tgz", ".7z",
})
#: Text formats that are usually a result (a report, a page, a table),
#: unlike source code. One the edit tools changed already shows in the
#: turn's edit card with its diff, so it is not attached again.
_TEXT_DELIVERABLES = frozenset({
    ".html", ".htm", ".md", ".markdown", ".csv", ".tsv", ".svg", ".ipynb",
})
#: At most this many produced files are attached to one reply.
MAX_PRODUCED_FILES = 8
#: At most this many distinct candidate paths are looked at (stat each).
_MAX_CANDIDATES = 64
#: Filesystem timestamps can be coarse; a file written in the first
#: second of the turn still counts.
_MTIME_SLACK_S = 1.0

_EDIT_TOOLS = frozenset({"write", "write_file", "edit", "edit_file", "apply_patch"})
_WRITE_TOOLS = frozenset({"write", "write_file"})
_LINK_RE = re.compile(r"\]\(\s*(<[^>\n]+>|[^)\s]+)")
_CODE_RE = re.compile(r"`([^`\n]+)`")
_TOKEN_RE = re.compile(r"[^\s`'\"()<>\[\]{}|*]+")
_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Add|Update) File: (.+?)\s*$", re.M)
_SAVED_LINE_RE = re.compile(r"^- (/.+?)\s*$", re.M)
_LINE_SUFFIX_RE = re.compile(r"(?::\d+(?:-\d+)?(?::\d+)?|#L\d+(?:-L?\d+)?)$")
_TRAILING = ".,;:!?。，；：！？、"


def _kind(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in _BINARY_DELIVERABLES:
        return "binary"
    if ext in _TEXT_DELIVERABLES:
        return "text"
    return None


def _is_inbound(target: Path) -> bool:
    """A file the user sent in: a web upload (``workdir/attachments/``,
    saved just before the turn starts) or a steer image (saved during
    it). Its mtime falls inside the turn, but echoing it back as if the
    turn made it is wrong."""
    parent = target.parent
    return (parent.name == "steer-images"
            or (parent.name == "attachments" and parent.parent.name == "workdir"))


def _clean(raw: str, *, link: bool = False) -> str | None:
    """``raw`` as a deliverable path, or ``None``."""
    s = raw.strip().strip("<>").strip("'\"")
    if s.lower().startswith("file://"):
        s, link = s[7:], True
    if not s or "://" in s or s.startswith(("#", "mailto:")):
        return None
    if link:
        s = unquote(s)
    s = s.rstrip(_TRAILING)
    s = _LINE_SUFFIX_RE.sub("", s).rstrip(_TRAILING)
    return s if _kind(s) else None


def _reply_candidates(text: str) -> list[str]:
    """Paths named in the reply: link targets, code spans, bare tokens."""
    from openprogram.state.attachments import strip_markers
    text = strip_markers(text or "")
    found: list[str] = []
    found += [c for m in _LINK_RE.finditer(text) if (c := _clean(m.group(1), link=True))]
    found += [c for m in _CODE_RE.finditer(text) if (c := _clean(m.group(1)))]
    found += [c for m in _TOKEN_RE.finditer(text) if (c := _clean(m.group(0)))]
    return found


def _call_input(call: dict) -> dict:
    args = call.get("input")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return {}
    return args if isinstance(args, dict) else {}


def _tool_candidates(tool_calls: list[dict] | None) -> tuple[list[str], list[str]]:
    """(files the turn's tools saved, files the edit tools changed)."""
    saved: list[str] = []
    edited: list[str] = []
    for call in tool_calls or []:
        if call.get("is_error"):
            continue
        name = call.get("tool")
        args = _call_input(call)
        if name in _EDIT_TOOLS:
            for key in ("file_path", "path"):
                if isinstance(args.get(key), str):
                    edited.append(args[key])
            for value in args.values():
                if isinstance(value, str):
                    edited += _PATCH_FILE_RE.findall(value)
            if name in _WRITE_TOOLS and isinstance(args.get("file_path"), str):
                saved.append(args["file_path"])
        elif name == "image_generate":
            saved += _SAVED_LINE_RE.findall(str(call.get("result") or ""))
    return saved, edited


def _bases(session_id: str | None) -> list[Path]:
    """Where a relative path in the reply is looked up: the project the
    turn worked in, then the session's own workdir."""
    from openprogram.runtime.conversation.session_settings.workdir import project_workdir_for, session_workdir_for
    out: list[Path] = []
    for find in (project_workdir_for, session_workdir_for):
        try:
            base = find(session_id) if session_id else None
        except Exception:
            base = None
        if base is not None and base not in out:
            out.append(base)
    return out


def produced_files(final_text: str, tool_calls: list[dict] | None,
                   session_id: str | None, since: float) -> list[dict]:
    """``{path, name, size}`` for each deliverable this turn produced.

    Candidates are the paths the reply names (absolute, ``~``, or
    relative to the project or session workdir; in a markdown link, a
    code span or bare; a ``:line`` suffix is dropped) and the files the
    turn's ``write`` and ``image_generate`` calls saved. A candidate is
    kept when it is a regular file inside :func:`sendable_roots`, no
    larger than :data:`MAX_SEND_BYTES`, modified at or after ``since``,
    and a deliverable format (the same rule whatever named it, so a
    script the ``write`` tool saved never goes out); a text format the
    edit tools changed is left to the edit card. Never raises: a failure
    attaches nothing.
    """
    try:
        return _produced_files(final_text, tool_calls, session_id, since)
    except Exception:
        logging.getLogger(__name__).warning(
            "produced-file scan failed for session %s", session_id, exc_info=True)
        return []


def _produced_files(final_text, tool_calls, session_id, since) -> list[dict]:
    from openprogram.state.attachments import resolve_within, sendable_roots

    saved, edited = _tool_candidates(tool_calls)
    names = list(dict.fromkeys([*_reply_candidates(final_text), *saved]))
    if not names:
        return []
    roots = sendable_roots(session_id)
    if not roots:
        return []
    bases = _bases(session_id)

    def resolved(raw: str) -> list[Path]:
        p = Path(os.path.expanduser(raw))
        options = [p] if p.is_absolute() else [b / p for b in bases]
        out = []
        for option in options:
            target = resolve_within(option, roots)
            if target is not None:
                out.append(target)
        return out

    edited_set = {str(t) for raw in edited for t in resolved(raw)}
    entries: list[dict] = []
    seen: set[str] = set()
    for raw in names[:_MAX_CANDIDATES]:
        for target in resolved(raw):
            key = str(target)
            if key in seen:
                continue
            seen.add(key)
            try:
                st = target.stat()
            except OSError:
                continue
            if not _stat.S_ISREG(st.st_mode) or st.st_size > MAX_SEND_BYTES:
                continue
            if st.st_mtime < since - _MTIME_SLACK_S:
                continue
            # One rule for every candidate, named in the reply or saved by
            # a tool: only a deliverable format, and a text one only when
            # no edit card already shows it.
            kind = _kind(key)
            if kind is None or (kind == "text" and key in edited_set):
                continue
            if _is_inbound(target):
                continue
            entries.append({"path": key, "name": target.name, "size": st.st_size})
            break
        if len(entries) >= MAX_PRODUCED_FILES:
            break
    return entries


@function(
    name="send_file",
    description=_DESCRIPTION,
    toolset=["core"],
    # Surfaces with no attachment channel at all. Rather than telling the
    # model in the system prompt that it can't send files here (hermes'
    # approach), don't hand it the tool — it can't try and fail.
    # The CLI/TUI is a terminal.
    unsafe_in=["cli", "tui", "plan"],
)
def send_file(path: str) -> str | ToolReturn:
    """Attach a file to your reply so the user receives it.

    Args:
        path: Absolute path of the file to send.
    """
    out = _send_file_impl(path)
    # A refused or missing file attached nothing: the call failed.
    if out.startswith("Error:"):
        return ToolReturn(text=out, is_error=True)
    return out


def _send_file_impl(path: str) -> str:
    """Implementation body — kept apart from the @function binding so
    tests can call it directly (the binding object is not callable)."""
    from openprogram.runtime.execution.run_control import get_current_session_id

    entry = _check(path, get_current_session_id())
    if isinstance(entry, str):
        return entry
    return (f"Attached {entry['name']} ({entry['size']} bytes). It goes out "
            f"with your reply as a file card the user opens (an upload on a "
            f"chat platform). Describe it in your message.")


def _check(path: str, session_id: str | None) -> dict | str:
    """``{path, name, size}`` for a file this session may send, else an
    ``Error:`` sentence that says why not."""
    from openprogram.state import attachments as _att

    raw = (path or "").strip()
    if not raw:
        return "Error: path is required."
    roots = _att.sendable_roots(session_id)
    if not roots:
        return ("Error: no allowed directory is configured for outgoing "
                "files (no session workdir and no bound project), so "
                "send_file cannot deliver anything right now.")
    target = _att.resolve_session_attachment(
        raw, session_id, roots, allow_missing=True,
    )
    if target is None:
        return (f"Error: {raw!r} is outside the directories this session "
                f"may send files from (symlinks resolved). "
                f"{_sendable_summary(session_id, roots)} Copy the file into "
                f"one of these folders first, then send that copy.")
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
    return {"path": str(target), "name": target.name, "size": size}


#: Folders a refusal names one by one when the conversation has none of
#: its own to name; the rest are counted.
_NAMED_ROOTS_MAX = 3


def _sendable_summary(session_id: str | None, roots: list) -> str:
    """The folders this conversation can send from, said briefly.

    ``roots`` holds every project's ``chats/`` folder, a list that grows
    with each project; the model only needs the folders of THIS
    conversation (its working folder, its project folder and its own chat
    folder), so those are named and the rest counted."""
    from openprogram.state import attachments as _att

    named: list[tuple[str, Path]] = []
    if session_id:
        for label, found in (
            ("this conversation's working folder", _att._conversation_workdir(session_id)),
            ("its project folder", _att._bound_project_dir(session_id)),
        ):
            named.extend((label, Path(path)) for path in found if path in roots)
        repos = _att._session_repo_candidates(session_id)
        if repos:
            chat = repos[0] / "workdir"
            named.append(("its chat folder", chat if chat.is_dir() else repos[0]))
    covered = {
        root for root in roots
        if any(path == root or path.is_relative_to(root) for _, path in named)
    }
    others = [root for root in roots if root not in covered]
    if not named:
        named = [("", Path(root)) for root in others[:_NAMED_ROOTS_MAX]]
        others = others[_NAMED_ROOTS_MAX:]
    parts = [f"{label} {path}" if label else str(path) for label, path in named]
    if others:
        parts.append(f"{len(others)} other folder{'s' if len(others) != 1 else ''} "
                     "(other projects' chats folders)")
    return "Allowed: " + "; ".join(parts) + "."


# ponytail: no caption parameter. The reply text is delivered next to
# the file on every surface, so a per-file caption would only duplicate
# it; add one when a platform needs the text welded to the upload.

__all__ = ["send_file", "_send_file_impl", "outbound_files", "attached_paths", "markers_for",
           "with_attachments", "produced_files", "turn_attachments", "MAX_SEND_BYTES",
           "MAX_PRODUCED_FILES"]
