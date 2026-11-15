"""The candidate's own updater, run on dummy versions inside the trial.

A changed updater applies from the next switch, so the trial has to prove now
that the code about to become the controller can still do its job: move the
pointer forward and back in a form the signed launcher reads, and read and
write the ``self-updates/`` files the old version also reads.

Run by the candidate's Python: ``python -m ...trial.self_test <action> ...``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

_TEST_UPDATE = "su_trial_self_test"
_BASE = "a" * 40
_CANDIDATE = "b" * 40


class _NoWorker:
    """What the controller does to a real worker. There is none: the dummy versions are the subject."""

    def wait_idle(self, until: float) -> bool:
        return True

    def local_tip(self) -> str:
        return _BASE

    def request_checkpoint(self, record) -> None:
        pass

    def probe_old(self, record) -> dict:
        return {"gate": {"candidate_sha": _BASE}, "doctor": {}, "subsystems": {}}

    def stop_worker(self) -> None:
        pass

    def start_worker(self) -> None:
        pass

    def is_up(self, record, commit: str) -> int:
        return os.getpid()

    def migrating(self, version: str) -> bool:
        return False

    def probe_restored(self, record, revision: str, reference: dict) -> dict:
        return {"candidate_sha": revision}

    def note_recovery(self, line: str) -> None:
        pass


def _controller(root: str):
    from ..control.switch import SwitchController
    from ..store import SelfUpdateStore
    from ..versions.layout import Layout

    return SwitchController(SelfUpdateStore(), _TEST_UPDATE, _NoWorker(), Layout(Path(root)))


def _switch(root: str, version: str, base: str) -> dict:
    """The candidate's own controller takes a ready update through the pointer move."""
    from ..store import SelfUpdateStore
    from ..types import UpdatePhase, UpdateRequest
    from ..versions import pointer
    from ..versions.layout import Layout

    layout = Layout(Path(root))
    store = SelfUpdateStore()
    store.create(UpdateRequest(
        update_id=_TEST_UPDATE, session_id="trial-self-test", origin_turn_id="trial", origin_assistant_id="trial",
        agent_id="main", repo=os.path.normpath(root), worktree_id="none", base_sha=_BASE, candidate_sha=_CANDIDATE,
        changed_paths=("pyproject.toml",), pre_update_evidence=("trial-self-test",), goal="Updater self-test",
        assertions=("The pointer moves and comes back.",), controller_version=pointer.read(layout).version,
        candidate_version=version,
    ))
    for phase in (UpdatePhase.TRIAL, UpdatePhase.READY):
        store.transition(_TEST_UPDATE, phase, detail={})
    _controller(root).advance(until=UpdatePhase.VERIFYING)
    moved = pointer.read(layout)
    return {"version": moved.version, "generation": moved.generation,
            "phase": store.load(_TEST_UPDATE).state.phase.value}


def _rollback(root: str, candidate: str) -> dict:
    from ..store import SelfUpdateStore
    from ..versions import pointer
    from ..versions.layout import Layout

    _controller(root)._rollback("updater self-test")
    return {"version": pointer.read(Layout(Path(root))).version,
            "phase": SelfUpdateStore().load(_TEST_UPDATE).state.phase.value}


def _sweep() -> dict:
    """What a worker does at start with an update it finds in flight."""
    from ..control.recovery import recover_pending_updates

    ready = recover_pending_updates()
    return {"ready": bool(ready)}


def main(arguments: list[str]) -> int:
    action, *rest = arguments
    result = {"switch": _switch, "rollback": _rollback, "sweep": _sweep}[action](*rest)
    print(json.dumps(result), flush=True)
    sys.stdout.flush()
    os._exit(0)  # A sweep leaves runner threads behind; nothing more is wanted of them.


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
