"""Docker backend.

Two modes:

* **Conversation container** (``container=`` set): a Docker machine gives
  each conversation one long-lived container, its instance, started on
  first use and reused by every later command, so installed packages and
  background processes survive between calls. The conversation's folders
  are bind-mounted at their host paths, which keeps file tools and shell
  commands looking at the same files under the same names. When the
  folder set changes (a working folder was added), the container is
  recreated with the new mounts. Its labels name the machine, the
  conversation and this installation's state directory, so
  :func:`remove_containers` finds it after the machine or the
  conversation is gone, and never touches the containers of another
  installation (the stable and the dev instance share one Docker).
* **One-shot** (no ``container``): ``docker run --rm`` per call, for
  callers outside a conversation (CLI checks, tests).
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import threading

from openprogram.host.backend.base import Backend, RunResult, decode_maybe, running_on, spawned_on


_log = logging.getLogger(__name__)
_NOT_FOUND = ("docker CLI not on PATH — install Docker or pick another "
              "environment.")
MACHINE_LABEL = "openprogram.machine"
CONVERSATION_LABEL = "openprogram.conversation"
HOME_LABEL = "openprogram.home"
_ready: dict[str, str] = {}
_ready_lock = threading.Lock()


def _mount_hash(image: str, mounts: tuple[str, ...]) -> str:
    raw = json.dumps([image, sorted(mounts)]).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def home_label() -> str:
    """This installation, as its containers are labelled: a digest of its state directory."""
    from openprogram.state.paths import get_state_dir
    return hashlib.sha256(str(get_state_dir().resolve()).encode()).hexdigest()[:16]


def _gone(proc, container: str) -> bool:
    """``docker exec`` was refused because the container was removed or stopped since it was last seen.

    Only the daemon's own refusal counts, not a command that failed with those words in its output.
    """
    err = (proc.stderr or "").strip()
    if proc.returncode == 0 or not err.startswith("Error response from daemon:"):
        return False
    return f"No such container: {container}" in err or "is not running" in err.lower()


def _switched_off(machine_id: str) -> str | None:
    """Why no container may start for ``machine_id`` now, or None.

    Asked after the command is counted on the machine: the configuration is
    written before a removal waits for counted commands, so either the removal
    waits for this command or this sees the machine off, and a container is
    never created again for a machine that is off or gone.
    """
    if not machine_id:
        return None
    from openprogram.host.machines import get_machine
    machine = get_machine(machine_id)
    if machine is None:
        return f"machine {machine_id!r} was removed"
    if not machine.enabled:
        return f"machine {machine_id!r} was switched off"
    return None


class DockerBackend(Backend):
    backend_id = "docker"

    def __init__(self, image: str = "ubuntu:24.04", *,
                 container: str | None = None,
                 mounts: tuple[str, ...] = (),
                 labels: dict[str, str] | None = None) -> None:
        self.image = image
        self.container = container
        self.mounts = tuple(m for m in mounts if m)
        self.labels = dict(labels or {})

    # ---- session container ------------------------------------------------

    def _ensure_container(self) -> str | None:
        """Start (or adopt) the conversation's container. Returns an error text."""
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
                    "--label", f"openprogram.mounts={want}", "--label", f"{HOME_LABEL}={home_label()}"]
            for key, value in self.labels.items():
                argv += ["--label", f"{key}={value}"]
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
        with running_on(self.machine_id):
            return self._run(command, timeout, cwd)

    def _exec(self, command: str, timeout: float, cwd: str | None):
        return subprocess.run(self._argv(command, cwd), capture_output=True, text=True, timeout=timeout)

    def _run(self, command: str, timeout: float, cwd: str | None) -> RunResult:
        if self.container:
            error = _switched_off(self.machine_id) or self._ensure_container()
            if error:
                return RunResult(exit_code=127 if error == _NOT_FOUND else 1,
                                 stdout="", stderr=error)
        try:
            proc = self._exec(command, timeout, cwd)
            if self.container and _gone(proc, self.container):
                # Removed or stopped since it was last seen (by cleanup, by hand, by a Docker restart):
                # forget it and start it again once, unless a removal of its machine took it.
                with _ready_lock:
                    _ready.pop(self.container, None)
                error = _switched_off(self.machine_id) or self._ensure_container()
                if error:
                    return RunResult(exit_code=127 if error == _NOT_FOUND else 1, stdout="", stderr=error)
                proc = self._exec(command, timeout, cwd)
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
            # No exec result comes back to recover from, so the cache is not trusted: the container may
            # have been stopped or removed since it was last seen.
            with _ready_lock:
                _ready.pop(self.container, None)
            # Counted while the container is made, so a removal of the machine waits for it.
            with running_on(self.machine_id):
                error = _switched_off(self.machine_id) or self._ensure_container()
            if error:
                raise RuntimeError(error)
        return {"args": self._argv(command, cwd)}

    def spawn(self, command: str,
              cwd: str | None = None) -> subprocess.Popen:
        # A one-shot container is torn down with its client (--rm); a
        # session container keeps running and only this exec ends.
        return spawned_on(self.machine_id, subprocess.Popen(
            **self.spawn_spec(command, cwd=cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        ))


def remove_containers(*, machine: str | None = None,
                      conversation: str | None = None) -> None:
    """Remove this installation's containers labelled with ``machine`` and/or ``conversation``.

    Found by label, not by the machines configured now, so the containers
    of a machine that was deleted or switched off go too. Another
    installation's containers carry another home label and stay. Best
    effort: no Docker on this computer means there is nothing to remove.
    """
    filters = []
    if machine:
        filters += ["--filter", f"label={MACHINE_LABEL}={machine}"]
    if conversation:
        filters += ["--filter", f"label={CONVERSATION_LABEL}={conversation}"]
    if not filters:
        return
    filters += ["--filter", f"label={HOME_LABEL}={home_label()}"]
    try:
        listed = subprocess.run(["docker", "ps", "-aq", *filters],
                                capture_output=True, text=True, timeout=60)
    except Exception:
        return
    ids = listed.stdout.split() if listed.returncode == 0 else []
    if not ids:
        return
    try:
        subprocess.run(["docker", "rm", "-f", *ids], capture_output=True,
                       timeout=120)
    except Exception:
        _log.debug("removing containers %s failed", ids, exc_info=True)
    with _ready_lock:
        # After the removal, so an inspect that raced it is forgotten too; a command that still finds its
        # container gone starts it again (``run``).
        _ready.clear()
