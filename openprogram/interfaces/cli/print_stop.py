"""How a ``--print`` command stops, whichever transport runs its turn.

One set of rules for the in-process run (``print_turn._stop_run``) and the
relay of a turn the Worker runs (``worker_turn._Relay.stop``):

* The first Ctrl-C, SIGTERM or (on Windows) SIGBREAK is raised as a
  ``KeyboardInterrupt`` (:func:`interrupt_on_terminate`); which one it was is
  kept (:func:`stop_signal`) and reported as the run's ``stopped_by``.
* From then on the command is stopping: a further signal is noted, not
  raised (:func:`hold_signals`), so it never lands in the middle of the
  cancel or of a file write.
* The turn is given :data:`CANCEL_GRACE_S` to stop, counted from when the
  stop began; once a further signal is noted, only
  :data:`SECOND_SIGNAL_GRACE_S` counted from when the cancel went out
  (:class:`StopClock`). A turn that takes the cancel stops within a few
  tenths of a second, killing the command its tool runs; a second signal
  right behind the first (a double Ctrl-C, a supervisor signalling the shell
  and the process group) must not abandon it.
* A cancel a Worker refuses for a stale version is sent again at most
  :data:`STALE_RETRIES` times (a Worker that announces
  ``cancel_ignores_version`` never refuses one).
* A stopped command exits with :func:`stopped_exit_code`, 128 plus the
  signal's number as shells report it: 130 for SIGINT, 143 for SIGTERM, 129
  for SIGHUP; SIGBREAK, Windows' nearest to SIGTERM, also 143. A further
  signal keeps the first one's code. ``stopped_by`` names the signal.
* On Windows, closing the console window (and a log-off or shutdown) is a
  stop too (:func:`_console_closed`), reported as ``SIGHUP``. Windows ends
  the process about five seconds after it, so the turn then gets only
  :data:`CONSOLE_CLOSE_GRACE_S`. Node maps the same event to SIGHUP, which
  is how Claude Code's ``--print`` stops on it.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from contextlib import contextmanager
from typing import Callable

#: How long a stopped turn is given to end before the command leaves.
CANCEL_GRACE_S = 30.0

#: How long the turn is still given, counted from the cancel, once a further
#: Ctrl-C or SIGTERM asks the command to leave.
SECOND_SIGNAL_GRACE_S = 2.0

#: How long a stop started by closing the Windows console has: Windows ends
#: the process about five seconds after the close.
CONSOLE_CLOSE_GRACE_S = 3.5

#: How often a wait checks for a further signal, which does not wake it.
STOP_POLL_S = 0.05

#: How many times a cancel refused for a stale version is sent again with the
#: version the refusal names (an older Worker, or a remote server).
STALE_RETRIES = 3

#: The exit status of a command whose run was stopped by Ctrl-C (or with
#: no signal named).
STOPPED_EXIT_CODE = 130

#: The exit status for each signal that stops a run (:func:`stopped_exit_code`).
STOP_EXIT_CODES = {"SIGINT": 130, "SIGTERM": 143, "SIGBREAK": 143, "SIGHUP": 129}

# The signals that ask the command to stop. SIGBREAK exists only on Windows,
# where it is the one a supervisor can send to another process: SIGTERM is
# never delivered there (``TerminateProcess`` ends the process with nothing
# run), and Ctrl-C does not reach a child started in its own process group,
# which is how a harness starts one it means to stop
# (``CREATE_NEW_PROCESS_GROUP`` and ``CTRL_BREAK_EVENT``).
# SIGHUP (not on Windows) is a closed terminal or a supervisor's hang-up.
_STOP_SIGNALS = ("SIGINT", "SIGTERM", "SIGBREAK", "SIGHUP")


class StopClock:
    """The wait of one stop: how long is left before the command leaves.

    ``noted`` is the event a further signal sets (:func:`hold_signals`).
    Until it is set the stop has :data:`CANCEL_GRACE_S` from its start; once
    it is set, :data:`SECOND_SIGNAL_GRACE_S` from when the cancel went out
    (:meth:`sent`), or from the start while none has. The grace periods are
    read when asked, so a test that changes them changes a running stop.
    """

    def __init__(self, noted: threading.Event | None = None) -> None:
        self.noted = noted if noted is not None else threading.Event()
        self.started = time.monotonic()
        self.sent_at: float | None = None

    def sent(self) -> None:
        """The cancel went out (the first time counts)."""
        if self.sent_at is None:
            self.sent_at = time.monotonic()

    def remaining(self) -> float:
        if self.noted.is_set():
            limit = (self.sent_at or self.started) + SECOND_SIGNAL_GRACE_S
        else:
            limit = self.started + CANCEL_GRACE_S
        if _console_closed_at is not None:
            limit = min(limit, _console_closed_at + CONSOLE_CLOSE_GRACE_S)
        return limit - time.monotonic()

    def waited(self) -> float:
        return time.monotonic() - self.started

    def wait_for(self, finished: threading.Event) -> bool:
        """Wait for ``finished`` while time is left; whether it was set.
        Short slices: a noted signal does not wake the wait."""
        while not finished.is_set():
            left = self.remaining()
            if left <= 0:
                break
            finished.wait(min(left, STOP_POLL_S))
        return finished.is_set()


def stopped_exit_code(signal_name: str | None = None) -> int:
    """The exit status of a command the signal ``signal_name`` stopped
    (:data:`STOP_EXIT_CODES`); 130 for none or another."""
    return STOP_EXIT_CODES.get(signal_name or "", STOPPED_EXIT_CODE)


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

_noted = threading.Event()
_held = False
# The name of the signal that first asked the command to stop.
_stop_signal: str | None = None
# When the Windows console was closed under the command (``_console_closed``).
_console_closed_at: float | None = None
# The console handler, kept alive for as long as Windows may call it.
_console_handler = None

# Windows console control events that end the process when handled late.
_CTRL_CLOSE_EVENT, _CTRL_LOGOFF_EVENT, _CTRL_SHUTDOWN_EVENT = 2, 5, 6


def _console_closed(event: int, *, interrupt_main: Callable[[], None] | None = None,
                    hold_s: float | None = None) -> bool:
    """The console handler: a close, log-off or shutdown stops the run.

    Ctrl-C and Ctrl-Break are left to the signal handlers (False). For a
    close the stop starts as a first signal would, in the main thread; the
    handler then holds the process (Windows ends it when the handler
    returns) while the stop runs, at most :data:`CONSOLE_CLOSE_GRACE_S`;
    :func:`leave` ends it sooner.
    """
    global _stop_signal, _console_closed_at
    if event not in (_CTRL_CLOSE_EVENT, _CTRL_LOGOFF_EVENT, _CTRL_SHUTDOWN_EVENT):
        return False
    if _console_closed_at is None:
        _console_closed_at = time.monotonic()
    if _stop_signal is None:
        _stop_signal = "SIGHUP"
    if _held:
        _noted.set()
    else:
        if interrupt_main is None:
            import _thread
            interrupt_main = _thread.interrupt_main
        interrupt_main()
    time.sleep(CONSOLE_CLOSE_GRACE_S + 0.5 if hold_s is None else hold_s)
    return True


def _watch_console_close() -> None:
    """Have Windows call :func:`_console_closed` (``SetConsoleCtrlHandler``)."""
    global _console_handler
    if sys.platform != "win32" or _console_handler is not None:
        return
    try:
        import ctypes
        from ctypes import wintypes

        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        _console_handler = handler_type(lambda event: bool(_console_closed(int(event))))
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler, True)
    except Exception:  # noqa: BLE001 - without it a closed console kills the run
        _console_handler = None


def _signal_name(signum: int) -> str:
    import signal

    try:
        return signal.Signals(signum).name
    except ValueError:
        return str(signum)


def stop_signal() -> str | None:
    """Which signal stopped the run (``SIGINT``, ``SIGTERM``, ``SIGBREAK``),
    or ``None`` when none did."""
    return _stop_signal


@contextmanager
def interrupt_on_terminate():
    """Within the block, SIGTERM stops the run the way Ctrl-C does.

    A supervisor that stops ``--print`` (a benchmark harness at its time
    limit, a container shutdown) sends SIGTERM; the run is then cancelled
    and its usage summary still written, instead of the process dying with
    the summary unwritten.

    Only the first Ctrl-C or SIGTERM is raised. The same handler holds the
    signals (:func:`hold_signals`) before it raises, so one that follows
    within milliseconds is noted for the stop to act on, not raised into it;
    and they stay held after the block, while the command stops.
    """
    import signal

    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def _interrupt(signum, _frame):
        global _stop_signal
        if _stop_signal is None:
            _stop_signal = _signal_name(signum)
        hold_signals()
        raise KeyboardInterrupt

    _watch_console_close()

    previous = {}
    for name in _STOP_SIGNALS:
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            previous[number] = signal.signal(number, _interrupt)
        except (OSError, ValueError):
            pass
    try:
        yield
    finally:
        if not _held:
            for number, handler in previous.items():
                signal.signal(number, handler)


@contextmanager
def signals_noted():
    """Within the block, Ctrl-C and SIGTERM do not interrupt the code.

    They are noted on the event the block gets instead. The command is
    already on its way out when this is used (delivering the cancel,
    writing the run's files), so a further signal asks for nothing that is
    not happening; raised into the middle of a write it would leave the
    write half done.
    """
    import signal

    noted = threading.Event()
    if threading.current_thread() is not threading.main_thread():
        yield noted
        return
    previous = {}
    for name in _STOP_SIGNALS:
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            previous[number] = signal.signal(number, lambda _signum, _frame: noted.set())
        except (OSError, ValueError):
            pass
    try:
        yield noted
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _note(_signum, _frame) -> None:
    _noted.set()


def hold_signals() -> threading.Event:
    """From now until the process ends, Ctrl-C and SIGTERM are noted.

    For a command that is already stopping its run: a further signal asks
    for nothing that is not happening, and raised into the middle of the
    cancel or of a file write it would leave that half done. The event is
    set by any signal noted since the first call; :func:`leave_stopped`
    acts on it once the run's files are written. Off the main thread
    nothing is installed.
    """
    import signal

    global _held
    if _held or threading.current_thread() is not threading.main_thread():
        return _noted
    _noted.clear()
    for name in _STOP_SIGNALS:
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            signal.signal(number, _note)
        except (OSError, ValueError):
            pass
    _held = True
    return _noted


def stopping() -> bool:
    """Whether the command is stopping (:func:`hold_signals` was called)."""
    return _held


def leave_stopped(code: int = STOPPED_EXIT_CODE, *, left_running: bool = False,
                  before_leaving: Callable[[], None] | None = None) -> None:
    """End a stopped run's command, its files written.

    A turn still busy (``left_running``) has ``before_leaving`` called (it
    stops the commands the turn was waiting on) and the process leaves at
    once (:func:`leave`); so does a command that had a further signal while
    it stopped. Otherwise the process exits normally with ``code``. A signal
    while the interpreter waits for its threads leaves at once with the same
    code. After that wait, the interpreter runs its exit callbacks and then
    takes its modules down, the last part with no Python signal handler
    running and, past a point, the default handlers back: a signal there
    would kill the process with its own status, so from the first exit
    callback on Ctrl-C and SIGTERM are ignored (:func:`_ignore_signals`),
    and the exit status stays ``code``.
    """
    import atexit
    import signal

    if left_running:
        if before_leaving is not None:
            before_leaving()
        leave(code)
    if threading.current_thread() is threading.main_thread():
        for name in _STOP_SIGNALS:
            number = getattr(signal, name, None)
            if number is None:
                continue
            try:
                signal.signal(number, lambda _signum, _frame: leave(code))
            except (OSError, ValueError):
                pass
        # Registered last, so it runs first of the exit callbacks, which
        # come after the interpreter has joined its threads.
        atexit.register(_ignore_signals)
    if _noted.is_set():
        leave(code)


def _ignore_signals() -> None:
    import signal

    for name in _STOP_SIGNALS:
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            signal.signal(number, signal.SIG_IGN)
        except (OSError, ValueError):
            pass


def leave(code: int) -> None:
    """End the process now, without the interpreter's shutdown.

    For a run whose turn was still busy when the command stopped waiting
    for it, or a command asked again to leave. An ordinary exit would wait
    for that turn after all, since it runs on an executor thread the
    interpreter joins on its way out, so the grace period would bound
    nothing and a second signal would not end the command; and a thread
    that writes to stderr while the interpreter shuts down aborts the
    process with a core dump. Everything the command owes is written by now
    (the reply, the usage and transcript files); stdout and stderr are
    flushed here.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001 - leaving regardless
            pass
    os._exit(code)


__all__ = [
    "CANCEL_GRACE_S", "CONSOLE_CLOSE_GRACE_S", "SECOND_SIGNAL_GRACE_S", "STALE_RETRIES", "STOPPED_EXIT_CODE",
    "STOP_EXIT_CODES",
    "STOP_POLL_S", "StopClock", "hold_signals", "interrupt_on_terminate", "leave",
    "leave_stopped", "signals_noted", "stop_signal", "stopped_exit_code", "stopping",
]
