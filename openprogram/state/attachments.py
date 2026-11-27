"""Chat attachments: one path policy, both directions.

Every file that travels through a chat message is referenced by an
absolute path in one marker, written and read by
:mod:`openprogram.state.attachment_markers`.

The path policy lives here, because "which directories may a chat
attachment come from" is one question asked in two directions:

* :func:`readable_roots` — bytes the LOCAL web UI may fetch back for
  display (``/api/file-raw``). Everything already reachable through the
  files panel, plus the session workdirs and the inbound channel
  attachment directories.
* :func:`sendable_roots` — files the AGENT may hand back to the user
  (the ``send_file`` tool). Deliberately narrower: the session's own
  workdir and the project it is bound to. Nothing else, because this
  direction lets a model name a path and have the system read it out.

A conversation with no project works in the default project's folder,
usually the home directory. Both sets add it as a :class:`WorkdirRoot`,
which refuses dot-folders, secret-looking files and the state directory
under it; ``sendable_roots`` adds it only while the conversation does not
deliver to a chat platform.

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


# ---------------------------------------------------------------------------
# Path policy
# ---------------------------------------------------------------------------

def _state_dir() -> Optional[Path]:
    try:
        from openprogram.state.paths import get_state_dir
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
        import openprogram
        parent = Path(openprogram.__file__).resolve().parent.parent
        if parent.is_dir():
            return parent
    except Exception:  # noqa: BLE001
        return Path(os.getcwd()).resolve()
    return Path(os.getcwd()).resolve()


def _session_project(session_id: str):
    """The project record this session belongs to, or ``None``.

    The registry's session list is a best-effort reverse index and can
    miss a session; the session's own log is the record of which project
    it belongs to.
    """
    from openprogram.state.store.project import project_store as _projects
    proj = _projects.project_for_session(session_id)
    if proj is None:
        project_id = _session_meta(session_id).get("project_id")
        proj = _projects.get_project(project_id) if isinstance(project_id, str) and project_id else None
    return proj


def _bound_project_dir(session_id: str | None) -> list[Path]:
    """The directory of the REAL project this session is bound to.

    Two traps, both verified against a live state dir rather than
    reasoned about:

    * ``project_workdir_for`` falls back to the default project when a
      session is unbound, so it can never answer "is this bound".
    * Every session is auto-bound to the DEFAULT project, whose path is
      the user's home directory. Taking that binding at face value makes
      ``$HOME`` a plain root for every ad-hoc chat. The default project
      is not a project the user pointed the agent at; it is the absence
      of one, and its folder is a :class:`WorkdirRoot` instead
      (:func:`_conversation_workdir`).
    """
    if not session_id:
        return []
    try:
        proj = _session_project(session_id)
    except Exception:  # noqa: BLE001
        return []
    if proj is None or getattr(proj, "is_default", False):
        return []
    path = getattr(proj, "path", None)
    if not path:
        return []
    p = Path(os.path.expanduser(str(path)))
    return [p.resolve()] if p.is_dir() else []


class WorkdirRoot(Path):
    """The folder a conversation with no project works in, as a root.

    That folder is the default project's, usually the home directory, so
    containment alone would admit ``~/.ssh``. :func:`resolve_within`
    asks :meth:`admits` as well: nothing under a dot-folder or a
    dot-file (``~/.ssh``, ``~/.aws``, ``~/.openprogram*``, ``.env``),
    nothing in the system's per-app data folder (``Library`` on macOS,
    ``AppData`` on Windows: keychains, browser profiles and cookies live
    there, and no deliverable does), no file whose name marks a secret,
    and nothing in the state directory, whose conversation folders are
    roots of their own.
    """

    _SECRET_SUFFIXES = frozenset({
        ".key", ".pem", ".p12", ".pfx", ".jks", ".keystore", ".kdbx",
        ".gpg", ".asc", ".ppk", ".ovpn",
    })
    _SECRET_NAMES = re.compile(
        r"^(id_(rsa|dsa|ecdsa|ed25519)(_sk)?(\.pub)?|credentials?|secrets?"
        r"|tokens?|passwords?)(\.[a-z0-9]+)?$",
        re.IGNORECASE,
    )
    _APP_DATA = frozenset({"library", "appdata"})

    def admits(self, target: Path) -> bool:
        try:
            parts = target.relative_to(self).parts
        except ValueError:
            return False
        if any(part.startswith(".") or part.lower() in self._APP_DATA
               for part in parts):
            return False
        name = target.name
        if (target.suffix.lower() in self._SECRET_SUFFIXES
                or self._SECRET_NAMES.match(name)):
            return False
        state = _state_dir()
        if state is not None and (target == state or target.is_relative_to(state)):
            return False
        return True


def _conversation_workdir(session_id: str | None) -> list[Path]:
    """The folder a conversation with no project works in, guarded.

    Only for a conversation that exists and is not bound to a real
    project: a bound one already has its project as a root. Its folder is
    the default project's path, where the turn's tools run
    (``openprogram.runtime.conversation.session_settings.workdir.apply_default_workdir``),
    so a deck the agent built there can be opened and attached.
    """
    if not session_id or not _session_repo_candidates(session_id):
        return []
    try:
        proj = _session_project(session_id)
        if proj is not None and not getattr(proj, "is_default", False):
            return []
        if proj is None:
            from openprogram.state.store.project import project_store as _projects
            proj = _projects.get_default_project()
    except Exception:  # noqa: BLE001
        return []
    path = getattr(proj, "path", None)
    if not path:
        return []
    p = Path(os.path.expanduser(str(path))).resolve()
    # A default folder set to the filesystem root (or a service account
    # whose home is ``/``) would make the whole disk a root.
    if p == Path(p.anchor) or not p.is_dir():
        return []
    return [WorkdirRoot(p)]


def _delivers_to_channel(session_id: str) -> bool:
    """Whether this conversation's replies also go to a chat platform:
    an owner chat bound to one (``channel``), or one that arrived from a
    platform contact (``peer_id``). The second stays true after its
    binding is cleared, because the contact's next message is routed to
    the same conversation and its reply, files included, goes back to
    them. An unreadable record counts as delivering, so it gets the
    narrow set."""
    try:
        if not _session_repo_candidates(session_id):
            return True
        meta = _session_meta(session_id)
        return bool(meta.get("channel") or meta.get("peer_id"))
    except Exception:  # noqa: BLE001
        return True


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


def _chats_roots(state: Path | None) -> list[Path]:
    """Every project's ``chats/`` folder: where conversations keep files."""
    if state is None:
        return []
    root = state / "projects"
    try:
        return [(entry / "chats").resolve() for entry in sorted(root.iterdir())
                if not entry.name.startswith(".") and (entry / "chats").is_dir()]
    except OSError:
        return []


