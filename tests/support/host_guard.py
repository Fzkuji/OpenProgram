"""Stop the suite from changing the checkout it runs from or the person's own state.

The suite runs inside a real repository (often the shared ``dev`` checkout) on
a machine where ``~/.openprogram`` and ``~/.openprogram-dev`` hold the stable
and development instances' data. A test that runs ``git worktree add`` from the
process's working directory, or ``git init --bare`` on a path that resolves to
the checkout's ``.git``, or that writes a log through a path captured before
HOME was redirected, changes those for real: branches pile up under
``op/wt/``, ``core.bare`` flips and the checkout stops working, a test's
events land in the stable instance's log.

``install`` adds a process-wide audit hook (``sys.addaudithook``) that refuses
three things while a test runs and records which test tried:

* a git command that would change the protected repository — its working tree
  or its common ``.git`` directory — through the process's working directory,
  ``-C``, ``--git-dir`` or ``GIT_DIR``; read-only commands pass;
* opening for writing, creating, renaming, removing or changing the mode of any
  path inside the protected ``.git`` directory or the person's real state
  directories (``~/.openprogram*``), and opening a SQLite database there;
* starting a Python or ``openprogram`` child process whose environment drops
  ``HOME`` or points it back at the person's real home, since that child would
  resolve the real state directory.

The refused operation raises ``RealStateWrite`` (a ``PermissionError``), and
the test that made it fails at teardown even if the code under test swallowed
the error. ``snapshot`` / ``compare`` are the session-end backstop for child
processes the hook cannot see.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

# Git reads these before anything else; inherited from a hook or a shell that
# set them, they would point every ``git`` a test runs at the real repository.
GIT_LOCATION_VARIABLES = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
    "GIT_PREFIX",
)

# Subcommands that never change a repository (``status`` may refresh the
# index's stat cache, which changes no content).
_READ_ONLY = frozenset({
    "rev-parse", "status", "log", "diff", "show", "ls-files", "ls-tree", "cat-file",
    "for-each-ref", "describe", "merge-base", "rev-list", "show-ref", "grep", "blame",
    "check-ignore", "check-attr", "name-rev", "shortlog", "var", "help", "version",
    "diff-tree", "diff-index", "diff-files", "count-objects", "verify-commit", "archive",
    "ls-remote", "cherry", "range-diff", "check-ref-format", "show-branch", "fsck",
    "cat-file", "hash-object", "patch-id", "mailinfo", "whatchanged", "annotate",
})
_CONFIG_READS = frozenset({
    "--get", "--get-all", "--get-regexp", "--get-urlmatch", "--list", "-l",
    "--get-color", "--get-colorbool", "get", "list",
})
_CONFIG_WRITES = frozenset({
    "--unset", "--unset-all", "--add", "--replace-all", "--rename-section",
    "--remove-section", "--edit", "-e",
})
_LIST_FLAGS = frozenset({"--list", "-l", "--show-current", "-a", "-r", "--all", "--remotes",
                         "-v", "-vv", "--verbose", "--contains", "--merged", "--no-merged",
                         "--points-at", "--format", "--sort"})


class RealStateWrite(PermissionError):
    """A test reached for the real repository or the person's real state."""


