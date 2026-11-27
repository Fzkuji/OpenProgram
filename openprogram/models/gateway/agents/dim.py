"""Dim alpha (arcships/dim, the ``dima`` command of npm's dimcode-alpha).

After yetone/magpie ``internal/agent/dim.go``, ``dim_daemon.go`` and
``dim_daemon_other.go`` (MIT License, Copyright (c) yetone). Dim ships a
built-in provider named Magpie (its default-catalog.json: Responses at
``http://127.0.0.1:3425/v1``, an API key, no models until listed). Dim
defines that provider, so OpenProgram uses it as Dim has it, id ``magpie``:
connecting fills it in through the API of Dim's daemon, the way Dim's own
Providers settings do: OpenProgram's gateway address and Dim's own agent
key, on; its models listed from the gateway's ``/models`` (the limits Dim
needs given where the list has none); the model picked made Dim's model.
So ``dima``, and anything else on Dim's daemon, runs on the gateway however
it is started. No other provider of Dim's is touched, and a key, or an
address of neither Dim's nor the gateway's, the user gave the provider is
left as it is: only the model is picked on it. What was there before is
kept, and turning it off (or picking Dim's own) puts it back where it is
still as OpenProgram left it. Nothing reads or uses Dim's own account.

Dim keeps its providers in a database its daemon owns (a version per
record that every write is checked against) and changes them only through
the daemon's JSON-RPC API, the one Dim's own CLI and desktop app use:
newline-delimited JSON-RPC 2.0 after a hello of protocol 4, on
``<home>/rust/daemon.sock``. Without a daemon, ``dima models magpie`` starts
one, as any dima command does, and lists nothing.

On Windows the daemon listens on a named pipe, reached through Dim's own
bridge (magpie ``dim_daemon_windows.go``): the server binary Dim keeps in its
native home, run as ``dimcode-server app-server --connect-pipe <pipe>``, which
says ``{"status":"ready"}`` on its stderr once the pipe is authenticated.

When Dim's daemon can't be reached or doesn't take the model, dima is started
on the gateway the way Dim documents for any OpenAI-compatible endpoint
instead: ``DIM_BASE_URL`` with ``DIM_API_FORMAT`` (``openai-compatible``,
Dim's generic provider), ``DIM_API_KEY``, ``DIM_MODEL``, and
``DIM_MAX_CONTEXT_TOKENS`` with ``DIM_MAX_OUTPUT_TOKENS``, read for that one
run only. A function named ``dima`` goes in each shell the user has
(``shellfn.py``, magpie ``update.ShellFunction``), which runs ``openprogram
dim run``, the real dima with them; once the daemon is reached (a sync while it runs), the
provider is filled in and the function goes.
"""
from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .base import Agent, Field, Live, Wiring, find_tool

#: Dim's own name for the provider it ships for a local gateway.
PROVIDER = "magpie"
#: The base URL of that provider as Dim ships it.
DEFAULT = "http://127.0.0.1:3425/v1"
#: The limits Dim is given for a model the catalog knows none of (Dim
#: refuses to start one without both).
CONTEXT = 128000
OUTPUT = 32000
LINE_LIMIT = 64 << 20
#: How long one connection to the daemon may take, a pipe's included.
WAIT = 60.0

_mu = threading.Lock()


class DimAway(Exception):
    """Dim's daemon isn't running, and none was started."""


class DimError(Exception):
    """An error the daemon answered, with Dim's own code (provider_conflict …)."""

    def __init__(self, message: str, code: str = ""):
        super().__init__("Dim: " + message)
        self.code = code


def home() -> Path:
    raw = (os.environ.get("DIMCODE_HOME") or "").strip()
    return Path(raw) if raw else Path.home() / ".dimcode-alpha"


def socket_path(folder: Path) -> Path:
    return folder / "rust" / "daemon.sock"


