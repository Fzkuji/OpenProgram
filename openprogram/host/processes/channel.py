"""The doorbell between a command's supervisor and whoever waits on it.

``processes.db`` stays the record of everything: a command's output, its
status, the stop and stdin rows written for it. The doorbell only says
"look now", both ways:

* A writer (``control``) inserts a command row, then connects: the line it
  sends wakes the supervisor, which reads its pending commands at once.
* A waiter (``collect``, ``control`` waiting for an acknowledgement)
  connects before it reads the record; the supervisor sends it one byte
  after every write it makes (output, status, acknowledgement). The
  connection closing means the supervisor ended: the kernel closes it on
  any exit, a crash included.

Each supervisor listens on a loopback TCP port it chose itself, recorded
with a random token in its record (the database is private to the user);
a connection that does not open with the token is dropped. Loopback TCP is
the one local channel Python has on macOS, Linux and Windows alike: a Unix
socket has no ``AF_UNIX`` on Windows and a 104-byte path limit on macOS, a
FIFO is POSIX only, and a file event arrives only after ``watchfiles``'
debounce. A lost or refused ring only delays a waiter to its resync.
"""
from __future__ import annotations

import hmac
import secrets
import select
import selectors
import socket
import threading

#: What a supervisor writes on its inherited stdout once its record runs
#: (and the doorbell's address is in it), for the starter waiting on it.
READY_LINE = b"ready\n"
#: A waiter reads the record again at least this often, in case a ring was
#: lost; nothing loses one in normal operation.
RESYNC_SECONDS = 10.0

_HOST = "127.0.0.1"
#: The longest opening line accepted: the token and a mode word.
_MAX_LINE = 128
#: The opening line's mode word for a writer that only pokes and leaves.
_POKE_ONLY = b"poke"


class Doorbell:
    """The supervisor's end: a listening socket, its subscribers and the ring.

    The supervisor's loop owns the selector and calls :meth:`handle` for
    every event on a socket registered here; :meth:`ring` may be called from
    any thread (the output reader rings after each chunk).
    """

    def __init__(self) -> None:
        self.token = secrets.token_hex(16)
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            # Windows: no other socket may bind this port while it is ours.
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        self._server.bind((_HOST, 0))
        self._server.listen(16)
        self._server.setblocking(False)
        self.port = self._server.getsockname()[1]
        self._lock = threading.Lock()
        self._opening: dict[socket.socket, bytearray] = {}
        self._pokers: set[socket.socket] = set()
        self._subscribers: set[socket.socket] = set()
        self._selector = None

    def register(self, selector) -> None:
        self._selector = selector
        selector.register(self._server, selectors.EVENT_READ, self)

    def owns(self, fileobj) -> bool:
        return (fileobj is self._server or fileobj in self._opening
                or fileobj in self._pokers or fileobj in self._subscribers)

    def handle(self, fileobj) -> bool:
        """Serve one readable socket; whether it asked the supervisor to look
        at its commands."""
        if fileobj is self._server:
            try:
                conn, _ = self._server.accept()
            except (BlockingIOError, InterruptedError):
                return False
            conn.setblocking(False)
            self._opening[conn] = bytearray()
            self._selector.register(conn, selectors.EVENT_READ, self)
            return False
        try:
            data = fileobj.recv(4096)
        except (BlockingIOError, InterruptedError):
            return False
        except OSError:
            data = b""
        if not data:
            self._drop(fileobj)
            return False
        if fileobj in self._pokers:
            return False  # nothing follows a poke but its end
        opening = self._opening.get(fileobj)
        if opening is None:
            return True  # a subscriber's later byte: look again
        opening.extend(data)
        if b"\n" not in opening:
            if len(opening) > _MAX_LINE:
                self._drop(fileobj)
            return False
        line = bytes(opening).split(b"\n", 1)[0]
        token, _, mode = line.partition(b" ")
        if not hmac.compare_digest(token, self.token.encode("ascii")):
            self._drop(fileobj)
            return False
        del self._opening[fileobj]
        if mode == _POKE_ONLY:
            # It closes after its line; its end is read as EOF.
            self._pokers.add(fileobj)
        else:
            with self._lock:
                self._subscribers.add(fileobj)
        return True

    def ring(self) -> None:
        """Tell every subscriber to look now. A subscriber with a byte still
        unread is already told; one whose connection broke is dropped by
        the loop when it reads its end."""
        # Sent under the lock, so no subscriber is closed mid-send; each
        # send is non-blocking.
        with self._lock:
            for conn in self._subscribers:
                try:
                    conn.send(b".")
                except OSError:
                    pass  # a byte is still unread (BlockingIOError), or it broke

    def _drop(self, conn) -> None:
        self._opening.pop(conn, None)
        self._pokers.discard(conn)
        if self._selector is not None:
            try:
                self._selector.unregister(conn)
            except (KeyError, ValueError):
                pass
        with self._lock:
            self._subscribers.discard(conn)
            conn.close()

    def close(self) -> None:
        for conn in [*self._opening, *self._pokers, *self._subscribers]:
            self._drop(conn)
        if self._selector is not None:
            try:
                self._selector.unregister(self._server)
            except (KeyError, ValueError):
                pass
        self._server.close()


