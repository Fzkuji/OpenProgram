"""Docker backend.

Two modes:

* **Session container** (``container=`` set): a Docker environment gives
  each conversation one long-lived container, started on first use and
  reused by every later command, so installed packages and background
  processes survive between calls. The session's folders are
  bind-mounted at their host paths, which keeps file tools and shell
  commands looking at the same files under the same names. When the
  folder set changes (a working folder was added), the container is
  recreated with the new mounts.
* **One-shot** (no ``container``): ``docker run --rm`` per call, for
  callers outside a session (CLI checks, tests).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import threading

from openprogram.host.backend.base import Backend, RunResult, decode_maybe


_NOT_FOUND = ("docker CLI not on PATH — install Docker or pick another "
              "environment.")
_ready: dict[str, str] = {}
_ready_lock = threading.Lock()


def _mount_hash(image: str, mounts: tuple[str, ...]) -> str:
    raw = json.dumps([image, sorted(mounts)]).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


class DockerBackend(Backend):
    backend_id = "docker"

    def __init__(self, image: str = "ubuntu:24.04", *,
                 container: str | None = None,
                 mounts: tuple[str, ...] = ()) -> None:
        self.image = image
        self.container = container
        self.mounts = tuple(m for m in mounts if m)

    # ---- session container ------------------------------------------------

    def _ensure_container(self) -> str | None:
        """Start (or adopt) the session container. Returns an error text."""
        assert self.container
        want = _mount_hash(self.image, self.mounts)
        with _ready_lock:
            if _ready.get(self.container) == want:
                return None
        try:
            probe = subprocess.run(
                ["docker", "inspect", "-f",
                 '{{.State.Running}} {{index .Config.Labels "openprogram.mounts"}}',
                 self.container],
                capture_output=True, text=True, timeout=30,
            )
        except FileNotFoundError:
            return _NOT_FOUND
        except subprocess.TimeoutExpired:
            return "docker did not answer within 30s"
        running, _, label = probe.stdout.strip().partition(" ")
        if probe.returncode == 0 and label != want:
            subprocess.run(["docker", "rm", "-f", self.container],
                           capture_output=True, timeout=60)
            probe = subprocess.CompletedProcess(probe.args, 1, "", "")
        if probe.returncode == 0 and running != "true":
            started = subprocess.run(["docker", "start", self.container],
                                     capture_output=True, text=True, timeout=60)
            if started.returncode != 0:
                return f"docker start failed: {started.stderr.strip()}"
        elif probe.returncode != 0:
            argv = ["docker", "run", "-d", "--init", "--name", self.container,
                    "--label", "openprogram.session=1",
                    "--label", f"openprogram.mounts={want}"]
            for path in self.mounts:
                argv += ["-v", f"{path}:{path}"]
            if self.mounts:
                argv += ["-w", self.mounts[0]]
            argv += [self.image, "tail", "-f", "/dev/null"]
            try:
                created = subprocess.run(argv, capture_output=True, text=True,
                                         timeout=600)
            except subprocess.TimeoutExpired:
                return "docker run timed out (image pull may still be running)"
            if created.returncode != 0:
                return f"docker run failed: {created.stderr.strip()}"
        with _ready_lock:
            _ready[self.container] = want
        return None

    def _argv(self, command: str, cwd: str | None = None) -> list[str]:
        if self.container:
            argv = ["docker", "exec", "-i"]
            if cwd:
                argv += ["-w", cwd]
            return argv + [self.container, "sh", "-c", command]
        argv = ["docker", "run", "--rm", "-i"]
        if cwd:
            argv += ["-w", cwd]
        argv += [self.image, "sh", "-c", command]
        return argv

    def run(self, command: str, timeout: float,
            cwd: str | None = None) -> RunResult:
        if self.container:
            error = self._ensure_container()
            if error:
                return RunResult(exit_code=127 if error == _NOT_FOUND else 1,
                                 stdout="", stderr=error)
        try:
            proc = subprocess.run(
                self._argv(command, cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return RunResult(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as e:
            return RunResult(
                exit_code=-1,
                stdout=decode_maybe(e.stdout),
                stderr=decode_maybe(e.stderr),
                timed_out=True,
            )
        except FileNotFoundError:
            return RunResult(exit_code=127, stdout="", stderr=_NOT_FOUND)

    def spawn_spec(self, command: str, cwd: str | None = None) -> dict:
        if self.container:
            error = self._ensure_container()
            if error:
                raise RuntimeError(error)
        return {"args": self._argv(command, cwd)}

    def spawn(self, command: str,
              cwd: str | None = None) -> subprocess.Popen:
        # A one-shot container is torn down with its client (--rm); a
        # session container keeps running and only this exec ends.
        return subprocess.Popen(
            **self.spawn_spec(command, cwd=cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )


def remove_session_containers(session_id: str) -> None:
    """Best-effort removal of a deleted session's containers."""
    from openprogram.host.backend import session_container_name
    try:
        from openprogram.host.environments import list_environments
        names = [session_container_name(env.id, session_id)
                 for env in list_environments() if env.kind == "docker"]
    except Exception:
        return
    for name in names:
        with _ready_lock:
            _ready.pop(name, None)
        try:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True,
                           timeout=60)
        except Exception:
            return