def pipe_name(native: Path) -> str:
    """The pipe of the daemon of Dim's native home, as dim-cli's connect_inner
    names it: ``dim-rust-`` and the first 32 hex digits of
    ``sha256("dimcode:" + the home as Rust's canonicalize gives it)``."""
    p = os.path.realpath(os.path.abspath(native))
    p = "\\\\?\\UNC\\" + p[2:] if p.startswith("\\\\") else "\\\\?\\" + p
    return "\\\\.\\pipe\\dim-rust-" + hashlib.sha256(("dimcode:" + p).encode("utf-8")).hexdigest()[:32]


class Bridge:
    """Dim's pipe bridge process, its stdout and stdin the connection, with the
    calls :class:`Conn` makes of a socket. A pipe has no timeout of its own:
    :func:`connect_daemon` closes the bridge after :data:`WAIT` (magpie
    ``dimConnect``'s ``time.AfterFunc(time.Minute, rw.Close)``), which ends
    a read or write a daemon that hung would block, ``_mu`` with it."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self.expired = False

    def expire(self) -> None:
        """The connection's time is up: the bridge goes."""
        self.expired = True
        self.close()

    def settimeout(self, _timeout: float) -> None:
        pass  # the whole connection's timer stands in (connect_daemon)

    def makefile(self, _mode: str):
        return self.proc.stdout

    def sendall(self, data: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        finally:
            self.proc.kill()
            self.proc.wait()


def _dial_pipe(folder: Path) -> Bridge:
    """Start Dim's own bridge to its daemon's pipe (dim-client's Client::open)."""
    native = folder / "rust"
    server = native / "dimcode-server.exe"
    if not server.is_file():
        raise DimAway("Dim's daemon isn't running: its server isn't installed")
    try:
        proc = subprocess.Popen([str(server), "app-server", "--connect-pipe", pipe_name(native)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        raise DimAway(f"Dim's daemon isn't running: {exc}") from exc
    bridge = Bridge(proc)
    line: list[bytes] = []

    def read() -> None:
        assert proc.stderr is not None
        line.append(proc.stderr.readline())
        for _ in proc.stderr:
            pass
    threading.Thread(target=read, daemon=True).start()
    end = time.monotonic() + 5
    while not line and time.monotonic() < end:
        time.sleep(0.05)
    import json
    try:
        ready = json.loads(line[0]) if line else {}
    except ValueError:
        ready = {}
    if not isinstance(ready, dict) or ready.get("status") != "ready":
        bridge.close()
        raise DimAway("Dim's pipe bridge isn't ready: " + (line[0].decode("utf-8", "replace").strip() if line else ""))
    return bridge


def _dial(folder: Path) -> socket.socket:
    if sys.platform == "win32":
        return _dial_pipe(folder)  # type: ignore[return-value]
    if not hasattr(socket, "AF_UNIX"):
        raise DimAway("Dim's daemon isn't reachable on this system")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect(str(socket_path(folder)))
    except OSError as exc:
        sock.close()
        raise DimAway(f"Dim's daemon isn't running: {exc}") from exc
    return sock


def _open_daemon(folder: Path, start: bool) -> socket.socket:
    """A connection to the daemon of Dim's home ``folder``, starting it when
    ``start`` and it isn't running."""
    try:
        return _dial(folder)
    except DimAway:
        if not start:
            raise
    binary = find_tool("dima")
    if not binary:
        raise DimAway("Dim's daemon isn't running: dima isn't installed")
    try:
        # any dima command starts the daemon; this one lists the provider's
        # models and changes nothing
        subprocess.run([binary, "models", PROVIDER], env={**os.environ, "DIMCODE_HOME": str(folder)},
                       capture_output=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DimAway(f"Dim's daemon isn't running: dima models: {exc}") from exc
    end = time.monotonic() + 10
    while True:
        try:
            return _dial(folder)
        except DimAway:
            if time.monotonic() > end:
                raise
            time.sleep(0.2)


#: Opens a connection to the daemon; tests stand in a fake daemon.
open_daemon: Callable[[Path, bool], socket.socket] = _open_daemon


class Conn:
    """One greeted connection to Dim's daemon."""

    def __init__(self, sock: socket.socket, timer: Optional[threading.Timer] = None):
        self.sock = sock
        self.timer = timer
        self.sock.settimeout(WAIT)
        self.reader = sock.makefile("rb")
        self.id = 0
        self.call("hello", {"protocolVersion": 4, "clientKind": "other", "clientName": "openprogram"})

    def close(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
        try:
            self.reader.close()
        finally:
            self.sock.close()

    def call(self, method: str, params: Any) -> Any:
        """Send ``method`` and read its answer; notifications and other answers
        on the way are skipped."""
        import json
        self.id += 1
        line = json.dumps({"jsonrpc": "2.0", "id": self.id, "method": method, "params": params}) + "\n"
        try:
            self.sock.sendall(line.encode("utf-8"))
            while True:
                raw = self.reader.readline(LINE_LIMIT)
                if not raw:
                    if getattr(self.sock, "expired", False):
                        raise DimError(f"{method}: the daemon didn't answer in {WAIT:.0f}s")
                    raise DimError(f"{method}: the daemon closed the connection")
                try:
                    m = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(m, dict) or m.get("id") != self.id:
                    continue
                if m.get("error"):
                    err = m["error"] if isinstance(m["error"], dict) else {}
                    data = err.get("data") if isinstance(err.get("data"), dict) else {}
                    raise DimError(str(err.get("message") or "error"), str(data.get("appError") or ""))
                return m.get("result")
        except (OSError, ValueError) as exc:
            # ValueError: the bridge closed under a read or write (its time up)
            if getattr(self.sock, "expired", False):
                raise DimError(f"{method}: the daemon didn't answer in {WAIT:.0f}s") from exc
            raise DimError(f"{method}: {exc}") from exc

    def provider(self, pid: str) -> dict[str, Any]:
        r = self.call("provider.management", {"providerId": pid}) or {}
        for p in r.get("providers") or []:
            if p.get("providerId") == pid:
                return p
        raise DimError(f"Dim has no {pid} provider")

    def update(self, p: dict[str, Any], patch: dict[str, Any]) -> None:
        self.call("provider.update", {"providerId": p["providerId"], "expectedVersion": p.get("version"), "patch": patch})

    def selection(self) -> Optional[dict[str, Any]]:
        return (self.call("provider.selection", {}) or {}).get("selection")

    def pick(self, provider: str, model: str, was: Optional[dict[str, Any]]) -> None:
        params: dict[str, Any] = {"providerId": provider, "modelId": model}
        if was is not None:
            params["expectedVersion"] = was.get("version")
        self.call("provider.select", params)


def connect_daemon(folder: Path, start: bool) -> Conn:
    sock = open_daemon(folder, start)
    timer = None
    if isinstance(sock, Bridge):
        # a pipe has no timeout: the bridge is closed after WAIT, which ends
        # whatever waits on it (magpie dimConnect)
        timer = threading.Timer(WAIT, sock.expire)
        timer.daemon = True
        timer.start()
    try:
        return Conn(sock, timer)
    except BaseException:
        if timer is not None:
            timer.cancel()
        sock.close()
        raise


def dim_url(u: str) -> str:
    """A base URL as Dim compares it."""
    return (u or "").strip().rstrip("/")


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def listed(at: Wiring) -> str:
    """Stands for the models the provider lists and the names it shows them by:
    Dim takes the names only when it lists the models again."""
    h = hashlib.sha256()
    for row in at.models:
        h.update(f"{row.get('id', '')}\x00{row.get('name', '')}\x00".encode("utf-8"))
    return h.hexdigest()[:16]


def limits(at: Wiring, ref: str) -> tuple[int, int]:
    """A model's context and output limits for Dim: the catalog's, else
    :data:`CONTEXT` and :data:`OUTPUT`, the output no larger than the context."""
    ctx = out = 0
    for row in at.models:
        if row.get("id") == ref:
            ctx, out = int(row.get("context_window") or 0), int(row.get("max_tokens") or 0)
            break
    if ctx <= 0:
        ctx = CONTEXT
    if out <= 0 or out > ctx:
        out = min(OUTPUT, ctx)
    return ctx, out


def wire(folder: Path, at: Wiring, rec: dict[str, Any], *, start: bool, pick: bool) -> dict[str, Any]:
    """Put Dim's provider on the gateway: its base URL, Dim's key and on; and,
    with ``pick``, list its models and make ``at.model`` Dim's model, giving
    it the limits Dim needs. What was there before is kept in ``rec`` the
    first time, for :func:`unwire`; ``rec`` is filled in as the change goes,
    so one cut short can be put back."""
    url, key = at.base("openai-chat"), at.key
    with _mu:
        c = connect_daemon(folder, start)
        try:
            p = c.provider(PROVIDER)
            if "wrote" not in rec:
                was = c.selection()
                if was is not None:
                    was = {"provider_id": was.get("provider_id"), "model_id": was.get("model_id")}
                base = dim_url((p.get("connection") or {}).get("baseUrl") or "")
                own = bool((p.get("connection") or {}).get("credentialConfigured")) or (
                    base != DEFAULT and base != dim_url(url))
                rec.update({"wrote": not own, "baseUrl": (p.get("connection") or {}).get("baseUrl") or "",
                            "selection": was})
            conn = p.get("connection") or {}
            if rec["wrote"] and (not rec.get("url") or dim_url(conn.get("baseUrl") or "") == dim_url(rec["url"])):
                patch: dict[str, Any] = {}
                if dim_url(conn.get("baseUrl") or "") != dim_url(url):
                    patch["baseUrl"] = url
                if not p.get("enabled"):
                    patch["enabled"] = True
                if not conn.get("credentialConfigured") or rec.get("key") != key_hash(key):
                    patch["credential"] = {"action": "set", "value": key}
                if patch:
                    c.update(p, patch)
                rec["url"], rec["key"] = url, key_hash(key)
            now = listed(at)
            if not pick:
                # Dim's list follows the models and their names, listed again
                # once they changed
                if not rec.get("picked") or rec.get("listed") == now:
                    return rec
                c.call("provider.refresh", {"providerId": PROVIDER})
                rec["listed"] = now
                return rec
            ref = at.model
            cat = c.call("provider.refresh", {"providerId": PROVIDER}) or {}
            rec["listed"] = now
            found = next((m for m in cat.get("models") or [] if m.get("id") == ref), None)
            if found is None:
                raise DimError(f"Dim's {PROVIDER} provider doesn't list {ref}", "")
            missing = found.get("missingLimits") or []
            if missing:
                # Dim picks no model without both limits; OpenProgram gives the ones it lacks
                ctx, out = limits(at, ref)
                given = {k: v for k, v in (("contextWindow", ctx), ("maxOutputTokens", out)) if k in missing}
                c.call("provider.models.edit", {"providerId": PROVIDER, "expectedVersion": cat.get("version"),
                                                "modelId": ref, "edit": given})
            cur = c.selection()
            if cur is None or cur.get("provider_id") != PROVIDER or cur.get("model_id") != ref:
                c.pick(PROVIDER, ref, cur)
            rec["picked"] = ref
            return rec
        finally:
            c.close()


def unwire(folder: Path, rec: dict[str, Any]) -> None:
    """Put back what :func:`wire` changed, where it is still as OpenProgram left
    it: Dim's model before, and the provider's address, key (cleared) and off."""
    if not rec:
        return
    with _mu:
        try:
            c = connect_daemon(folder, True)
        except DimAway:
            if not find_tool("dima"):
                return  # Dim is gone, its settings with it
            raise
        try:
            was = rec.get("selection")
            picked = rec.get("picked") or ""
            if picked and was and (was.get("provider_id") != PROVIDER or was.get("model_id") != picked):
                try:
                    cur = c.selection()
                    if cur and cur.get("provider_id") == PROVIDER and cur.get("model_id") == picked:
                        c.pick(was["provider_id"], was["model_id"], cur)
                except DimError:
                    pass  # a model since gone stays unpicked
            if not rec.get("wrote"):
                return
            p = c.provider(PROVIDER)
            conn = p.get("connection") or {}
            if not rec.get("url") or dim_url(conn.get("baseUrl") or "") != dim_url(rec["url"]):
                return
            # Dim clears a key only with the provider turned off in the same patch
            patch: dict[str, Any] = {"enabled": False}
            if conn.get("credentialConfigured"):
                patch["credential"] = {"action": "clear"}
            before = rec.get("baseUrl") or ""
            if before and dim_url(before) != dim_url(conn.get("baseUrl") or ""):
                patch["baseUrl"] = before
            c.update(p, patch)
        finally:
            c.close()


def _err(exc: Exception):
    from .engine import AgentConfigError
    return AgentConfigError(str(exc))


#: The command the shell function is named after.
COMMAND = "dima"
#: The variables Dim reads to start on an OpenAI-compatible endpoint for one run.
ENV_NAMES = ("DIM_API_FORMAT", "DIM_API_KEY", "DIM_BASE_URL", "DIM_MAX_CONTEXT_TOKENS",
             "DIM_MAX_OUTPUT_TOKENS", "DIM_MODEL")


def launch_env(at: Wiring) -> dict[str, str]:
    """The variables Dim reads to start on the gateway for one run (magpie
    ``dimLaunch``), the model in ``DIM_MODEL`` (a ``--model`` flag would come
    before a subcommand, which Dim refuses)."""
    ctx, out = limits(at, at.model)
    return {"DIM_API_FORMAT": "openai-compatible", "DIM_API_KEY": at.key, "DIM_BASE_URL": at.base("openai-chat"),
            "DIM_MAX_CONTEXT_TOKENS": str(ctx), "DIM_MAX_OUTPUT_TOKENS": str(out), "DIM_MODEL": at.model}


def _on_function() -> Optional[Wiring]:
    """Dim's wiring while it is on OpenProgram's model and its daemon's
    provider isn't (the ``dima`` function is in place), else ``None``."""
    from .. import stash
    try:
        record = stash.entry("dim") or {}
    except stash.StashUnreadable:
        return None
    values = dict(record.get("values") or {})
    if not values.get("model") or (record.get("live") or {}).get("shell") is None:
        return None
    from . import find
    from .engine import wiring
    return wiring(find("dim"), values)


def starting(at: Optional[Wiring] = None) -> dict[str, Any]:
    """What starts dima (magpie Dim's ``Start``): the ``DIM_*`` variables
    while it is on the function, and as it is otherwise (``model`` "")."""
    out: dict[str, Any] = {"bin": COMMAND, "flag": "", "plain": [], "env": {}, "args": [], "model": ""}
    at = at or _on_function()
    if at is not None:
        out.update(env=launch_env(at), model=at.model)
    return out


def run(args: list[str]) -> int:
    """``openprogram dim run [args…]``, what the function dima runs."""
    from .shellfn import run_launch
    return run_launch(starting(), args)


def env_lines() -> list[str]:
    """``openprogram dim env``, for PowerShell's function."""
    from .shellfn import launch_env_lines
    return launch_env_lines(starting())


def function(kind: str, prog: list[str]) -> str:
    """The function dima in a shell of ``kind`` (magpie ``launchSpec.function``)."""
    from .shellfn import launch_function
    return launch_function(kind, prog, "dim", {"bin": COMMAND, "env": dict.fromkeys(ENV_NAMES, "")})


def _shell(at: Optional[Wiring]) -> list[str]:
    """The ``dima`` function while Dim's provider isn't on the model (``at``),
    taken out for ``None``; the profiles that have it."""
    from . import shellfn
    if at is None:
        shellfn.drop_shell_function(COMMAND)
        return []
    return shellfn.shell_function(COMMAND, function)


def _wired(at: Wiring, rec: dict[str, Any]) -> bool:
    """Dim's own provider is on the model, so dima runs on it as it is."""
    return bool(rec.get("picked")) and rec.get("picked") == at.model


def _connect(at: Wiring, kept: dict[str, Any]) -> dict[str, Any]:
    folder = home()
    first = not kept.get("dim")
    rec = dict(kept.get("dim") or {})
    try:
        # connected on this model already (a moved port, a new key): the
        # address and key only, a model picked in Dim since stays
        wire(folder, at, rec, start=True, pick=not (at.same_pick and _wired(at, rec)))
    except (DimAway, DimError) as exc:
        if first and isinstance(exc, DimError):
            # a first connect cut short: what it changed goes back
            try:
                unwire(folder, rec)
            except (DimAway, DimError):
                pass
            rec = {}
        # no daemon to reach, or it doesn't take the model: dima is started
        # with the variables of a dima function instead
        return {"dim": rec, "shell": _shell(at)}
    _shell(None)
    return {"dim": rec}


def _disconnect(kept: dict[str, Any]) -> None:
    _shell(None)
    try:
        unwire(home(), kept.get("dim") or {})
    except (DimAway, DimError) as exc:
        raise _err(exc) from exc


def _sync(at: Wiring, kept: dict[str, Any]) -> dict[str, Any]:
    """A running daemon gets the gateway's address and key again (a new port),
    and its list again once the models or their names changed; none is
    started here. A Dim on the dima function moves to its provider once the
    daemon is reached, and the function goes; while it isn't, the function's
    variables follow the gateway."""
    rec = dict(kept.get("dim") or {})
    try:
        wire(home(), at, rec, start=False, pick=not _wired(at, rec))
    except (DimAway, DimError) as exc:
        if kept.get("shell") is not None or isinstance(exc, DimAway) and not _wired(at, rec):
            return {**kept, "dim": rec, "shell": _shell(at)}
        if isinstance(exc, DimAway):
            return kept
        raise _err(exc) from exc
    _shell(None)
    return {"dim": rec}


def _notice() -> str:
    from .. import stash
    record = stash.entry("dim") or {}
    held = (record.get("live") or {}).get("shell")
    if held is None:
        return ""
    if held:
        where = ", ".join(p.replace(str(Path.home()), "~", 1) for p in held)
        return (f"Dim's daemon couldn't take the model, so dima starts on OpenProgram through the dima function "
                f"in {where}: open a new terminal and run dima.")
    return f"Dim's daemon couldn't take the model; start dima on OpenProgram with: {launch_command()}"


def launch_command(powershell: bool = sys.platform == "win32") -> str:
    """What starts dima on the gateway in this system's shell (magpie ``dimCommand``)."""
    from .shellfn import posix_quote, ps_quote
    out = ""
    for name, value in starting()["env"].items():
        out += f"$env:{name}={ps_quote(value)}; " if powershell else f"{name}={posix_quote(value)} "
    return out + COMMAND


def agent() -> Agent:
    return Agent(
        id="dim", name="Dim", icon="dim-color", kind="cli", protocol="openai-responses",
        aliases=("dima", "dimcode-alpha"), bin="dima", dir=home, path=lambda: home() / "rust" / "dimcode.sqlite",
        fields=[Field("model", "Model")],
        wire=lambda at: [], notice=_notice, live=Live(connect=_connect, disconnect=_disconnect, sync=_sync),
    )
