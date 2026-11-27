"""Give an official update to the update controller.

``official.replay`` decides which commits ride on an update and may try several
candidates. Each candidate is one update record of kind ``official``, so a
trial that fails is an ``aborted`` record and the phase table stays as it is.

This runs outside the worker, because the switch restarts the worker. It holds
each update's controller lock and heartbeat, the same as the controller the
tool starts; if it dies, the worker's watchdog restarts that controller and the
update finishes from its phase.

The desktop App splits the two halves, as other apps do with a download in the
background and a switch on restart: ``upgrade --prepare`` stops a candidate
that passed its trial in ``ready``, marked ``held`` and with no controller, and
the next ``upgrade`` switches to it (:func:`activate_held`). Nothing else moves
a held update; another update that needs the slot cancels it first.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import subprocess
from typing import Iterable

from ..official import replay
from ..official.repository import ReleaseError
from ..store import SelfUpdateStore
from ..types import ActiveUpdateError, UpdatePhase, UpdateRecord, UpdateRequest, is_held, is_terminal, mint_update_id
from ..versions import pointer as pointers
from ..versions import store as sealed
from openprogram.host._platform.runtime_layout import Layout, current_runtime, shell_hosts
from . import backend
from .switch import LiveHost, SwitchController, candidate_version, controlling, take_next

_NO_CONVERSATION = "official-update"
_AGENT = "main"
#: Outcome of ``upgrade --prepare``: tried and held for a restart.
PREPARED = "prepared"


class _Attempts:
    """One record per candidate; a trial that passes keeps its record for activation."""

    def __init__(self, store: SelfUpdateStore, layout: Layout, host, controller_version: str,
                 hold: dict | None = None) -> None:
        self.store, self.layout, self.host, self.controller_version = store, layout, host, controller_version
        self.hold_marks = hold
        self.update_id: str | None = None
        self.held = False
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
                                  detail={"turn_released": True, **(self.hold_marks or {})})
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

    def hold(self, version: str) -> replay.Verdict:
        """In place of ``activate`` for ``--prepare``: leave the passed trial in ``ready``, held."""
        try:
            self.held = is_held(self.store.load(str(self.update_id)))
            return replay.Verdict(self.held, "" if self.held else "the prepared update did not stay ready")
        finally:
            self._release_control()


def _busy(store: SelfUpdateStore) -> str | None:
    """The update holding the slot; a held one gives it up, so it does not count."""
    active = store.load_active()
    if active is None or is_terminal(active.state.phase) or is_held(active):
        return None
    return active.request.update_id


def _tell_owner(result: replay.ReplayResult) -> None:
    """The owner hears the result in their latest conversation, as for direct edits."""
    from ..delivery.owner_notice import tell

    try:
        tell(f"official-{result.release}-{result.outcome}", result.message)
    except Exception:  # noqa: BLE001 - the command already printed the result.
        logging.getLogger(__name__).warning("the release result was not posted", exc_info=True)


def apply_plan(
    update, *, store: SelfUpdateStore | None = None, host=None, layout: Layout | None = None,
    controller_version: str | None = None, hold: dict | None = None,
) -> replay.ReplayResult:
    """Apply a downloaded update (``official.assemble.Plan``); with ``hold``, prepare and hold it."""
    def run(layout: Layout, lifecycle: replay.Lifecycle, update_id: str, parent: str | None) -> replay.ReplayResult:
        return replay.replay_update(layout, update, lifecycle, update_id=update_id, parent=parent)

    return _controlled(run, store=store, host=host, layout=layout, controller_version=controller_version, hold=hold)


def _runtime(layout: Layout | None, controller_version: str | None) -> tuple[Layout, str]:
    if layout is None or controller_version is None:
        runtime = current_runtime()
        if runtime is None:
            raise ReleaseError("this installation does not run from the version store")
        layout, controller_version = runtime.layout, runtime.version
    return layout, controller_version


def _controlled(
    run, *, store: SelfUpdateStore | None, host, layout: Layout | None, controller_version: str | None,
    hold: dict | None = None,
) -> replay.ReplayResult:
    layout, controller_version = _runtime(layout, controller_version)
    target = store or SelfUpdateStore()
    busy = _busy(target)
    if busy is not None:
        return replay.ReplayResult(outcome=replay.ABORTED, message=f"Update {busy} is running; try again when it finishes.")
    attempts = _Attempts(target, layout, host or LiveHost(target, layout), controller_version, hold)
    lifecycle = replay.Lifecycle(trial=attempts.trial, activate=attempts.hold if hold else attempts.activate)
    parent = pointers.read_or_recover(layout).version
    result = run(layout, lifecycle, mint_update_id(), parent)
    if attempts.held and result.outcome == replay.ACTIVATED:
        result.outcome = PREPARED
        result.message = f"{result.release or 'The update'} is ready; it applies when OpenProgram restarts."
    if attempts.update_id is not None:
        attempts.host.retention()
        take_next(target)
        if result.outcome != PREPARED:
            _tell_owner(result)
    return result


# Held for a restart ---------------------------------------------------------


def held_update(store: SelfUpdateStore | None = None) -> UpdateRecord | None:
    """The update prepared and held for a restart, if there is one."""
    target = store or SelfUpdateStore()
    if not target.root.exists():
        return None
    record = target.load_active()
    return record if record is not None and is_held(record) else None


def describe_held(record: UpdateRecord) -> dict:
    """What ``upgrade --check --json`` says about a held update."""
    detail = record.state.detail
    return {"version": candidate_version(record), "target": str(detail.get("target") or ""),
            "channel": str(detail.get("channel") or ""), "latest_version": str(detail.get("latest_version") or ""),
            "announce": bool(detail.get("announce"))}


def activate_held(
    record: UpdateRecord, *, store: SelfUpdateStore | None = None, host=None, layout: Layout | None = None,
) -> replay.ReplayResult | None:
    """Switch to a held update. ``None`` when it no longer fits and was cancelled.

    It was tried on top of ``local`` as it was then; when ``local`` has moved
    since, the held version lacks those commits, so it is cancelled and the
    caller installs the update the usual way, which carries them over.
    """
    if layout is None:
        layout, _ = _runtime(None, None)
    target = store or SelfUpdateStore()
    live = host or LiveHost(target, layout)
    update_id = record.request.update_id
    name = str(record.state.detail.get("target") or "the update")
    with controlling(target.root / update_id) as acquired:
        if not acquired:
            return replay.ReplayResult(outcome=replay.ABORTED, release=name,
                                       message=f"{name} is already being applied; try again when it finishes.")
        current = target.load(update_id)
        if not is_held(current):
            return replay.ReplayResult(outcome=replay.ABORTED, release=name,
                                       message=f"{name} is no longer waiting for a restart.")
        if not _installed_shell_hosts(layout, candidate_version(current)):
            # Prepared with the App shell staged beside the App: it runs only in that App, after the swap.
            if _staged_app_ready(layout):
                return _held_result(current)
            # That App is gone: install the update the usual way, which stages it again.
            target.cancel_held("the new App it needs is no longer staged")
            return None
        base = str(current.state.detail.get("base_sha") or current.request.base_sha)
        if live.local_tip() != base:
            target.cancel_held("the local line moved after it was prepared")
            return None
        SwitchController(target, update_id, live, layout).advance(release_hold=True)
    state = target.load(update_id).state
    reason = str(state.detail.get("reason") or state.phase.value)
    if state.phase is UpdatePhase.SUCCEEDED:
        result = replay.ReplayResult(outcome=replay.ACTIVATED, release=name, message=f"Updated to {name}.")
    elif state.phase is UpdatePhase.ROLLED_BACK:
        result = replay.ReplayResult(outcome=replay.ROLLED_BACK, release=name,
                                     message=f"{name} was returned to the previous version: {reason}")
    else:
        result = replay.ReplayResult(outcome=replay.ABORTED, release=name,
                                     message=f"{name} was not applied, and the installation is unchanged: {reason}")
    result.version = candidate_version(record)
    if is_terminal(state.phase):
        live.retention()
        take_next(target)
        _tell_owner(result)
    return result


def _installed_shell_hosts(layout: Layout, version: str) -> bool:
    try:
        manifest = sealed.read_manifest(layout, version)
    except Exception:  # noqa: BLE001 - an unreadable version fails in the switch, which says why.
        return True
    compat = manifest.get("shell_compat")
    return shell_hosts(layout, compat, str(manifest.get("base") or "") or None, compat=compat is not None,
                       staged=False)


def _staged_app_ready(layout: Layout) -> bool:
    """Whether a new App is staged beside the running one, for Restart to update to swap in."""
    from ..official import shell as shells
    from ..official.repository import current_platform

    app = shells.running_app(current_platform())
    shells.forget_stale(layout, app)
    staged = shells.read_staged(layout)
    if app is None or staged is None:
        return False
    path = Path(str(staged.get("path") or ""))
    return path == shells.staged_target(app) and path.exists()


def _held_result(record: UpdateRecord) -> replay.ReplayResult:
    name = str(record.state.detail.get("target") or "The update")
    return replay.ReplayResult(outcome=PREPARED, release=name, version=candidate_version(record),
                               message=f"{name} is ready; it applies when OpenProgram restarts.")


def _about(record: UpdateRecord | None, found=None) -> dict:
    """``channel``, ``latest_version`` and ``announce`` for the command's report."""
    if found is not None and (found.done is None or record is None):
        return found.about()
    if record is not None:
        held = describe_held(record)
        return {"channel": held["channel"], "latest_version": held["latest_version"], "announce": held["announce"]}
    return {"channel": "", "latest_version": "", "announce": False}


