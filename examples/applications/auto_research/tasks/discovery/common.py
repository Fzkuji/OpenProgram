"""What the discovery tasks share: JSON schema helpers, a run folder, running a command
with a time limit (its whole process group is killed on timeout), and running a
generated analysis script under a guard that keeps it inside its own folder. Plain code.
"""

import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

OUTPUT_CHARS = 20_000       # characters of a command's output kept


def _str(limit: int, least: int = 1) -> dict:
    return {"type": "string", "minLength": least, "maxLength": limit}


def _arr(items: dict, least: int, most: int) -> dict:
    return {"type": "array", "items": items, "minItems": least, "maxItems": most}


def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


def _prob() -> dict:
    return {"type": "number", "minimum": 0, "maximum": 1}


def out_folder(paper: Path, out_dir: str, default: str) -> Path:
    base = Path(out_dir).expanduser() if out_dir else paper / "drafts" / default
    out = base / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True, exist_ok=True)
    return out


def shown(path: Path, paper: Path) -> str:
    """A path relative to the repository when it is inside it, else absolute."""
    try:
        return Path(path).resolve().relative_to(paper).as_posix()
    except ValueError:
        return str(path)


def _text(value) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def run(command, cwd: Path, timeout: float, env: dict | None = None, shell: bool = False) -> dict:
    """Run a command; {output (stdout then stderr, the tail kept), code (None on timeout),
    seconds}. On timeout the whole process group is killed, so children do not linger."""
    start = time.monotonic()
    proc = subprocess.Popen(command, cwd=cwd, env=env, shell=shell, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        out, err = proc.communicate()
        code = None
    output = _text(out) + ("\n" + _text(err) if err else "")
    if len(output) > OUTPUT_CHARS:
        output = "[… cut]\n" + output[-OUTPUT_CHARS:]
    return {"output": output, "stdout": _text(out)[-OUTPUT_CHARS:], "code": code,
            "seconds": round(time.monotonic() - start, 2)}


# The guard an analysis script runs under: an audit hook installed before the script.
# Reads are allowed in the script's folder and where Python and its libraries live;
# writes only in the script's folder; no sockets, no new processes.
GUARD = r'''
import os, runpy, sys
HERE = os.path.realpath(os.getcwd())
SYSTEM = ("/usr", "/System", "/Library/Frameworks", "/dev", "/proc", "/sys", "/etc", "/private/etc",
          "/private/var/db/timezone", "/opt/homebrew/Cellar", "/opt/homebrew/opt", "/opt/homebrew/lib")
ROOTS = {os.path.realpath(p) for p in (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix)}
ROOTS |= {os.path.realpath(p) for p in sys.path if p and os.path.isdir(p) and os.path.realpath(p) != HERE}
ROOTS |= {os.path.realpath(p) for p in SYSTEM if os.path.exists(p)}
ROOTS = tuple(ROOTS)
DENY = ("socket.", "subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork",
        "os.forkpty", "os.kill", "os.putenv", "pty.", "webbrowser.")
WRITES = ("os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.link",
          "os.symlink", "os.truncate", "os.utime", "shutil.")
busy = []

def inside(path, roots):
    p = os.path.realpath(os.path.join(HERE, os.fsdecode(path)))
    return any(p == r or p.startswith(r.rstrip(os.sep) + os.sep) for r in roots)

def hook(event, args):
    if busy:
        return
    busy.append(1)
    try:
        if event.startswith(DENY):
            raise PermissionError(f"not allowed in an analysis script: {event}")
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            mode = args[1] or "r"
            flags = args[2] or 0
            writing = any(c in str(mode) for c in "wax+") or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)
            if not inside(args[0], (HERE,) if writing else (HERE,) + ROOTS):
                raise PermissionError(f"analysis scripts read only their data files: {os.fsdecode(args[0])}")
        elif event.startswith(WRITES):
            for a in args:
                if isinstance(a, (str, bytes, os.PathLike)) and not inside(a, (HERE,)):
                    raise PermissionError(f"analysis scripts change only their own folder: {event}")
        elif event in ("os.listdir", "os.scandir") and args and isinstance(args[0], (str, bytes, os.PathLike)):
            if not inside(args[0], (HERE,) + ROOTS):
                raise PermissionError(f"analysis scripts list only their own folder: {os.fsdecode(args[0])}")
    finally:
        busy.pop()

script = sys.argv[1]
sys.argv = [script]
sys.addaudithook(hook)
runpy.run_path(script, run_name="__main__")
'''


def run_guarded(script: Path, cwd: Path, timeout: float) -> dict:
    """Run a Python script in cwd under GUARD with a minimal environment, isolated mode."""
    guard = cwd / "_guard.py"
    guard.write_text(GUARD, encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(cwd), "TMPDIR": str(cwd), "LANG": "C.UTF-8",
           "PYTHONIOENCODING": "utf-8", "MPLBACKEND": "Agg", "OMP_NUM_THREADS": "1"}
    return run([sys.executable, "-I", "-B", str(guard), str(script)], cwd, timeout, env=env)


__all__ = ["GUARD", "OUTPUT_CHARS", "out_folder", "run", "run_guarded", "shown"]
