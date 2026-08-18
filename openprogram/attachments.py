"""Chat attachments — one marker lexicon, one path policy, both directions.

Every file that travels through a chat message is referenced by an
absolute path in a single marker::

    [attachment: <name> (<ext>, <N> KB[, <count>]) @json "<abs path>"]

The JSON string makes every legal path delimiter and trailing space
reversible. The historical unquoted ``@ <path>`` spelling remains readable.
The current spelling is written by all four producers — browser upload,
``@``-mention, inbound channel attachment, and the agent's own
``send_file`` — and read by all four consumers: the web chip parser
(``apps/web/components/chat/messages/user-attachments.tsx``), the sidebar
title stripper, the channel delivery step, and this module. Adding a
fifth producer means calling :func:`format_marker`, not inventing a
fifth spelling. Divergence here is not cosmetic: a marker the chip
regex misses is rendered to the user as raw prose.

Path policy lives here too, because "which directories may a chat
attachment come from" is one question asked in two directions:

* :func:`readable_roots` — bytes the LOCAL web UI may fetch back for
  display (``/api/file-raw``). Everything already reachable through the
  files panel, plus the session workdirs and the inbound channel
  attachment directories.
* :func:`sendable_roots` — files the AGENT may hand back to the user
  (the ``send_file`` tool). Deliberately narrower: the session's own
  workdir and the project it is bound to. Nothing else, because this
  direction lets a model name a path and have the system read it out.

Both go through :func:`resolve_within`, which fully resolves symlinks
BEFORE testing containment — a symlink inside an allowed root pointing
at ``~/.ssh/id_rsa`` must not pass. Same guard the reference harnesses
use (hermes ``validate_media_delivery_path``, weclaw's allowed root).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable, Optional


#: Names/paths are interpolated into a bracketed marker, so a literal
#: bracket or paren inside a filename would truncate it at parse time.
_MARKER_UNSAFE = re.compile(r"[\[\]()]+")

#: Current marker uses a JSON string so every absolute-path character,
#: including ``]``, quotes, backslashes, and trailing spaces, round-trips.
JSON_MENTION_RE = re.compile(
    r"\[attach(?:ed|ment):\s*([^()\[\]]+?)\s*"
    r"\(([^,)]+),\s*([\d.]+)\s*KB(?:,\s*([^)]+))?\)"
    r"\s*@json\s*(\"(?:\\.|[^\"\\])*\")"
    r"(?:\s*@previewjson\s*(\"(?:\\.|[^\"\\])*\"))?\]"
)
#: Historical unquoted marker, retained for stored conversations.
MENTION_RE = re.compile(
    r"\[attach(?:ed|ment):\s*([^()\[\]]+?)\s*"
    r"\(([^,)]+),\s*([\d.]+)\s*KB(?:,\s*([^)]+))?\)"
    r"\s*@(?!json\s)\s*([^\]]+)\]"
)


def safe_marker_text(value: str) -> str:
    """Strip the characters that would truncate a marker at parse time."""
    return _MARKER_UNSAFE.sub("_", (value or "").strip()) or "file"


def kb_of(size_bytes: int) -> int:
    """Size in KB as the marker spells it — matching the web composer's
    ``Math.max(1, Math.round(bytes / 1024))`` so an upload's optimistic
    chip and the stored marker agree."""
    return max(1, round((size_bytes or 0) / 1024))


def ext_of(name: str, mime: str = "") -> str:
    """The marker's type word: the filename extension, else the mime
    subtype, else ``file``."""
    ext = os.path.splitext(name or "")[1].lstrip(".").lower()
    if ext:
        return safe_marker_text(ext)
    sub = (mime or "").partition(";")[0].rpartition("/")[2].strip().lower()
    return safe_marker_text(sub) if sub else "file"


def format_marker(
    name: str, path: str | os.PathLike, size_bytes: int,
    mime: str = "", count: str = "",
    preview_path: str | os.PathLike | None = None,
) -> str:
    """One attachment marker. ``count`` is the optional scope badge
    ("500 pages" / "4210 lines") the web chip shows as a third field."""
    safe_name = safe_marker_text(name or os.path.basename(str(path)))
    extra = f", {count}" if count else ""
    encoded_path = json.dumps(str(path), ensure_ascii=False)
    preview = (f" @previewjson {json.dumps(str(preview_path), ensure_ascii=False)}"
               if preview_path is not None else "")
    return (f"[attachment: {safe_name} "
            f"({ext_of(safe_name, mime)}, {kb_of(size_bytes)} KB{extra})"
            f" @json {encoded_path}{preview}]")


def find_markers(text: str) -> list[tuple[str, str, str]]:
    """``[(whole marker, display name, absolute path), …]`` in ``text``."""
    found: list[tuple[int, str, str, str]] = []
    for match in JSON_MENTION_RE.finditer(text or ""):
        try:
            path = json.loads(match.group(5))
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(path, str):
            found.append((match.start(), match.group(0),
                          match.group(1).strip(), path))
    for match in MENTION_RE.finditer(text or ""):
        found.append((match.start(), match.group(0), match.group(1).strip(),
                      match.group(5).strip()))
    found.sort(key=lambda item: item[0])
    return [(whole, name, path) for _, whole, name, path in found]


def strip_markers(text: str) -> str:
    """Remove current and historical path-bearing attachment markers."""
    matches = [*JSON_MENTION_RE.finditer(text or ""),
               *MENTION_RE.finditer(text or "")]
    for match in sorted(matches, key=lambda item: item.start(), reverse=True):
        text = text[:match.start()] + text[match.end():]
    return text


# ---------------------------------------------------------------------------
# Path policy
# ---------------------------------------------------------------------------

def _state_dir() -> Optional[Path]:
    try:
        from openprogram.paths import get_state_dir
        return Path(get_state_dir()).resolve()
    except Exception:  # noqa: BLE001
        return None


def project_root() -> Path:
    """The OpenProgram checkout the worker is serving, in lookup order:

      1. ``OPENPROGRAM_PROJECT_ROOT`` (deployment override).
      2. The directory containing ``openprogram/``.
      3. Process cwd.
    """
    env = os.environ.get("OPENPROGRAM_PROJECT_ROOT")
    if env:
        p = Path(os.path.expanduser(env)).resolve()
        if p.is_dir():
            return p
    try:
        from openprogram.updater.detect import repo_root
        parent = repo_root()
        if parent is not None:
            return parent
    except Exception:  # noqa: BLE001
        return Path(os.getcwd()).resolve()
    return Path(os.getcwd()).resolve()


def _bound_project_dir(session_id: str | None) -> list[Path]:
    """The directory of the REAL project this session is bound to.

    Two traps, both verified against a live state dir rather than
    reasoned about:

    * ``project_workdir_for`` falls back to the default project when a
      session is unbound, so it can never answer "is this bound".
    * Every session is auto-bound to the DEFAULT project, whose path is
      the user's home directory. Taking that binding at face value makes
      ``$HOME`` an allowed root for every ad-hoc chat — in the outbound
      direction that is the whole home tree readable out to a chat
      platform. The default project is not a project the user pointed
      the agent at; it is the absence of one.
    """
    if not session_id:
        return []
    try:
        from openprogram.store.project import project_store as _projects
        proj = _projects.project_for_session(session_id)
    except Exception:  # noqa: BLE001
        return []
    if proj is None or getattr(proj, "is_default", False):
        return []
    path = getattr(proj, "path", None)
    if not path:
        return []
    p = Path(os.path.expanduser(str(path)))
    return [p.resolve()] if p.is_dir() else []


def _channel_attachment_dirs() -> list[Path]:
    """``<state>/channels/*/accounts/*/attachments`` — where inbound
    Telegram/Discord/Slack files land. Globbed to the attachment leaf on
    purpose: ``credentials.json`` is that directory's sibling, so the
    account directory itself must never become a served root.
    """
    state = _state_dir()
    if state is None:
        return []
    try:
        return [p.resolve() for p in
                (state / "channels").glob("*/accounts/*/attachments")
                if p.is_dir()]
    except OSError:
        return []


def readable_roots(session_id: str | None = None) -> list[Path]:
    """Directories whose bytes the local web UI may fetch for display."""
    roots = [project_root()]
    state = _state_dir()
    if state is not None and (state / "sessions").is_dir():
        roots.append((state / "sessions").resolve())
    roots.extend(_channel_attachment_dirs())
    roots.extend(_bound_project_dir(session_id))
    return roots


def sendable_roots(session_id: str | None = None) -> list[Path]:
    """Directories the agent may send a file OUT of.

    Narrower than :func:`readable_roots` by design — this is the
    direction where a model names a path and the system reads it out to
    a chat platform, so it covers only the two places the agent's own
    work legitimately lands: the per-session workdir (its cwd, where
    ``write`` / ``image_generate`` / uploads put things) and the project
    the session is bound to. Not the OpenProgram checkout, not the
    inbound channel directories, not the rest of the state directory.
    """
    roots: list[Path] = []
    state = _state_dir()
    if state is not None and (state / "sessions").is_dir():
        roots.append((state / "sessions").resolve())
    roots.extend(_bound_project_dir(session_id))
    return roots


def resolve_within(path: str | os.PathLike, roots: Iterable[Path]) -> Optional[Path]:
    """``path`` fully resolved, or ``None`` when it lands outside ``roots``.

    ``Path.resolve()`` runs FIRST so a symlink planted inside an allowed
    root cannot point the read at something outside it — containment is
    tested against the real target, never the name used to reach it.
    """
    try:
        target = Path(os.path.expanduser(str(path))).resolve()
    except (OSError, ValueError):
        return None
    for root in roots:
        try:
            if target == root or target.is_relative_to(root):
                return target
        except ValueError:
            continue
    return None
