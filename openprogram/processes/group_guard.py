"""Keep a POSIX process-group identity alive until its ordinary children exit.

This small detached group leader has no runtime/config dependencies. A parent
supervisor owns its Popen handle and may terminate this exact group. Commands
that deliberately create another session are outside this managed group.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time


def _other_members():
    # ps itself runs in our group; exclude it after collecting its snapshot.
    inspector = subprocess.Popen(["ps", "-axo", "pid=,pgid=,stat="],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    output, _ = inspector.communicate(timeout=3)
    if inspector.returncode:
        raise RuntimeError("process group inspection failed")
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 3 and int(fields[1]) == os.getpid():
            if int(fields[0]) not in {os.getpid(), inspector.pid} and not fields[2].startswith("Z"):
                return True
    return False


def main():
    # A caught signal is reset by exec in the backend child; unlike SIG_IGN,
    # this does not accidentally make the launched command ignore SIGTERM.
    signal.signal(signal.SIGTERM, lambda *_: None)
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
        proc = subprocess.Popen(**launch, stdin=subprocess.PIPE, bufsize=0)

        def forward_input():
            try:
                chunk = pending_input
                while True:
                    if not chunk:
                        chunk = os.read(sys.stdin.fileno(), 8192)
                        if not chunk:
                            break
                    offset = 0
                    while offset < len(chunk):
                        offset += os.write(proc.stdin.fileno(), chunk[offset:])
                    chunk = b""
            except (BrokenPipeError, OSError):
                pass
            finally:
                proc.stdin.close()

        threading.Thread(target=forward_input, daemon=True).start()
        code = proc.wait()
        while _other_members():
            time.sleep(0.1)
        return code if code >= 0 else 128 - code
    except BaseException as exc:
        print(f"[process group failed: {type(exc).__name__}]", file=sys.stderr, flush=True)
        # Losing the group owner must not silently detach live descendants.
        os.killpg(os.getpid(), signal.SIGKILL)
        return 1


if __name__ == "__main__":
    sys.exit(main())
