"""run_checks, and the one command run that apply_edits' verify uses, after
magpie's ``internal/fasttools/checks.go``, ``group_other.go`` and
``group_windows.go`` (MIT License, Copyright (c) yetone)."""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any

from .server import ToolError, fields, from_bytes, root, to_bytes, utf8_start

MAX_CHECKS = 8
# what a failed command shows of its output: its end, where the failure is
FAIL_LINES = 40
FAIL_BYTES = 2400


@dataclass
class Check:
    cmd: str = ""
    name: str = ""
    timeout: int = 0


def _check(raw: Any, i: int) -> Check:
    # a command as a plain string too
    if isinstance(raw, str):
        return Check(cmd=raw)
    f = fields(raw, {"cmd": "string", "name": "string", "timeout": "int"}, f" (command {i + 1})")
    return Check(f["cmd"], f["name"], f["timeout"])


@dataclass
class CheckResult:
    name: str = ""
    ok: bool = False
    code: int = 0
    timeout: bool = False
    took: float = 0.0  # seconds
    out: str = ""
    err: str = ""

    def text(self, tail: bool) -> str:
        took = go_duration(self.took)
        if self.err:
            return f"FAIL {self.name}: {self.err}\n"
        if self.ok:
            return f"ok   {self.name} ({took})\n"
        why = f"exit {self.code}"
        if self.timeout:
            why = "timed out"
        s = f"FAIL {self.name}: {why} ({took})\n"
        if tail and self.out.strip() != "":
            s += indent(tail_of(self.out)) + "\n"
        return s


def go_duration(seconds: float) -> str:
    """seconds rounded to a tenth, as Go's Duration.Round(100ms).String()
    writes it: 0s, 300ms, 1.2s, 2m3.5s."""
    ns = int(seconds * 1e9)
    m = 100_000_000
    q, r = divmod(ns, m)
    ns = q * m if r + r < m else (q + 1) * m
    if ns == 0:
        return "0s"
    if ns < 1_000_000_000:
        return f"{ns // 1_000_000}ms"
    h, rest = divmod(ns, 3600 * 10**9)
    mi, rest = divmod(rest, 60 * 10**9)
    s, frac = divmod(rest, 10**9)
    sec = str(s)
    if frac:
        sec += "." + f"{frac:09d}".rstrip("0")
    out = ""
    if h:
        out += f"{h}h"
    if h or mi:
        out += f"{mi}m"
    return out + sec + "s"


def run_checks(raw: Any) -> str:
    a = fields(raw, {"cwd": "string", "commands": "array"})
    commands = [_check(c, i) for i, c in enumerate(a["commands"] or [])]
    if not commands:
        raise ToolError("no commands")
    if len(commands) > MAX_CHECKS:
        raise ToolError(f"{len(commands)} commands: at most {MAX_CHECKS} in one call")
    dir = root(a["cwd"])
    res: list[CheckResult] = [CheckResult()] * len(commands)
    threads = []
    for i, c in enumerate(commands):
        if c.name == "":
            c.name = c.cmd
            b = to_bytes(c.name)
            if len(b) > 60:
                c.name = from_bytes(b[:57]) + "…"
        t = c.timeout
        if t <= 0:
            t = 300

        def one(i: int = i, c: Check = c, t: int = t) -> None:
            res[i] = run_one(dir, c, float(min(t, 1800)))

        th = threading.Thread(target=one, daemon=True)
        th.start()
        threads.append(th)
    for th in threads:
        th.join()
    failed = sum(1 for r in res if not r.ok)
    b = [f"{len(res) - failed} of {len(res)} passed\n"]
    for r in res:
        b.append(r.text(True))
    return "".join(b).rstrip("\n")


def run_one(dir: str, c: Check, timeout: float) -> CheckResult:
    """Run c's command line in a shell in dir."""
    r = CheckResult(name=c.name)
    if c.cmd.strip() == "":
        r.err = "no command"
        return r
    name, args = shell_args(c.cmd)
    kw: dict[str, Any] = {}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        # own_group: the command runs in a process group of its own, which
        # its timeout kills whole: a check's shell and every program it
        # started (a test binary, a watcher) end with it, not only the shell
        kw["process_group"] = 0
    began = time.monotonic()
    try:
        p = subprocess.Popen([name, *args], cwd=dir, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
    except OSError as e:
        r.took = time.monotonic() - began
        r.err = str(e)
        return r
    timed_out = False
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _end_tree(p)
        out, _ = p.communicate()
    r.took = time.monotonic() - began
    r.out = from_bytes(out or b"")
    if p.returncode == 0 and not timed_out:
        r.ok = True
    elif timed_out:
        r.timeout = True
    else:
        # Go's ExitCode: -1 for a command a signal ended
        r.code = p.returncode if p.returncode >= 0 else -1
    return r


def _end_tree(p: subprocess.Popen) -> None:
    """End p and everything it started: its process group on unix; its
    tree on Windows, as magpie's proc does there."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        p.kill()
    except OSError:
        pass


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)")


def tail_of(out: str) -> str:
    """The last lines of out, its colours taken out."""
    out = _ANSI.sub("", out)
    out = out.replace("\r\n", "\n")
    # a progress line rewritten with \r: its last state
    lines = out.rstrip("\n").split("\n")
    for i, l in enumerate(lines):
        j = l.rfind("\r")
        if j >= 0:
            lines[i] = l[j + 1:]
    left = 0
    if len(lines) > FAIL_LINES:
        left = len(lines) - FAIL_LINES
        lines = lines[left:]
    s = "\n".join(lines)
    b = to_bytes(s)
    if len(b) > FAIL_BYTES:
        cut = len(b) - FAIL_BYTES
        while cut < len(b) and not utf8_start(b[cut]):
            cut += 1
        i = b.find(b"\n", cut)
        if 0 <= i - cut < 200:
            cut = i + 1
        s = from_bytes(b[cut:])
        left = -1
    if left > 0:
        s = f"… {left} lines before\n" + s
    elif left < 0:
        s = "…\n" + s
    return s


def indent(s: str) -> str:
    return "  " + s.replace("\n", "\n  ")


_shell_lock = threading.Lock()
_shell: list[str] = []


def look_shell() -> str:
    """bash when there is one: commands are written for it more often than
    for a plain sh."""
    with _shell_lock:
        if not _shell:
            _shell.append(shutil.which("bash") or "")
        return _shell[0]


def shell_args(line: str) -> tuple[str, list[str]]:
    """The program and arguments that run a command line in a shell:
    PowerShell on Windows, as Antigravity runs its own commands there."""
    if os.name == "nt":
        return "powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", line]
    sh = look_shell()
    if sh:
        return sh, ["-c", line]
    return "/bin/sh", ["-c", line]