class Guard:
    def __init__(self, repo_tops: list[Path], git_common_dir: Optional[Path],
                 host_home: Path, test_home: Path, guard_state: bool):
        self.repo_top = tuple(v for top in repo_tops for v in _variants(top))
        self.git_dir = _variants(git_common_dir)
        self.host_home = _variants(host_home)
        self.state_prefixes = tuple(p + os.sep + ".openprogram" for p in self.host_home) if guard_state else ()
        self.test_home = test_home
        self.violations: list[str] = []
        self._lock = threading.Lock()
        self._busy = threading.local()
        self.current_test = "<collection>"

    # -- bookkeeping -------------------------------------------------------

    def refuse(self, what: str) -> None:
        message = f"{self.current_test}: {what}"
        with self._lock:
            self.violations.append(message)
        raise RealStateWrite(f"test isolation: {what} (see tests/support/host_guard.py)")

    def take(self) -> list[str]:
        with self._lock:
            taken, self.violations = self.violations, []
        return taken

    # -- the hook ----------------------------------------------------------

    def __call__(self, event: str, args) -> None:
        if event not in _EVENTS or getattr(self._busy, "on", False):
            return
        self._busy.on = True
        try:
            _EVENTS[event](self, args)
        finally:
            self._busy.on = False

    def _protected_path(self, path) -> Optional[str]:
        if isinstance(path, int) or path is None:
            return None
        try:
            text = os.fsdecode(os.fspath(path))
        except TypeError:
            return None
        text = os.path.abspath(text)
        for prefix in self.git_dir:
            if text == prefix or text.startswith(prefix + os.sep):
                return text
        for prefix in self.state_prefixes:
            # ``~/.openprogram`` and every ``~/.openprogram-<profile>``.
            if text.startswith(prefix):
                rest = text[len(prefix):]
                if not rest or rest[0] in (os.sep, "-"):
                    return text
        return None

    def on_open(self, args) -> None:
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
        writing = False
        if isinstance(mode, str):
            writing = any(ch in mode for ch in "wax+")
        if isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):
            writing = True
        if writing and (hit := self._protected_path(path)):
            self.refuse(f"opened {hit} for writing")

    def _change(self, paths, dir_fds) -> None:
        for path, dir_fd in zip(paths, dir_fds):
            # A name relative to an open directory (``shutil.rmtree`` walks a
            # temporary tree that way) is not relative to the working directory.
            if dir_fd is not None and not os.path.isabs(os.fsdecode(path)):
                continue
            if hit := self._protected_path(path):
                self.refuse(f"changed {hit}")

    def on_one_path(self, args) -> None:
        # (path, dir_fd) or (path, mode, dir_fd) or (path,)
        args = tuple(args)
        dir_fd = args[-1] if len(args) > 1 and (args[-1] is None or isinstance(args[-1], int)) else None
        if isinstance(args[0], int):
            return
        self._change(args[:1], (dir_fd,))

    def on_two_paths(self, args) -> None:
        # (src, dst, src_dir_fd, dst_dir_fd)
        args = tuple(args) + (None, None, None, None)
        self._change(args[:2], args[2:4])

    def on_sqlite(self, args) -> None:
        if args and (hit := self._protected_path(args[0])):
            self.refuse(f"opened the database {hit}")

    def on_popen(self, args) -> None:
        executable, argv, cwd, env = (tuple(args) + (None, None, None, None))[:4]
        if isinstance(argv, (str, bytes, os.PathLike)):
            return  # a shell string; not parsed
        try:
            argv = [os.fsdecode(a) if isinstance(a, (bytes, os.PathLike)) else str(a) for a in (argv or ())]
        except TypeError:
            return
        if not argv:
            return
        name = os.path.basename(argv[0]).lower()
        if name.endswith(".exe"):
            name = name[:-4]
        if name == "git":
            self._check_git(argv[1:], cwd, env)
        elif self.state_prefixes and env is not None and _runs_openprogram(name, argv):
            home = env.get("HOME") if hasattr(env, "get") else None
            if home is None or os.path.abspath(os.fsdecode(home)) in self.host_home:
                self.refuse(f"started {' '.join(argv[:3])} with HOME "
                            f"{'unset' if home is None else 'set to the real home'}; "
                            "it would read and write the real ~/.openprogram")

    def _check_git(self, rest: list[str], cwd, env) -> None:
        if not (self.repo_top or self.git_dir):
            return
        environ = os.environ if env is None else env
        base = os.path.abspath(os.fsdecode(cwd)) if cwd is not None else os.getcwd()
        git_dir = environ.get("GIT_DIR") if hasattr(environ, "get") else None
        i = 0
        while i < len(rest):
            arg = rest[i]
            if arg == "-C" and i + 1 < len(rest):
                base = os.path.join(base, rest[i + 1])
                i += 2
            elif arg in ("-c", "--namespace", "--exec-path", "--config-env") and i + 1 < len(rest):
                i += 2
            elif arg.startswith("--git-dir="):
                git_dir = arg.split("=", 1)[1]
                i += 1
            elif arg == "--git-dir" and i + 1 < len(rest):
                git_dir = rest[i + 1]
                i += 2
            elif arg.startswith("-"):
                i += 1
            else:
                break
        sub, sub_args = (rest[i], rest[i + 1:]) if i < len(rest) else ("", [])
        if _read_only(sub, sub_args):
            return
        positional = [a for a in sub_args if not a.startswith("-")]
        if git_dir:
            targets = [os.path.join(base, os.fsdecode(git_dir))]
        elif sub == "init" and positional:
            # ``git init <dir>`` acts on <dir> (``--bare`` on an existing
            # ``.git`` turns that checkout bare).
            targets = [os.path.join(base, positional[-1])]
        elif sub == "clone":
            # Reads its source; writes only the new directory.
            targets = [os.path.join(base, positional[1])] if len(positional) > 1 else []
        else:
            targets = [base]
        for target in targets:
            target = os.path.abspath(target)
            if self._in_protected_repo(target):
                self.refuse(f"ran `git {' '.join(rest)}` against the repository at {target}")

    def _in_protected_repo(self, path: str) -> bool:
        for prefix in self.git_dir:
            if path == prefix or path.startswith(prefix + os.sep):
                return True
        # Inside the working tree: git discovers the checkout unless a closer
        # repository sits between the path and the top.
        for top in self.repo_top:
            if path == top or path.startswith(top + os.sep):
                probe = path
                while probe != top and len(probe) > len(top):
                    if os.path.exists(os.path.join(probe, ".git")):
                        return False
                    probe = os.path.dirname(probe)
                return True
        return False


