"""Apply bounded model edits and test a new isolated candidate without installing it."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import selectors
import shlex
import signal
import stat
import subprocess
import sys

from .iteration import _PROTECTED_PATHS, _DEPENDENCY_FILES
from .types import IterationMode, _validate_changed_path
from openprogram.programs.tools.system.self_update import (
    _git, _recorded_path, _validate_candidate_snapshot, _validate_registered_worktree,
)


def allowed_path(request, path):
    _validate_changed_path(path)
    parts = PurePosixPath(path).parts
    if (any(part in {".git", ".gitmodules", ".gitattributes", ".worktreeinclude"} or part.startswith(".env") for part in parts)
            or any(ord(c) < 32 for c in path)
            or any(fnmatch.fnmatchcase(path, pattern) for pattern in _PROTECTED_PATHS)
            or any(fnmatch.fnmatchcase(parts[-1], pattern) for pattern in _DEPENDENCY_FILES)):
        raise ValueError("source repair cannot modify protected files")
    allowed = (any(fnmatch.fnmatchcase(path, pattern) for pattern in request.iteration_policy.allowed_paths)
               if request.iteration_policy.mode is IterationMode.BOUNDED_AUTO else path in request.changed_paths)
    if not allowed:
        raise ValueError("source repair path is outside the original scope")


def _file(root, relative):
    path = root / relative
    if path.resolve() != path or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("source repair path contains a symlink")
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1_048_576:
            raise ValueError("source repair requires a bounded regular file")
        return path, path.read_bytes().decode("utf-8")
    return path, None


def _edits(request, root, edits):
    if not isinstance(edits, list) or not 1 <= len(edits) <= 32:
        raise ValueError("source repair requires 1 to 32 edits")
    files = {}
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"path", "old_text", "new_text"}:
            raise ValueError("invalid source edit")
        allowed_path(request, edit["path"])
        path, original = _file(root, edit["path"])
        if path in files:
            original = files[path][1]
        before, after = edit["old_text"], edit["new_text"]
        if (before is not None and (not isinstance(before, str) or not before)
                or after is not None and not isinstance(after, str) or before == after):
            raise ValueError("invalid source edit text")
        if before is None:
            if original is not None:
                raise ValueError("new source file already exists")
            changed = after
        elif original is None or original.count(before) != 1:
            raise ValueError("source edit does not uniquely match original text")
        elif after is None:
            if before != original:
                raise ValueError("deletion must match the entire source file")
            changed = None
        else:
            changed = original.replace(before, after, 1)
        files[path] = (files[path][0] if path in files else original, changed)
    return files


# A separate stdlib-only watchdog bounds the test even if the worker exits.
# The candidate subprocess cannot signal it across the native sandbox boundary.
_WATCHDOG = '''import os, signal, subprocess, sys, time
parent, seconds = int(sys.argv[1]), float(sys.argv[2])
def stop(*_):
    raise SystemExit(125)
signal.signal(signal.SIGTERM, stop)
child = None
try:
    child = subprocess.Popen(sys.argv[3:], start_new_session=True)
    end = time.monotonic() + seconds
    while child.poll() is None:
        if os.getppid() != parent:
            raise SystemExit(125)
        if time.monotonic() >= end:
            raise SystemExit(124)
        time.sleep(.05)
    raise SystemExit(child.returncode)
finally:
    if child is not None:
        try: os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        child.wait()
'''


def _test(command, candidate, scratch, remaining, check, *, verification=False, output_limit=1_048_576):
    from .supervisor import _sandbox_executable, _sandbox_profile
    from openprogram.store.session.git_session import atomic_write_text
    sandbox = _sandbox_executable()
    home, temporary = scratch / "home", scratch / "tmp"
    for directory in (home, temporary):
        directory.mkdir(mode=0o700)
    profile = _sandbox_profile(candidate, scratch, home, temporary)
    if verification:
        if (not isinstance(command, list) or not command
                or any(not isinstance(arg, str) or "\0" in arg for arg in command)
                or not Path(command[0]).is_absolute()
                or type(output_limit) is not int or not 1 <= output_limit <= 262144):
            raise ValueError("native verification requires fixed argv and output bound")
        # Source and user data remain read-only/inaccessible even if test code
        # tries to override HOME or write through a relative path.
        profile += f"(deny file-read* (subpath {json.dumps(str(Path.home().resolve()))}))\n"
        for readable in (candidate, scratch, Path(command[0]).parent.parent):
            profile += f"(allow file-read* (subpath {json.dumps(str(readable))}))\n"
        profile += f"(deny file-write* (subpath {json.dumps(str(candidate))}))\n"
    profile += f"(allow file-read* (subpath {json.dumps(str(candidate))}))\n"
    profile += f"(deny file-write* (literal {json.dumps(str(candidate / '.git'))}))\n"
    runtime = Path(sys.base_prefix).resolve()
    if runtime.is_relative_to(Path("/Applications/OpenProgram.app/Contents/Resources/runtime")):
        # Packaged tests may read the running interpreter, never write the App.
        profile += f"(allow file-read* (subpath {json.dumps(str(runtime))}))\n"
    profile_path = scratch / "test.sb"
    atomic_write_text(profile_path, profile)
    argv = list(command) if verification else shlex.split(command)
    if not argv:
        raise ValueError("empty required test")
    if not verification and argv[0] in {"python", "python3", Path(sys.executable).name}:
        argv[0] = sys.executable  # No silent PATH fallback to a different Python.
    env = dict(PATH=f"{Path(sys.executable).parent}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
               HOME=str(home), TMPDIR=str(temporary), CI="1", PYTHONDONTWRITEBYTECODE="1",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
    # -I ignores PYTHONDONTWRITEBYTECODE; the unsandboxed watchdog needs -B.
    proc = subprocess.Popen([sys.executable, "-I", "-B", "-c", _WATCHDOG, str(os.getpid()), str(remaining),
                             str(sandbox), "-f", str(profile_path), *argv], cwd=candidate,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    output = bytearray()
    total = 0
    try:
        os.set_blocking(proc.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                check()
                if selector.select(.1):
                    chunk = os.read(proc.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    output.extend(chunk)
                    if not verification:
                        del output[:-200_000]
                    if total > output_limit:
                        raise ValueError("native output exceeded its approved limit" if verification
                                         else "required test output exceeded 1 MiB")
        check()
        return proc.wait(timeout=2), output.decode("utf-8", errors="replace")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        proc.stdout.close()
        if not verification:
            atomic_write_text(scratch / "test.log", output.decode("utf-8", errors="replace"))


def materialize(store, record, frozen, repair_request, output, check):
    """Controller-only mutation. An existing intent is never replayed."""
    from .source_repair import _path
    from openprogram.store.session.git_session import atomic_write_text
    from openprogram.worktree.types import Worktree
    from openprogram.worktree.store import save_worktree
    from openprogram.worktree.manager import _broadcast_worktree_status
    source = _recorded_path(record.request.repo, "source repo")
    original = _recorded_path(frozen["candidate_path"], "original candidate")
    _validate_registered_worktree(source, original, record.request.candidate_sha, frozen["branch_name"])
    _validate_candidate_snapshot(original, record.request.candidate_sha)
    _edits(record.request, original, output["edits"])
    suffix = hashlib.sha256(repair_request["job_id"].encode()).hexdigest()[:12]
    root = store.root.parent / "worktrees"
    if root.is_symlink() or root.resolve() != root:
        raise ValueError("repair worktree root is not canonical")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    candidate = root / f"wt_{suffix}-source-repair"
    branch = f"op/self-update/{record.request.update_id}/{record.state.attempt}"
    worktree = Worktree(id=f"wt_{suffix}", source_repo=str(source), worktree_path=str(candidate),
                        branch_name=branch, base_ref=record.request.candidate_sha,
                        parent_session=record.request.session_id, parent_job=repair_request["job_id"])
    check()
    intent = _path(store, record, "intent")
    if intent.exists() or intent.is_symlink() or candidate.exists() or candidate.is_symlink():
        raise ValueError("interrupted source repair requires inspection; mutations are not replayed")
    store._write_json(intent, dict(schema=1, worktree=worktree.to_dict(), output=output))
    # This path deliberately uses pinned Git and no .worktreeinclude copying.
    _git(source, "worktree", "add", "-b", branch, str(candidate), record.request.candidate_sha)
    save_worktree(worktree)
    _broadcast_worktree_status(worktree)
    files = _edits(record.request, candidate, output["edits"])
    for path, (before, after) in files.items():
        check()
        if _file(candidate, path.relative_to(candidate).as_posix())[1] != before:
            raise ValueError("candidate changed before applying source edit")
        if after is None:
            path.unlink()
        else:
            mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, after)
            path.chmod(mode)
    check()
    paths = [path.relative_to(candidate).as_posix() for path in files]
    _git(candidate, "add", "--", *paths)
    _git(candidate, "-c", "user.name=OpenProgram Self Update", "-c", "user.email=self-update@localhost",
         "-c", "commit.gpgsign=false", "commit", "-m", "Apply bounded self-update source repair")
    sha = _git(candidate, "rev-parse", "HEAD")
    changed = tuple(filter(None, _git(candidate, "diff", "--name-only", "-z", f"{record.request.base_sha}..{sha}").split("\0")))
    for path in changed:
        allowed_path(record.request, path)
    _validate_candidate_snapshot(candidate, sha)
    manifest = dict(worktree_id=worktree.id, worktree_path=str(candidate), branch_name=branch,
                    candidate_sha=sha, base_sha=record.request.base_sha, changed_paths=list(changed), tests=[])
    store._write_json(_path(store, record, "candidate"), manifest)
    git_file = (candidate / ".git").read_bytes()
    for index, command in enumerate(record.request.iteration_policy.required_tests):
        remaining = check()
        scratch = store.root / record.request.update_id / f"repair-test-{record.state.attempt}-{index}"
        scratch.mkdir(mode=0o700)
        code, log = _test(command, candidate, scratch, remaining, check)
        if (candidate / ".git").is_symlink() or (candidate / ".git").read_bytes() != git_file:
            raise ValueError("candidate Git binding changed during required tests")
        _validate_registered_worktree(source, candidate, sha, branch)
        _validate_candidate_snapshot(candidate, sha)
        manifest["tests"].append(dict(command=command, candidate_sha=sha, exit_code=code,
                                      log_sha256=hashlib.sha256(log.encode()).hexdigest(), log_path=str(scratch / "test.log")))
        store._write_json(_path(store, record, "candidate"), manifest)
        if code != 0:
            raise ValueError(f"required test failed with exit {code}")
    check()
    return manifest
