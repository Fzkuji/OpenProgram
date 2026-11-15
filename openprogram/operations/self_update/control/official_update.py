"""Give an official release to the update controller.

``official.replay`` decides which commits ride on a release and may try several
candidates. Each candidate is one update record of kind ``official``, so a
trial that fails is an ``aborted`` record and the phase table stays as it is.

This runs outside the worker, because the switch restarts the worker. It holds
each update's controller lock and heartbeat, the same as the controller the
tool starts; if it dies, the worker's watchdog restarts that controller and the
update finishes from its phase.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import subprocess
from typing import Iterable

from ..official import replay
from ..official.assets import ReleaseError
from ..store import SelfUpdateStore
from ..types import ActiveUpdateError, UpdatePhase, UpdateRequest, is_terminal, mint_update_id
from ..versions import pointer as pointers
from ..versions import store as sealed
from ..versions.layout import Layout, current_runtime
from . import backend
from .switch import LiveHost, SwitchController, controlling, take_next

_NO_CONVERSATION = "official-update"
_AGENT = "main"


class _Attempts:
    """One record per candidate; a trial that passes keeps its record for activation."""

    def __init__(self, store: SelfUpdateStore, layout: Layout, host, controller_version: str) -> None:
        self.store, self.layout, self.host, self.controller_version = store, layout, host, controller_version
        self.update_id: str | None = None
        self._control = None

    def _changed(self, commit: str, tip: str) -> tuple[str, ...]:
        done = subprocess.run(["git", "-C", str(self.layout.repo), "diff", "--name-only", "-z", f"{tip}..{commit}"],
                              capture_output=True, text=True, timeout=120)
        return tuple(path for path in done.stdout.split("\0") if path) or ("pyproject.toml",)

    def _request(self, version: str) -> UpdateRequest:
        commit = str(sealed.read_manifest(self.layout, version)["commit"])
        tip = self.host.local_tip()
        return UpdateRequest(
            update_id=mint_update_id(), session_id=_NO_CONVERSATION, origin_turn_id=_NO_CONVERSATION,
            origin_assistant_id=_NO_CONVERSATION, agent_id=_AGENT, repo=os.path.normpath(str(self.layout.repo)),
            worktree_id="none", base_sha=tip, candidate_sha=commit, changed_paths=self._changed(commit, tip),
            pre_update_evidence=(f"official-version:{version}",), goal=f"Install the official release as {version}",
            assertions=(f"OpenProgram runs commit {commit}.",), kind="official",
            timeout_seconds=backend.timeout_seconds(backend.VERSIONS),
            controller_version=self.controller_version, candidate_version=version,
        )

    def _controller(self) -> SwitchController:
        return SwitchController(self.store, str(self.update_id), self.host, self.layout)

    def _verdict(self, passed_phase: UpdatePhase) -> replay.Verdict:
        state = self.store.load(str(self.update_id)).state
        if state.phase is passed_phase:
            return replay.Verdict(True)
        return replay.Verdict(False, str(state.detail.get("reason") or state.phase.value))

    def _release_control(self) -> None:
        control, self._control = self._control, None
        if control is not None:
            control.__exit__(None, None, None)

    def trial(self, version: str) -> replay.Verdict:
        request = self._request(version)
        try:
            self.store.create(request)
        except ActiveUpdateError as exc:
            # Not this candidate's fault, so replay must not drop a commit over it.
            raise ReleaseError(f"another update is running: {exc}") from exc
        self.update_id = request.update_id
        self._control = controlling(self.store.root / request.update_id)
        if not self._control.__enter__():
            self._release_control()
            raise ReleaseError("another controller already holds this update")
        try:
            self.store.transition(request.update_id, UpdatePhase.TRIAL, expected_phase=UpdatePhase.PREPARING,
                                  detail={"turn_released": True})
            self._controller().advance(until=UpdatePhase.READY)
            verdict = self._verdict(UpdatePhase.READY)
        except BaseException:
            self._release_control()
            raise
        if not verdict.passed:
            self._release_control()
        return verdict

    def activate(self, version: str) -> replay.Verdict:
        try:
            self._controller().advance()
            return self._verdict(UpdatePhase.SUCCEEDED)
        finally:
            self._release_control()


def _busy(store: SelfUpdateStore) -> str | None:
    active = store.load_active()
    if active is None or is_terminal(active.state.phase):
        return None
    return active.request.update_id


def _tell_owner(result: replay.ReplayResult) -> None:
    """The owner hears the result in their latest conversation, as for direct edits."""
    from ..delivery.owner_notice import tell

    try:
        tell(f"official-{result.release}-{result.outcome}", result.message)
    except Exception:  # noqa: BLE001 - the command already printed the result.
        logging.getLogger(__name__).warning("the release result was not posted", exc_info=True)


def apply_release(
    directory: Path, *, store: SelfUpdateStore | None = None, host=None, layout: Layout | None = None,
    controller_version: str | None = None, trusted_keys: Iterable[str] | None = None, platform: str | None = None,
) -> replay.ReplayResult:
    """Apply the release files in ``directory`` to this version store."""
    if layout is None or controller_version is None:
        runtime = current_runtime()
        if runtime is None:
            raise ReleaseError("this installation does not run from the version store")
        layout, controller_version = runtime.layout, runtime.version
    target = store or SelfUpdateStore()
    busy = _busy(target)
    if busy is not None:
        return replay.ReplayResult(outcome=replay.ABORTED, message=f"Update {busy} is running; try again when it finishes.")
    attempts = _Attempts(target, layout, host or LiveHost(target, layout), controller_version)
    lifecycle = replay.Lifecycle(trial=attempts.trial, activate=attempts.activate)
    parent = pointers.read_or_recover(layout).version
    result = replay.replay_release(layout, Path(directory), lifecycle, update_id=mint_update_id(), parent=parent,
                                   platform=platform, trusted_keys=trusted_keys)
    if attempts.update_id is not None:
        attempts.host.retention()
        take_next(target)
        _tell_owner(result)
    return result


__all__ = ["apply_release"]