def _runs_openprogram(name: str, argv: list[str]) -> bool:
    """A child that would load OpenProgram and resolve its state directory."""
    if name == "openprogram":
        return True
    if not name.startswith("python"):
        return False
    for i, arg in enumerate(argv[1:-1], start=1):
        if arg == "-m" and argv[i + 1].split(".")[0] in ("openprogram", "tests"):
            return True
    return any(arg.startswith("-c") and "openprogram" in arg for arg in argv[1:3]) or (
        len(argv) > 2 and argv[1] == "-c" and "openprogram" in argv[2])


def _read_only(sub: str, args: list[str]) -> bool:
    if sub in _READ_ONLY or sub == "":
        return True
    flags = {a.split("=", 1)[0] for a in args if a.startswith("-")}
    positional = [a for a in args if not a.startswith("-")]
    if sub == "config":
        if flags & _CONFIG_WRITES or positional[:1] in (["set"], ["unset"], ["rename-section"],
                                                        ["remove-section"], ["edit"]):
            return False
        return bool(flags & _CONFIG_READS) or positional[:1] in (["get"], ["list"]) or len(positional) <= 1
    if sub in ("branch", "tag"):
        return not positional or bool(flags & _LIST_FLAGS)
    if sub == "worktree":
        return positional[:1] == ["list"]
    if sub in ("stash", "notes", "remote", "reflog"):
        return not positional or positional[0] in ("list", "show", "-v", "get-url")
    if sub == "symbolic-ref":
        return len(positional) <= 1 and "-d" not in flags and "--delete" not in flags
    return False


_EVENTS = {
    "open": Guard.on_open,
    "os.rename": Guard.on_two_paths,
    "os.link": Guard.on_two_paths,
    "os.symlink": Guard.on_two_paths,
    "os.remove": Guard.on_one_path,
    "os.rmdir": Guard.on_one_path,
    "os.mkdir": Guard.on_one_path,
    "os.chmod": Guard.on_one_path,
    "os.truncate": Guard.on_one_path,
    "shutil.rmtree": Guard.on_one_path,
    "sqlite3.connect": Guard.on_sqlite,
    "subprocess.Popen": Guard.on_popen,
}


def _variants(path: Optional[Path]) -> tuple[str, ...]:
    if path is None:
        return ()
    raw = os.path.abspath(str(path))
    real = os.path.realpath(raw)
    return tuple(dict.fromkeys((raw, real)))


def repository_of(root: Path) -> tuple[list[Path], Optional[Path]]:
    """The checkout's tops (this worktree and the main checkout) and its
    common ``.git`` directory, or ``([], None)`` outside a repository."""
    env = {k: v for k, v in os.environ.items() if k not in GIT_LOCATION_VARIABLES}
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--path-format=absolute",
             "--show-toplevel", "--git-common-dir"],
            capture_output=True, text=True, timeout=10, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return [], None
    lines = out.stdout.split("\n")
    if out.returncode != 0 or len(lines) < 2 or not lines[0] or not lines[1]:
        return [], None
    top, common = Path(lines[0]), Path(lines[1])
    tops = [top]
    if common.name == ".git" and common.parent != top:
        tops.append(common.parent)
    return tops, common


def snapshot(git_common_dir: Optional[Path], test_home: Path) -> dict:
    """What a leak from a child process would change: the repository's
    config, and worktrees registered into it from this run's test home."""
    if git_common_dir is None:
        return {}
    config = git_common_dir / "config"
    try:
        digest = hashlib.sha256(config.read_bytes()).hexdigest()
    except OSError:
        digest = None
    home = os.path.realpath(str(test_home))
    ours = []
    worktrees = git_common_dir / "worktrees"
    try:
        for entry in sorted(worktrees.iterdir()):
            try:
                gitdir = (entry / "gitdir").read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if os.path.realpath(gitdir).startswith(home) or gitdir.startswith(str(test_home)):
                ours.append(gitdir)
    except OSError:
        pass
    return {"config": digest, "worktrees_from_this_run": ours}


def compare(before: dict, after: dict) -> list[str]:
    problems = []
    if before.get("config") != after.get("config"):
        problems.append("the repository's .git/config changed during the run")
    added = sorted(set(after.get("worktrees_from_this_run", ())) - set(before.get("worktrees_from_this_run", ())))
    if added:
        problems.append("worktrees were registered in the real repository: " + ", ".join(added))
    return problems


def install(guard: Guard) -> None:
    sys.addaudithook(guard)