def _session_meta(session_id: str) -> dict:
    """The session log's meta, read from disk without opening a
    SessionStore (see :func:`_session_repo_candidates`)."""
    from openprogram.state.store.session.files.session_log import SessionLog
    for repo in _session_repo_candidates(session_id):
        meta = SessionLog(repo).read_meta()
        if isinstance(meta, dict):
            return meta
    return {}


def readable_roots(session_id: str | None = None) -> list[Path]:
    """Directories whose bytes the local web UI may fetch for display."""
    roots = [project_root()]
    roots.extend(_chats_roots(_state_dir()))
    roots.extend(_channel_attachment_dirs())
    roots.extend(_bound_project_dir(session_id))
    roots.extend(_conversation_workdir(session_id))
    return roots


def _session_repo_candidates(session_id: str) -> list[Path]:
    """Return the unambiguous current repo for a session id.

    This is deliberately filesystem-only. Resolving an attachment for a
    read-only HTTP request must not instantiate ``SessionStore`` (whose
    startup maintenance can rewrite its registry). The durable location map
    is preferred, then every project's ``chats/`` folder is considered.
    """
    if not session_id or "/" in session_id or "\\" in session_id:
        return []
    state = _state_dir()
    if state is None:
        return []
    from openprogram.state.store.session.files.placement import LOCATIONS_FILE

    root = state / "projects"
    if (root / ".deleted" / session_id).is_file():
        return []
    candidates: list[Path] = []
    try:
        locations = json.loads((root / LOCATIONS_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        locations = {}
    recorded = locations.get(session_id) if isinstance(locations, dict) else None
    if isinstance(recorded, str):
        candidates.append(Path(recorded).expanduser())
    candidates.extend(chats / session_id for chats in _chats_roots(state))
    valid: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            root_resolved = root.resolve()
            if not resolved.is_relative_to(root_resolved):
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            if (resolved / "main.jsonl").is_file() or (resolved / "agents").is_dir():
                valid.append(resolved)
        except (OSError, ValueError):
            continue
    # A stale location and a newly discovered nested repo may coexist. Do not
    # guess if more than one current repo can claim the same session id.
    return valid if len(valid) == 1 else []


def _kept_media(raw: Path, session_id: str) -> Optional[Path]:
    """A picture a conversation keeps (``chats/<id>/media/<sha>.<ext>``, the path its
    marker was written with) in the conversation's folder now: it moves with the
    conversation to another project."""
    parts = raw.parts
    if len(parts) < 4 or parts[-4:-1] != ("chats", session_id, "media") or parts[-1] in {".", ".."}:
        return None
    repos = _session_repo_candidates(session_id)
    if len(repos) != 1:
        return None
    candidate = repos[0] / "media" / parts[-1]
    try:
        resolved = candidate.resolve()
        if resolved.parent != (repos[0] / "media").resolve() or not resolved.is_file():
            return None
    except (OSError, ValueError):
        return None
    return resolved


def resolve_session_attachment(
    path: str | os.PathLike,
    session_id: str | None,
    roots: Iterable[Path],
    *,
    allow_missing: bool = False,
) -> Optional[Path]:
    """Resolve an attachment path, including one legacy session alias.

    Historical markers may contain ``<project>/.openprogram/sessions/<id>/
    workdir/attachments/<relative>``. Only that exact shape, with the
    caller's session id, can be rebased. The canonical session repo must be
    unique, live, application-owned, and contain the same relative suffix.

    A legacy path is rebased before the old path is considered. This matters
    when migration left both copies reachable: the canonical session copy is
    the durable attachment, while the old source may be stale or unavailable.
    """
    roots = tuple(roots)
    raw: Path | None = None
    marker_start: int | None = None
    legacy_start: int | None = None
    try:
        raw = Path(os.path.expanduser(str(path)))
        parts = raw.parts
        marker_start = next(
            (i for i in range(len(parts) - 4)
             if parts[i:i + 2] == (".openprogram", "sessions")
             and parts[i + 3:i + 5] == ("workdir", "attachments")),
            None,
        )
        if (marker_start is not None and session_id
                and parts[marker_start + 2] != session_id):
            return None
        if raw.is_absolute() and not any(part in {".", ".."} for part in parts):
            legacy_start = marker_start
        if (legacy_start is not None and session_id
                and parts[legacy_start + 2] != session_id):
            return None
    except (OSError, ValueError):
        raw = None
    target = resolve_within(path, roots)
    if (target is None or not target.is_file()) and session_id and raw is not None:
        moved = _kept_media(raw, session_id)
        if moved is not None:
            return moved
    if legacy_start is None or not session_id:
        if target is not None and target.is_file():
            return target
        return target if allow_missing else None
    relative = Path(*parts[legacy_start + 5:])
    if not relative.parts or any(part in {".", ".."} for part in relative.parts):
        return target if allow_missing else None
    repos = _session_repo_candidates(session_id)
    if len(repos) != 1:
        if target is not None and target.is_file():
            return target
        return target if allow_missing else None
    repo_root = repos[0].resolve()
    attachment_root = (repo_root / "workdir" / "attachments").resolve()
    try:
        if not attachment_root.is_relative_to(repo_root):
            return None
    except ValueError:
        return None
    candidate = (attachment_root / relative).resolve()
    try:
        if not candidate.is_relative_to(attachment_root):
            return None
    except ValueError:
        return None
    if resolve_within(candidate, roots) != candidate:
        return None
    if candidate.is_file():
        return candidate
    if target is not None and target.is_file():
        return target
    return target if allow_missing else None


def sendable_roots(session_id: str | None = None) -> list[Path]:
    """Directories the agent may send a file OUT of.

    Narrower than :func:`readable_roots` by design — this is the
    direction where a model names a path and the system reads it out to
    a chat platform, so it covers only the two places the agent's own
    work legitimately lands: the per-session workdir (its cwd, where
    ``write`` / ``image_generate`` / uploads put things) and the project
    the session is bound to. Not the OpenProgram checkout, not the
    inbound channel directories, not the rest of the state directory.

    A conversation with no project also gets the folder it works in (a
    :class:`WorkdirRoot`, so dot-folders and secrets stay out), but only
    while its replies stay in the owner's App: a conversation that
    delivers to a chat platform keeps the two places above, and the home
    folder is never readable out to WeChat or Telegram.
    """
    roots: list[Path] = list(_chats_roots(_state_dir()))
    roots.extend(_bound_project_dir(session_id))
    if session_id and not _delivers_to_channel(session_id):
        roots.extend(_conversation_workdir(session_id))
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
                if isinstance(root, WorkdirRoot) and not root.admits(target):
                    continue
                return target
        except ValueError:
            continue
    return None
