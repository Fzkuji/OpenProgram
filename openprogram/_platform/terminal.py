"""Platform terminal."""
from __future__ import annotations
from .. import _compat as state


def executable_cmd(argv: list[str]) -> list[str]:
    """Return an argv that ``subprocess(..., shell=False)`` can execute.

    Windows ``CreateProcess`` cannot directly launch ``.cmd``/``.bat`` files
    or scripts whose interpreter is declared by a POSIX shebang. Route batch
    files through ``cmd.exe`` and resolve a shebang interpreter without ever
    enabling shell interpolation. This supports credential helpers and other
    user-configured subprocesses while preserving their argv boundaries.

    A Git-for-Windows shell is discovered next to ``git.exe`` only when a
    helper explicitly asks for ``sh``/``bash``. It is not a runtime
    dependency; an absent interpreter remains a clear launch failure.
    """
    import shlex
    import shutil
    from pathlib import Path

    if not argv:
        return argv
    exe, rest = argv[0], list(argv[1:])
    resolved = shutil.which(exe) or exe
    if state._sys.platform != "win32":
        return [resolved, *rest]
    if resolved.lower().endswith((".cmd", ".bat")):
        comspec = state._os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/c", resolved, *rest]
    if resolved.lower().endswith(".ps1"):
        powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if powershell:
            return [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                resolved,
                *rest,
            ]

    try:
        with Path(resolved).open("rb") as script:
            first_line = script.readline(4096)
    except OSError:
        first_line = b""
    if first_line.startswith(b"#!"):
        declaration = first_line[2:].decode("utf-8", errors="replace").strip()
        words = shlex.split(declaration, posix=True)
        if words:
            interpreter, interpreter_args = words[0], words[1:]
            if interpreter.replace("\\", "/").rsplit("/", 1)[-1] == "env":
                if interpreter_args[:1] == ["-S"]:
                    interpreter_args = shlex.split(" ".join(interpreter_args[1:]))
                if interpreter_args:
                    interpreter, interpreter_args = (
                        interpreter_args[0], interpreter_args[1:]
                    )
            interpreter_name = interpreter.replace("\\", "/").rsplit("/", 1)[-1]
            found = shutil.which(interpreter) or shutil.which(interpreter_name)
            if found is None and interpreter_name in {"python", "python3"}:
                found = state._sys.executable
            if found is None and interpreter_name in {"sh", "bash"}:
                git = shutil.which("git.exe")
                if git:
                    candidate = Path(git).parent.parent / "bin" / f"{interpreter_name}.exe"
                    if candidate.is_file():
                        found = str(candidate)
            if found:
                return [found, *interpreter_args, resolved, *rest]
    return [resolved, *rest]


def node_tool_cmd(argv: list[str]) -> list[str]:
    """Compatibility alias for existing Node-ecosystem call sites."""
    return state.executable_cmd(argv)