def poke(record: dict | None) -> None:
    """Wake the supervisor of ``record`` to read its commands, and leave."""
    port, token = _address(record)
    if port is None:
        return
    try:
        with socket.create_connection((_HOST, port), timeout=2) as conn:
            conn.sendall(token.encode("ascii") + b" " + _POKE_ONLY + b"\n")
    except OSError:
        pass  # the supervisor ended; its record says so, or the waiter's resync will


def _address(record: dict | None) -> tuple[int | None, str | None]:
    if not record:
        return None, None
    port, token = record.get("notify_port"), record.get("notify_token")
    if not isinstance(port, int) or not isinstance(token, str):
        return None, None
    return port, token


class Watch:
    """A waiter's end: rung by the supervisor of one record, or woken by
    :meth:`wake` from another thread (a cancellation).

    Connecting also wakes the supervisor to read its commands, so a writer
    that waits for its acknowledgement connects after inserting its row.
    ``ended`` turns true once the supervisor is gone: its connection
    closed, or a recorded port refused the connection.
    """

    def __init__(self, record: dict | None) -> None:
        self._wake_read, self._wake_write = socket.socketpair()
        self._wake_read.setblocking(False)
        self._wake_write.setblocking(False)
        self._conn: socket.socket | None = None
        self.ended = False
        port, token = _address(record)
        if port is None:
            return
        try:
            conn = socket.create_connection((_HOST, port), timeout=2)
            conn.sendall(token.encode("ascii") + b"\n")
            conn.setblocking(False)
        except OSError:
            self.ended = True
            return
        self._conn = conn

    @property
    def connected(self) -> bool:
        return self._conn is not None

    def wake(self) -> None:
        """End the current :meth:`wait` now; safe from any thread."""
        try:
            self._wake_write.send(b"\0")
        except OSError:
            pass  # already woken, or closed

    def wait(self, timeout: float) -> bool:
        """Block until the supervisor rings, it ends, :meth:`wake` is called
        or ``timeout`` seconds pass; whether something may have changed."""
        readers = [self._wake_read] + ([self._conn] if self._conn is not None else [])
        ready = select.select(readers, [], [], max(0.0, timeout))[0]
        if not ready:
            return False
        if self._wake_read in ready:
            _drain(self._wake_read)
        if self._conn is not None and self._conn in ready:
            if not _drain(self._conn):
                self._conn.close()
                self._conn = None
                self.ended = True
        return True

    def close(self) -> None:
        for sock in (self._conn, self._wake_read, self._wake_write):
            if sock is not None:
                sock.close()
        self._conn = None

    def __enter__(self) -> "Watch":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def _drain(sock: socket.socket) -> bool:
    """Read what is waiting; False once the other end closed."""
    try:
        while True:
            data = sock.recv(4096)
            if not data:
                return False
            if len(data) < 4096:
                return True
    except (BlockingIOError, InterruptedError):
        return True
    except OSError:
        return False


__all__ = ["Doorbell", "READY_LINE", "RESYNC_SECONDS", "Watch", "poke"]
