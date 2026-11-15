"""Where memory lives on disk.

The personal memory is the home project's workspace,
``<state>/projects/default/memory``; a project's own memory is
``<state>/projects/<project-id>/memory`` (see :mod:`.project_layer`).
Inside a workspace, ``topics/`` holds the memory and ``MEMORY.md`` at
the root is the entry point; its core section is rendered from
``topics/core.md``. Footnotes cite chat messages, which the session
store keeps, so a workspace holds no copy of any conversation.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def root() -> Path:
    """The memory workspace, created on first use."""
    from openprogram.state.paths import _restrict_to_owner, get_memory_dir, get_state_dir

    state = get_state_dir()
    from .policy import selected_root
    from .project_layer import active_root
    path = active_root() or selected_root() or get_memory_dir()
    path.mkdir(parents=True, exist_ok=True)
    # ``get_state_dir`` protects the profile root when it creates it, but
    # this is the call that brings it into being on a fresh install — the
    # mkdir above makes both levels — so the mode is set here too rather
    # than left to whichever call happened to come first.
    _restrict_to_owner(state, 0o700)
    _restrict_to_owner(path, 0o700)
    return path


def topics_dir() -> Path:
    return root() / "topics"


def timeline_dir() -> Path:
    return root() / "timeline"


def guide() -> Path:
    """``MEMORY.md``: the map, then the core rendered from
    ``topics/core.md``."""
    return root() / "MEMORY.md"


def core() -> Path:
    """The old root ``core.md``, read only until the next write moves its
    content into ``MEMORY.md``."""
    return root() / "core.md"


def state_dir() -> Path:
    """Bookkeeping that is about memory but is not memory.

    Inside the runtime directory, which the workspace revision ignores.
    A file that changed on every poll would otherwise look like a
    concurrent write to anything holding a revision.
    """
    from .workspace_layout import ensure_runtime_dir

    return ensure_runtime_dir(root())


_WORKSPACE_ID = re.compile(r"w-[0-9a-f]{8}")


def workspace_id() -> str:
    """Stable identity for markers written by this memory workspace."""
    path = state_dir() / "workspace-id"
    try:
        current = path.read_text(encoding="utf-8").strip()
    except OSError:
        current = ""
    if _WORKSPACE_ID.fullmatch(current):
        return current

    from openprogram.state.store.session.atomic_file import atomic_write_text
    from .management.transaction import workspace_write_lock

    with workspace_write_lock(root()):
        try:
            current = path.read_text(encoding="utf-8").strip()
        except OSError:
            current = ""
        if not _WORKSPACE_ID.fullmatch(current):
            current = f"w-{uuid.uuid4().hex[:8]}"
            atomic_write_text(path, current + "\n")
    return current


# What the previous memory layer kept at the workspace root. None of it
# means anything to the current one, and left in place it would show up
# in every listing — one installation had 3934 wiki files.
_SUPERSEDED = ("journal", "wiki", ".state", "index.sqlite")


def _set_aside_superseded(base: Path) -> Path | None:
    """Move the previous layer's files out of the workspace, once.

    Moved rather than deleted, and to a sibling directory rather than a
    subdirectory: inside the workspace it would still be listed, and
    deleting someone's notes to make room for a new format is not a
    migration.
    """
    present = [name for name in _SUPERSEDED if (base / name).exists()]
    if not present:
        return None
    archive = base.parent / f"{base.name}-superseded"
    archive.mkdir(parents=True, exist_ok=True)
    for name in present:
        target = archive / name
        if target.exists():
            # A previous pass already saved a copy; leave it as the
            # record and drop the leftover rather than merging blindly.
            continue
        (base / name).rename(target)
    logger.info(
        "memory: moved the previous layout (%s) to %s",
        ", ".join(present), archive,
    )
    return archive


_OLD_SOURCE_LINK = re.compile(
    r"\]\((?:\.\./)*sources/(?P<provider>[^/)\s]+)/_v2/(?P<file>[^)#\s]+)\.md"
    r"#source-(?P<anchor>[0-9a-f]{16})\)"
)
_OLD_SOURCE_ID = re.compile(
    r'<a id="source-(?P<anchor>[0-9a-f]{16})"></a>\s*\n'
    r"<!-- source-id:(?P<ref>[^\s]+) -->"
)


def cite_chats_directly(base: Path) -> bool:
    """Point footnotes at the chat instead of the old ``sources/`` copy.

    Memory once archived every conversation it cited under ``sources/``
    and linked footnotes there. The chat history is that record, so each
    link becomes the cited message's chat link and the copy goes; Git
    keeps it. A citation whose original was never a chat here, such as an
    imported Claude Code transcript, becomes the link of a chat that does
    not exist, which shows as a deleted original.
    """
    from . import chat_refs
    from .management.transaction import git_commit_state, workspace_write_lock

    sources = base / "sources"
    if not sources.is_dir():
        return False
    with workspace_write_lock(base):
        if not sources.is_dir():
            return False
        refs: dict[str, str] = {}
        for archive in sources.rglob("*.md"):
            text = archive.read_text(encoding="utf-8", errors="replace")
            for match in _OLD_SOURCE_ID.finditer(text):
                refs[match.group("anchor")] = match.group("ref")

        def chat_link(match: re.Match[str]) -> str:
            value = refs.get(match.group("anchor"), "")
            provider, _, rest = value.partition("/")
            thread, _, message = rest.rpartition("/")
            if not (thread and message):
                thread = match.group("file")
                message = "source-" + match.group("anchor")
            if provider and provider != chat_refs.PROVIDER:
                thread = f"{provider}-{thread}"
            return f"]({chat_refs.link(chat_refs.ref(thread, message))})"

        rewritten = 0
        candidates = [base / "MEMORY.md", base / "core.md"]
        for name in ("topics", "timeline"):
            if (base / name).is_dir():
                candidates.extend((base / name).rglob("*.md"))
        for path in candidates:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            updated = _OLD_SOURCE_LINK.sub(chat_link, text)
            if updated != text:
                path.write_text(updated, encoding="utf-8")
                rewritten += 1
        shutil.rmtree(sources)
        try:
            git_commit_state(base, "memory: cite chats directly")
        except Exception as exc:  # noqa: BLE001 - history must not block memory
            logger.debug("memory: citation migration was not committed: %s", exc)
    logger.info(
        "memory: %s now cites chats directly (%d files rewritten)", base, rewritten,
    )
    return True


_MEMORY_FILE_MODE = 0o600
_MEMORY_DIR_MODE = 0o700
_permissions_migrated: set[Path] = set()
_git_initialized: set[Path] = set()


def restrict_workspace_permissions(base: Path) -> int:
    """Make an existing workspace owner-only. Returns files changed.

    A workspace created before memory files were owner-only is still full
    of 0644 transcripts, and nothing rewrites a file that is never edited
    again — so the modes have to be corrected in place rather than waiting
    for the next write to do it.

    Symlinks are skipped rather than followed: ``chmod`` through one
    changes whatever it points at, and a link planted in the workspace
    would be a way to have this walk re-mode a file outside it.
    """
    changed = 0
    for path in [base, *base.rglob("*")]:
        try:
            if path.is_symlink():
                continue
            mode = path.stat().st_mode & 0o777
            wanted = _MEMORY_DIR_MODE if path.is_dir() else _MEMORY_FILE_MODE
            if mode != wanted:
                path.chmod(wanted)
                changed += 1
        except OSError:
            # One unreadable file does not justify leaving the rest of the
            # workspace world-readable.
            continue
    return changed


def _migrate_permissions_once(base: Path) -> None:
    if base in _permissions_migrated:
        return
    _permissions_migrated.add(base)
    changed = restrict_workspace_permissions(base)
    if changed:
        logger.info(
            "memory: restricted %d workspace path(s) to owner-only", changed,
        )


def ensure() -> Path:
    """Create the workspace skeleton if it is not there yet."""
    from .workspace_layout import runtime_dir

    base = root()
    # An initialized workspace already owns its layout. In particular, do not
    # recreate ``topics/`` while a transaction has temporarily moved it into
    # its backup before installing the staged replacement.
    marker = base / ".git" / "openprogram-memory-ready"
    install_backup = base / f"{runtime_dir(base).name}-block-backup"
    has_layout = (base / "topics").is_dir()
    if (base in _git_initialized or marker.is_file()) and (
        has_layout or install_backup.is_dir()
    ):
        _ensure_git_history(base)
        _migrate_permissions_once(base)
        cite_chats_directly(base)
        return base
    _set_aside_superseded(base)
    (base / "topics").mkdir(parents=True, exist_ok=True)
    _ensure_git_history(base)
    _migrate_permissions_once(base)
    cite_chats_directly(base)
    # Nothing seeds the always-on block. It is rendered from
    # ``topics/core.md``, so an empty workspace has no block, and a
    # placeholder written here would look like a master to the render and
    # let it replace a hand-written ``core.md`` that was never moved.
    return base


def _ensure_git_history(base: Path) -> None:
    """Create a private Git repository and snapshot pre-existing memory."""
    from .management.transaction import TransactionError, workspace_write_lock
    from .runtime.state import RuntimeStateStore
    from .workspace_layout import RUNTIME_DIR_NAMES

    if shutil.which("git") is None:
        raise TransactionError(
            "GIT_UNAVAILABLE",
            "Git is required for Memory history but was not found",
            details={"memory_committed": False, "git_committed": False},
        )
    if base in _git_initialized:
        return
    marker = base / ".git" / "openprogram-memory-ready"
    if marker.is_file():
        _git_initialized.add(base)
        return
    try:
        with workspace_write_lock(base):
            if base in _git_initialized or marker.is_file():
                _git_initialized.add(base)
                return
            if not (base / ".git").exists():
                subprocess.run(
                    ["git", "init", "-q"], cwd=base, check=True,
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                )
            marker = base / ".git" / "openprogram-memory-ready"
            for key, value in (
                ("user.name", "OpenProgram Memory"),
                ("user.email", "memory@openprogram.local"),
            ):
                configured = subprocess.run(
                    ["git", "config", "--local", "--get", key],
                    cwd=base,
                    capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                )
                if configured.returncode != 0:
                    subprocess.run(
                        ["git", "config", "--local", key, value],
                        cwd=base,
                        check=True,
                        capture_output=True,
                        text=True, encoding="utf-8", errors="replace",
                    )
            exclude = base / ".git" / "info" / "exclude"
            current_excludes = (
                exclude.read_text(encoding="utf-8")
                if exclude.is_file()
                else ""
            )
            additions = [
                f"/{name}*"
                for name in RUNTIME_DIR_NAMES
                if f"/{name}*" not in current_excludes.splitlines()
            ]
            if additions:
                exclude.parent.mkdir(parents=True, exist_ok=True)
                exclude.write_text(
                    current_excludes.rstrip("\n")
                    + ("\n" if current_excludes else "")
                    + "\n".join(additions)
                    + "\n",
                    encoding="utf-8",
                )
            head = subprocess.run(
                ["git", "rev-parse", "--verify", "HEAD"],
                cwd=base,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
            )
            commit = RuntimeStateStore(base).git_commit(
                "memory: initialize history"
            )
            if commit is None:
                if head.returncode != 0:
                    subprocess.run(
                        [
                            "git", "commit", "-q", "--allow-empty", "-m",
                            "memory: initialize history",
                        ],
                        cwd=base,
                        check=True,
                        capture_output=True,
                        text=True, encoding="utf-8", errors="replace",
                    )
            marker.write_text("ready\n", encoding="utf-8")
            _git_initialized.add(base)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise TransactionError(
            "GIT_UNAVAILABLE",
            f"Memory Git history could not be initialized: {exc}",
            details={"memory_committed": False, "git_committed": False},
        ) from exc