def _windows_powershell(script: str, *, timeout: float = 5.0) -> str:
    """Run one read-only Windows CIM query and return UTF-8 output.

    Kept in the compatibility seam because modern Windows no longer ships
    WMIC and product modules must not grow their own platform subprocesses.
    """

    import shutil

    executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if executable is None:
        return ""
    prefix = (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();"
    )
    flags = getattr(state._subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = state._subprocess.run(
            [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                prefix + script,
            ],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=flags,
        )
    except (OSError, state._subprocess.TimeoutExpired):
        return ""
    return result.stdout if result.returncode == 0 else ""


def desktop_bundle_metadata(app_path):
    """Read the installed shell version and resources without launching it.

    Select the actual bundle layout, so release tooling can inspect macOS
    fixtures on any host. Windows PE metadata requires native PowerShell.
    """
    import plistlib
    from pathlib import Path

    app = Path(app_path)
    if app.name == "OpenProgram.exe" and app.is_file():
        app = app.parent
    plist = app / "Contents" / "Info.plist"
    if plist.is_file():
        with plist.open("rb") as stream:
            version = plistlib.load(stream).get("CFBundleShortVersionString")
        return app / "Contents" / "Resources", version

    executable = app / "OpenProgram.exe"
    resources = app / "resources"
    if not executable.is_file() or not (resources / "app.asar").is_file():
        raise ValueError("installed Desktop bundle layout is unavailable")
    # A single-quoted PowerShell literal treats all path characters as data;
    # doubling apostrophes also handles installation folders such as O'Brien.
    literal = str(executable.resolve()).replace("'", "''")
    version = state._windows_powershell(
        f"[Diagnostics.FileVersionInfo]::GetVersionInfo('{literal}').ProductVersion",
        timeout=15,
    ).strip()
    if not version:
        raise ValueError("installed Windows EXE product version is unavailable")
    # Electron's Windows resource can express the three-part release version
    # with a zero fourth component. Do not discard a nonzero revision.
    parts = version.split(".")
    if len(parts) == 4 and parts[-1] == "0" and all(part.isdecimal() for part in parts):
        version = ".".join(parts[:3])
    return resources, version


def _utf8_shell_environment(env=None):
    child_env = dict(state.os.environ if env is None else env)
    child_env.setdefault("PYTHONUTF8", "1")
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    return child_env


def powershell_invocation(executable: str, command: str, env=None):
    """Keep PowerShell source/Unicode independent of native argv quoting."""
    import base64

    source = (
        '$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); '
        + command
    )
    encoded = base64.b64encode(source.encode("utf-16-le")).decode("ascii")
    return [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], state._utf8_shell_environment(env)


def git_bash_invocation(bash: str, command: str, env=None):
    """Transport shell source past MSYS argv parsing without losing escapes.

    MSYS parses the native Windows command line differently from the CRT
    quoting used by subprocess. In particular, doubled backslashes inside a
    `-c` argument can collapse. Environment values are passed verbatim. Remove
    the transport variable before evaluating so descendant tools do not inherit
    a second copy of the command (which may contain credentials).
    """
    child_env = state._utf8_shell_environment(env)
    child_env["OPENPROGRAM_INTERNAL_SHELL_COMMAND"] = command
    trampoline = (
        '__openprogram_source=$OPENPROGRAM_INTERNAL_SHELL_COMMAND; '
        'unset OPENPROGRAM_INTERNAL_SHELL_COMMAND; '
        'export -n __openprogram_source; eval "$__openprogram_source"'
    )
    return [bash, "-c", trampoline], child_env


def interactive_pty_available() -> bool:
    """True when an :class:`InteractivePty` can be spawned on this host."""
    if state._sys.platform == "win32":
        try:
            import winpty  # noqa: F401  (pywinpty)
            return True
        except Exception:
            return False
    try:
        import pty  # noqa: F401
        return True
    except Exception:
        return False


class InteractivePty:
    """Spawn ``argv`` under a pseudo-terminal and drive it line by line.

    Unified API over POSIX ``pty`` and Windows ConPTY (``pywinpty``):

      * ``read_nonblocking(timeout)`` → text seen so far, or ``""`` if nothing
        arrived within ``timeout`` seconds.
      * ``write(text)`` → send ``text`` as if typed at the prompt.
      * ``wait(timeout)`` → the child's exit code (raises
        :class:`subprocess.TimeoutExpired` on timeout).
      * ``kill()`` / ``close()`` → terminate + release the pty.
      * ``alive`` → whether the child is still running.

    Raises :class:`RuntimeError` from the constructor when no pty backend
    exists — guard with :func:`interactive_pty_available` to fall back."""

    def __init__(self, argv, env=None, *, cols: int = 120, rows: int = 40) -> None:
        self._argv = list(argv)
        self._closed = False
        if state._sys.platform == "win32":
            self._init_windows(env, cols, rows)
        else:
            self._init_posix(env, cols, rows)

    # -- POSIX (stdlib pty) -----------------------------------------------
    def _init_posix(self, env, cols, rows) -> None:
        try:
            import pty
        except ImportError as e:  # pragma: no cover - exotic POSIX build
            raise RuntimeError("pty unavailable on this host") from e
        self._backend = "posix"
        self._master, slave = pty.openpty()
        try:
            try:
                import fcntl as _f
                import struct
                import termios
                _f.ioctl(self._master, termios.TIOCSWINSZ,
                         struct.pack("HHHH", rows, cols, 0, 0))
            except Exception:
                pass
            self._proc = state._subprocess.Popen(
                self._argv, stdin=slave, stdout=slave, stderr=slave,
                close_fds=True, env=env, start_new_session=True,
            )
        except BaseException:
            # Popen failed (ENOENT / EMFILE / permissions): close both fds so
            # they don't leak — __init__ is aborting and the half-built object
            # won't be close()d by the caller.
            for _fd in (self._master, slave):
                try:
                    state._os.close(_fd)
                except OSError:
                    pass
            raise
        state._os.close(slave)

    def _posix_read(self, timeout: float) -> str:
        import select
        try:
            r, _w, _e = select.select([self._master], [], [], timeout)
        except (OSError, ValueError):
            return ""
        if self._master not in r:
            return ""
        try:
            data = state._os.read(self._master, 4096)
        except OSError:
            return ""
        return data.decode("utf-8", "replace")

    # -- Windows (ConPTY via pywinpty) ------------------------------------
    def _init_windows(self, env, cols, rows) -> None:
        try:
            import winpty
        except Exception as e:  # ImportError or a binding load failure
            raise RuntimeError(
                "pywinpty (winpty) is required to drive an interactive login "
                "on Windows; install it or use the token paste flow"
            ) from e
        import queue as _queue
        import threading
        self._backend = "win"
        self._queue: "_queue.Queue" = _queue.Queue()
        # pywinpty's PtyProcess.spawn accepts an argv list and an env dict.
        self._proc = winpty.PtyProcess.spawn(
            self._argv, env=env, dimensions=(rows, cols),
        )

        def _pump() -> None:
            # ConPTY reads block; a daemon thread funnels chunks to the queue
            # so read_nonblocking() can honour a timeout. read() raises EOF at
            # child exit.
            try:
                while True:
                    chunk = self._proc.read(4096)
                    if chunk:
                        self._queue.put(chunk)
            except Exception:
                pass
            finally:
                self._queue.put(None)  # EOF sentinel

        self._reader = threading.Thread(target=_pump, daemon=True)
        self._reader.start()

    def _win_read(self, timeout: float) -> str:
        import queue as _queue
        try:
            chunk = self._queue.get(timeout=timeout)
        except _queue.Empty:
            return ""
        if chunk is None:  # EOF sentinel — child exited
            return ""
        buf = [chunk]
        try:  # drain anything already queued without blocking
            while True:
                more = self._queue.get_nowait()
                if more is None:
                    break
                buf.append(more)
        except _queue.Empty:
            pass
        return "".join(buf)

    # -- unified API -------------------------------------------------------
    def read_nonblocking(self, timeout: float = 1.0) -> str:
        if self._backend == "win":
            return self._win_read(timeout)
        return self._posix_read(timeout)

    def write(self, text: str) -> None:
        try:
            if self._backend == "win":
                # A ConPTY completes a line on CR (the Enter keypress), not a
                # bare LF — so translate "\n" to "\r\n". Callers can keep
                # writing "<line>\n" and it works on both platforms. (Normalise
                # any existing CRLF first to avoid "\r\r\n".)
                text = text.replace("\r\n", "\n").replace("\n", "\r\n")
                self._proc.write(text)
            else:
                state._os.write(self._master, text.encode("utf-8"))
        except Exception:
            pass

    @property
    def alive(self) -> bool:
        try:
            if self._backend == "win":
                return bool(self._proc.isalive())
            return self._proc.poll() is None
        except Exception:
            return False

    def wait(self, timeout: float | None = None) -> int:
        if self._backend == "win":
            import time as _t
            end = None if timeout is None else _t.time() + timeout
            while self._proc.isalive():
                if end is not None and _t.time() >= end:
                    raise state._subprocess.TimeoutExpired(self._argv, timeout)
                _t.sleep(0.1)
            return int(self._proc.exitstatus or 0)
        return self._proc.wait(timeout=timeout)

    def kill(self) -> None:
        # Kill the whole tree, not just the leader. On Windows the spawned
        # child is `cmd.exe /c meridian.cmd …` (node_tool_cmd wraps the .cmd
        # shim) which in turn spawns node; on POSIX the backend itself spawns
        # `claude`. Terminating only the leader would orphan the real OAuth
        # process. kill_process_tree handles both (taskkill /T on Windows,
        # killpg on POSIX — the child leads its own session via start_new_session).
        pid = getattr(getattr(self, "_proc", None), "pid", None)
        if pid:
            try:
                state.kill_process_tree(pid)
            except Exception:
                pass
        try:
            if self._backend == "win":
                self._proc.terminate(force=True)
            else:
                self._proc.kill()
        except Exception:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._backend == "win":
                try:
                    # PtyProcess.close() closes its TCP bridge, but pywinpty's
                    # native reader can still be blocked in PTY.read().  Wake
                    # that read first so both the bridge and our queue pump can
                    # terminate deterministically.
                    self._proc.pty.cancel_io()
                except Exception:
                    pass
                try:
                    self._proc.close(force=True)
                except Exception:
                    pass
                # The adapter pump and pywinpty's internal read helper must
                # observe the closed ConPTY before close() returns. Leaving
                # daemon readers alive leaks production threads into the next
                # command/test and can retain console handles unnecessarily.
                reader = getattr(self, "_reader", None)
                if reader is not None:
                    try:
                        reader.join(timeout=2.0)
                    except Exception:
                        pass
                native_reader = getattr(self._proc, "_thread", None)
                if native_reader is not None:
                    try:
                        native_reader.join(timeout=2.0)
                    except Exception:
                        pass
            else:
                try:
                    state._os.close(self._master)
                except OSError:
                    pass
        except Exception:
            pass


def prompt_toolkit_usable() -> bool:
    """Return True if ``prompt_toolkit`` (and therefore ``questionary``,
    ``inquirer``, …) can render a full-screen interactive prompt in the
    current terminal.

    On POSIX with a tty, or Windows with a native ``cmd.exe`` /
    Windows Terminal / PowerShell console, returns True.

    On Git Bash (MinTTY), Cygwin without a winpty wrapper, redirected
    stdio, IDEs that pipe through pseudo-ttys, or any other case where
    prompt_toolkit's ``create_output()`` fails — returns False, so
    callers can fall back to plain ``input()``-driven menus.

    The probe is destructive-free (it creates and immediately drops
    the output backend) but does a small amount of work, so the
    result is cached for the lifetime of the process.
    """
    global _PROMPT_TOOLKIT_USABLE_CACHE
    if state._PROMPT_TOOLKIT_USABLE_CACHE is not None:
        return state._PROMPT_TOOLKIT_USABLE_CACHE
    try:
        from prompt_toolkit.output.defaults import create_output
    except ImportError:
        state._PROMPT_TOOLKIT_USABLE_CACHE = False
        return False
    try:
        # create_output() raises NoConsoleScreenBufferError on Windows
        # MinTTY and friends; any other terminal-detection issue also
        # surfaces here.
        create_output()
        state._PROMPT_TOOLKIT_USABLE_CACHE = True
    except Exception:  # noqa: BLE001 — any failure is "don't use it"
        state._PROMPT_TOOLKIT_USABLE_CACHE = False
    return state._PROMPT_TOOLKIT_USABLE_CACHE
