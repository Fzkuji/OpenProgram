"""Writes a sandboxed command may never make, whatever its writable roots.

A sandboxed command can escape by writing a file that something outside
the sandbox runs later: a shell startup file, a git hook, an editor's task
file, a login agent, or OpenProgram's own config, which could switch the
sandbox off for the next turn. The worker's working directory is the home
directory, so a chat without a project folder has home as its writable
root and would reach every one of these. The list follows Anthropic's
sandbox-runtime ("mandatory deny paths"), plus OpenProgram's own state.

Three kinds of rule, applied on top of any policy:

* names, anywhere: a file or folder with one of these names is never
  writable, in the project or in home (``**/.zshrc``);
* home folders: whole trees under home that hold startup configuration
  (``~/.ssh``, ``~/Library/LaunchAgents``, the OpenProgram state folders);
  a writable root that sits inside one of them (a session worktree under
  ``~/.openprogram/worktrees``) stays writable, but the name rules still
  hold inside it;
* the repository the command starts in: its ``.git/hooks`` and
  ``.git/config`` are never writable. Only the existing repository is
  covered, so ``git init`` and ``git clone`` keep working; a hook in a new
  repository runs only if the user later works in it.

An approved escalation (writable root ``/``) lifts all three, since the
user approved that one command running outside the write boundary.
"""
from __future__ import annotations

import glob
import os

# Names that something outside the sandbox reads and acts on.
PROTECTED_NAMES: tuple[str, ...] = (
    # Shell startup files.
    ".bashrc", ".bash_profile", ".bash_login", ".profile",
    ".zshrc", ".zprofile", ".zshenv", ".zlogin",
    # Git configuration that can name programs to run.
    ".gitconfig", ".gitmodules",
    # Tool configuration that runs commands.
    ".ripgreprc", ".mcp.json",
)
PROTECTED_NAME_TREES: tuple[str, ...] = (
    ".vscode", ".idea",
    os.path.join(".claude", "commands"), os.path.join(".claude", "agents"),
    os.path.join(".claude", "hooks"),
)
PROTECTED_NAME_FILES: tuple[str, ...] = (
    os.path.join(".claude", "settings.json"),
    os.path.join(".claude", "settings.local.json"),
)

# Folders under home whose contents run at login or configure tools.
HOME_TREES: tuple[str, ...] = (
    ".ssh",
    os.path.join("Library", "LaunchAgents"),
    os.path.join(".config", "autostart"),
    os.path.join(".config", "systemd"),
    os.path.join(".config", "git"),
    ".claude",
)


def lifted(writable_roots) -> bool:
    """True when the policy already writes everywhere (an escalation)."""
    return "/" in tuple(writable_roots)


def _state_dirs() -> list[str]:
    """Every OpenProgram state folder: the active one and each profile's."""
    home = os.path.expanduser("~")
    found = set(glob.glob(os.path.join(home, ".openprogram")))
    found.update(glob.glob(os.path.join(home, ".openprogram-*")))
    try:
        from openprogram.state.paths import get_state_dir
        found.add(str(get_state_dir()))
    except Exception:  # noqa: BLE001 — a broken state module must not open the floor
        pass
    return sorted(path for path in found if os.path.isdir(path) or not os.path.exists(path))


def home_trees() -> list[str]:
    """Absolute folders whose whole tree is write-protected."""
    home = os.path.expanduser("~")
    trees = [os.path.join(home, name) for name in HOME_TREES]
    trees += _state_dirs()
    return list(dict.fromkeys(trees))


def repository_git_paths(cwd: str) -> list[str]:
    """``.git/hooks`` and ``.git/config`` of the repository holding ``cwd``.

    A linked worktree's ``.git`` is a file and its repository lies outside
    the writable roots already, so only a real ``.git`` folder counts.
    """
    current = os.path.realpath(cwd)
    while True:
        git_dir = os.path.join(current, ".git")
        if os.path.lexists(git_dir):
            if os.path.isdir(git_dir) and not os.path.islink(git_dir):
                return [os.path.join(git_dir, "hooks"), os.path.join(git_dir, "config")]
            return []
        parent = os.path.dirname(current)
        if parent == current:
            return []
        current = parent


def name_globs() -> list[str]:
    """Glob patterns for the name rules, in the sandbox's glob syntax."""
    globs = [f"**/{name}" for name in PROTECTED_NAMES]
    globs += [f"**/{name}/**" for name in PROTECTED_NAME_TREES]
    globs += [f"**/{name}" for name in PROTECTED_NAME_FILES]
    return [pattern.replace(os.sep, "/") for pattern in globs]


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def nested_roots(roots: list[str]) -> list[str]:
    """Writable roots that sit inside a protected home folder."""
    trees = [os.path.realpath(tree) for tree in home_trees()]
    return [root for root in roots
            if root != "/" and any(_inside(root, tree) and root != tree for tree in trees)]


def concrete_paths(cwd: str, roots: list[str]) -> tuple[list[str], list[str]]:
    """Existing paths for backends that mount instead of matching globs.

    Returns ``(trees, names)``: protected home folders, then the name-rule
    paths found in the top of each writable root and in home, plus the
    repository's git paths. bubblewrap can only cover a path that exists,
    so a protected name created later inside a deep folder is not covered
    on Linux; the macOS profile matches by pattern and has no such gap.
    """
    trees = [os.path.realpath(tree) for tree in home_trees() if os.path.exists(tree)]
    names: list[str] = []
    places = [os.path.realpath(os.path.expanduser("~")), *(r for r in roots if r != "/")]
    for place in dict.fromkeys(places):
        for name in (*PROTECTED_NAMES, *PROTECTED_NAME_TREES, *PROTECTED_NAME_FILES):
            path = os.path.join(place, name)
            if os.path.lexists(path):
                names.append(os.path.realpath(path))
    names += [path for path in repository_git_paths(cwd) if os.path.lexists(path)]
    return trees, list(dict.fromkeys(names))


def denial(target: str, cwd: str, roots: list[str]) -> str | None:
    """Why a direct write to ``target`` is refused, or None."""
    import re
    from openprogram.host.sandbox import _glob_to_regex, _regex_path
    real = os.path.realpath(target)
    nested = nested_roots(roots)
    for tree in home_trees():
        tree = os.path.realpath(tree)
        if _inside(real, tree) and not any(_inside(real, root) for root in nested):
            return f"{tree} holds startup configuration and is never writable in the sandbox"
    for path in repository_git_paths(cwd):
        if _inside(real, os.path.realpath(path)):
            return f"{path} runs or configures git outside the sandbox and is never writable"
    normalized = _regex_path(real)
    for pattern in name_globs():
        regex = _glob_to_regex(pattern)
        if regex and re.match(regex, normalized):
            return f"{real} is read by tools outside the sandbox and is never writable"
    return None
