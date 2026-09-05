#!/usr/bin/env python3
"""Exercise the standalone Ink TUI through a real POSIX pseudo-terminal."""

from __future__ import annotations

import argparse
import errno
import fcntl
import os
import pty
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path


_READY_ENV = "_OPENPROGRAM_TUI_READY_FILE"
_READY_MARKER = b"OpenProgram Ink TUI first frame ready\n"
_ENTER_ALT_SCREEN = b"\x1b[?1049h"
_EXIT_ALT_SCREEN = b"\x1b[?1049l"


def _make_controlling_terminal() -> None:
    """Run in the child immediately before exec."""
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _drain(master_fd: int, output: bytearray) -> None:
    while True:
        try:
            chunk = os.read(master_fd, 65536)
        except BlockingIOError:
            return
        except OSError as exc:
            # Linux PTY masters report EIO after the final slave closes.
            if exc.errno == errno.EIO:
                return
            raise
        if not chunk:
            return
        output.extend(chunk)


def _detail(output: bytearray) -> str:
    return bytes(output[-4000:]).decode("utf-8", errors="replace")


def _wait_for_ready(
    process: subprocess.Popen[bytes],
    ready_path: Path,
    master_fd: int,
    output: bytearray,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _drain(master_fd, output)
        try:
            if ready_path.read_bytes() == _READY_MARKER:
                return
        except OSError:
            pass
        returncode = process.poll()
        if returncode is not None:
            raise RuntimeError(
                "Ink TUI exited before its first-frame handshake "
                f"(rc={returncode}):\n{_detail(output)}"
            )
        time.sleep(0.02)
    raise RuntimeError(
        f"timed out after {timeout:.1f}s waiting for Ink's first frame:\n"
        f"{_detail(output)}"
    )


def _wait_for_exit(
    process: subprocess.Popen[bytes],
    master_fd: int,
    output: bytearray,
    timeout: float,
) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _drain(master_fd, output)
        returncode = process.poll()
        if returncode is not None:
            _drain(master_fd, output)
            return returncode
        time.sleep(0.02)
    raise RuntimeError(f"Ink TUI did not exit after SIGTERM:\n{_detail(output)}")


def _run(node: str, entry: Path, timeout: float) -> None:
    if os.name != "posix":
        raise RuntimeError("the Ink PTY smoke test requires POSIX")
    if not entry.is_file():
        raise RuntimeError(f"standalone Ink bundle is missing: {entry}")

    master_fd, slave_fd = pty.openpty()
    backend_guard: socket.socket | None = None
    process: subprocess.Popen[bytes] | None = None
    output = bytearray()
    try:
        backend_guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        backend_guard.bind(("127.0.0.1", 0))
        backend_guard.listen(1)
        # Give the renderer a stable, realistic viewport.
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 120, 0, 0))
        original_mode = termios.tcgetattr(slave_fd)
        os.set_blocking(master_fd, False)

        with tempfile.TemporaryDirectory(prefix="openprogram-tui-pty-") as temp_dir:
            ready_path = Path(temp_dir) / "first-frame"
            env = os.environ.copy()
            env[_READY_ENV] = str(ready_path)
            env["OPENPROGRAM_STATE_DIR"] = str(Path(temp_dir) / "state")
            env["OPENPROGRAM_WS"] = (
                f"ws://127.0.0.1:{backend_guard.getsockname()[1]}/ws"
            )
            env["OPENPROGRAM_TUI_NO_ALT_SCREEN"] = "0"
            env["OPENPROGRAM_TUI_SCREEN_READER"] = "0"
            env["TERM"] = "xterm-256color"
            process = subprocess.Popen(
                [node, str(entry), "--demo"],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                close_fds=True,
                preexec_fn=_make_controlling_terminal,
            )

            _wait_for_ready(process, ready_path, master_fd, output, timeout)

            raw_mode = termios.tcgetattr(slave_fd)
            if raw_mode[3] & (termios.ICANON | termios.ECHO):
                raise RuntimeError(
                    "Ink reported its first frame without putting the PTY in raw mode"
                )

            process.send_signal(signal.SIGTERM)
            returncode = _wait_for_exit(process, master_fd, output, timeout)
            if returncode != 143:
                raise RuntimeError(
                    f"Ink TUI returned {returncode} after SIGTERM, expected 143:\n"
                    f"{_detail(output)}"
                )

            backend_guard.setblocking(False)
            try:
                connection, _address = backend_guard.accept()
            except BlockingIOError:
                pass
            else:
                connection.close()
                raise RuntimeError("backend-free --demo unexpectedly opened a WebSocket")

        restored_mode = termios.tcgetattr(slave_fd)
        if restored_mode != original_mode:
            raise RuntimeError("Ink TUI did not restore the PTY termios state")
        if _ENTER_ALT_SCREEN not in output:
            raise RuntimeError("Ink TUI never entered the alternate screen")
        if _EXIT_ALT_SCREEN not in output:
            raise RuntimeError("Ink TUI did not leave the alternate screen after SIGTERM")
        if b"OpenProgram TUI Kit" not in output:
            raise RuntimeError("Ink TUI did not render the backend-free demo screen")
    finally:
        if process is not None and process.poll() is None:
            try:
                # The child owns a session, so reap any future helper process
                # it might start as well as Node itself.
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                process.wait()
            except (ChildProcessError, OSError):
                pass
        for fd in (master_fd, slave_fd):
            try:
                os.close(fd)
            except OSError:
                pass
        if backend_guard is not None:
            backend_guard.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("node", help="Node.js executable")
    parser.add_argument("entry", type=Path, help="standalone Ink bundle")
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args()

    _run(args.node, args.entry.resolve(), args.timeout)
    print("OpenProgram Ink TUI PTY smoke passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as exc:
        print(f"OpenProgram Ink TUI PTY smoke failed: {exc}", file=sys.stderr)
        sys.exit(1)