def prepare_update(
    *, store: SelfUpdateStore | None = None, say=None, trusted_keys: Iterable[str] | None = None, transport=None,
) -> tuple[replay.ReplayResult, dict]:
    """``upgrade --prepare``: download, build and try the update, then hold it for a restart.

    A held update for the same target is reported again without work; one for
    an older target is cancelled and the new one prepared. Offline, a held
    update is still reported.
    """
    from ..official import fetch

    target = store or SelfUpdateStore()
    held = held_update(target)
    try:
        found = fetch.discover(trusted_keys=trusted_keys, transport=transport)
    except ReleaseError:
        if held is not None:
            return _held_result(held), _about(held)
        raise
    if held is not None:
        if found.done is None and describe_held(held)["target"] == found.target:
            return _held_result(held), _about(held, found)
        target.cancel_held(f"replaced by {found.target or 'a newer state'}")
    if found.done is not None:
        return found.done, _about(None, found)
    return fetch.update(found=found, hold=True, say=say, trusted_keys=trusted_keys), _about(None, found)


def apply_update(
    *, store: SelfUpdateStore | None = None, say=None, trusted_keys: Iterable[str] | None = None,
    host=None, layout: Layout | None = None, transport=None,
) -> tuple[replay.ReplayResult, dict]:
    """``upgrade``: switch to the held update when it is still what would be installed, else install as usual."""
    from ..official import fetch

    target = store or SelfUpdateStore()
    held = held_update(target)
    found = None
    if held is not None:
        try:
            found = fetch.discover(trusted_keys=trusted_keys, transport=transport)
        except ReleaseError:
            found = None  # Offline: the held update is what there is.
        if found is None or (found.done is None and describe_held(held)["target"] == found.target):
            about = _about(held, found)
            result = activate_held(held, store=target, host=host, layout=layout)
            if result is not None:
                return result, about
            found = None  # Cancelled because ``local`` moved: install it the usual way.
        elif found.done is not None:
            target.cancel_held("nothing to install any more")
    if found is None:
        found = fetch.discover(trusted_keys=trusted_keys, transport=transport)
    return fetch.update(found=found, say=say, trusted_keys=trusted_keys), _about(None, found)


__all__ = ["PREPARED", "apply_update", "prepare_update", "activate_held", "apply_plan", "describe_held", "held_update"]
