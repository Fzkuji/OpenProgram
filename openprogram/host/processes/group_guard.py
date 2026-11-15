"""Keep a POSIX process-group identity alive until its ordinary children exit.

This small detached group leader has no runtime/config dependencies. A parent
supervisor owns its Popen handle and may terminate this exact group. Commands
that deliberately create another session are outside this managed group.

Without a terminal the command's stdin is closed, as in Codex. With one, the
leader makes a new pseudo-terminal its controlling terminal with this group in
the foreground, so input arrives raw and Ctrl-C (``\x03``) interrupts the
whole group.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time

_log = logging.getLogger(__name__)


def _other_members(proc_root="/proc", session=False):
    """Whether a live process other than this leader is still in its group.

    With ``session`` the scope is this leader's session instead: it also
    counts processes that moved to a group of their own (a job started under
    job control, a child that called setpgid) and still share the terminal.
    The leader started the session, so its pid is both ids.
    """
    if os.path.isdir(os.path.join(proc_root, "self")):
        # Linux: the kernel's table, so an image without ps (slim Debian,
        # Alpine without procps) still runs commands.
        return _other_members_from_proc(proc_root, session)
    # ps itself runs in our group; exclude it after collecting its snapshot.
    inspector = subprocess.Popen(["ps", "-axo", "pid=,pgid=,stat="],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    output, _ = inspector.communicate(timeout=3)
    if inspector.returncode:
        raise RuntimeError("process group inspection failed")
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[2].startswith("Z"):
            continue
        pid = int(fields[0])
        if pid in {os.getpid(), inspector.pid}:
            continue
        if session:
            # macOS ps shows no session id; ask the kernel per process.
            try:
                if os.getsid(pid) == os.getpid():
                    return True
            except OSError:
                pass  # exited since the snapshot
        elif int(fields[1]) == os.getpid():
            return True
    return False


def _other_members_from_proc(proc_root, session=False):
    me = os.getpid()
    for name in os.listdir(proc_root):
        if not name.isdigit() or int(name) == me:
            continue
        try:
            with open(os.path.join(proc_root, name, "stat"), "rb") as stream:
                # comm may hold spaces or ')'; after its last ')' come
                # state, ppid, pgrp, session, ...
                fields = stream.read().rsplit(b")", 1)[-1].split()
        except OSError:
            continue  # exited between the listing and the read
        column = 3 if session else 2
        if len(fields) > column and fields[column] == str(me).encode() and fields[0] not in (b"Z", b"X"):
            return True
    return False


def _write_all(target, chunk):
    offset = 0
    while offset < len(chunk):
        offset += os.write(target, chunk[offset:])


def _pump(source, target):
    try:
        while chunk := os.read(source, 8192):
            _write_all(target, chunk)
    except OSError:
        pass  # EIO once every terminal descriptor of the command is closed


def _pump_terminal(master, target, finished):
    """Copy the terminal's output until it ends or the group is gone.

    Linux reports EIO once the command's last terminal descriptor closes.
    macOS does not while this leader holds the terminal as its controlling
    terminal, and that reference is what keeps the last bytes readable: a
    macOS terminal whose slave side is fully closed discards unread output.
    So once ``finished`` is readable no process can write any more, and the
    pump drains what is queued without waiting and stops. The leader makes
    it readable only after every other process of its session is gone, or
    after the session's stragglers have had the same two seconds to write
    that the join below always allowed.
    """
    import select

    try:
        while True:
            ready, _, _ = select.select([master, finished], [], [])
            if master in ready:
                chunk = os.read(master, 8192)
                if not chunk:
                    return
                _write_all(target, chunk)
            elif finished in ready:
                while select.select([master], [], [], 0)[0]:
                    chunk = os.read(master, 8192)
                    if not chunk:
                        return
                    _write_all(target, chunk)
                return
    except OSError:
        pass  # EIO once every terminal descriptor of the command is closed


def _spawn_in_terminal(launch, pending_input):
    import fcntl
    import pty
    import struct
    import termios

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 120, 0, 0))
    # This leader started a session of its own and has no controlling
    # terminal yet; its group becomes the terminal's foreground group.
    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
    os.tcsetpgrp(slave, os.getpgrp())
    env = dict(launch.pop("env", None) or os.environ)
    env.setdefault("TERM", "xterm-256color")
    proc = subprocess.Popen(**launch, env=env, stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    finished, group_gone = os.pipe()
    output = threading.Thread(target=_pump_terminal,
                              args=(master, sys.stdout.fileno(), finished), daemon=True)
    output.start()

    def forward_input():
        if pending_input:
            os.write(master, pending_input)
        _pump(sys.stdin.fileno(), master)

    threading.Thread(target=forward_input, daemon=True).start()
    return proc, output, group_gone


def main():
    # A caught signal is reset by exec in the backend child; unlike SIG_IGN,
    # this does not accidentally make the launched command ignore SIGTERM.
    signal.signal(signal.SIGTERM, lambda *_: None)
    signal.signal(signal.SIGINT, lambda *_: None)
    try:
        # Use raw pipe I/O throughout: a daemon holding a buffered stdin
        # lock would abort the interpreter when the managed group finishes.
        frame = bytearray()
        while b"\n" not in frame:
            chunk = os.read(sys.stdin.fileno(), 8192)
            if not chunk:
                raise EOFError("missing process launch frame")
            frame.extend(chunk)
        header, _, pending_input = bytes(frame).partition(b"\n")
        launch = json.loads(header)
        output = None
        if launch.pop("_tty", False):
            proc, output, group_gone = _spawn_in_terminal(launch, pending_input)
        else:
            proc = subprocess.Popen(**launch, stdin=subprocess.DEVNULL)
        code = proc.wait()
        while _other_members():
            time.sleep(0.1)
        if output is not None:
            # A job in another group of this session (``set -m``, setpgid)
            # still holds the terminal and may write; the pump keeps reading
            # while one is alive, up to the bound the output always had.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and _other_members(session=True):
                time.sleep(0.1)
            # Nothing is left to write: the pump drains the queue and stops.
            os.close(group_gone)
            output.join(timeout=2)
        return code if code >= 0 else 128 - code
    except BaseException as exc:
        _log.error("process group failed: %s", type(exc).__name__)
        # Losing the group owner must not silently detach live descendants.
        os.killpg(os.getpid(), signal.SIGKILL)
        return 1


if __name__ == "__main__":
    sys.exit(main())
