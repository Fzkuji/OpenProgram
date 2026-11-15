"""The whole trial, with real workers, in a temporary root and on ephemeral ports."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

import pytest

from openprogram.operations.self_update.store import SelfUpdateStore
from openprogram.operations.self_update.trial import sandbox
from openprogram.operations.self_update.trial.engine import TrialRequest, run_trial
from openprogram.operations.self_update.types import UpdatePhase, UpdateRequest
from openprogram.operations.self_update.versions.layout import Layout
from tests.support.trial_versions import build_version, compile_launcher, make_base

pytestmark = [
    pytest.mark.skipif(
        sys.platform != "darwin" or shutil.which("cc") is None or sandbox.isolation_level() != "sandbox",
        reason="needs a C compiler and sandbox-exec",
    ),
    pytest.mark.timeout(600),
]
OLD_COMMIT, NEW_COMMIT = "1" * 40, "2" * 40
SECRET = "sk-live-do-not-copy"


def _seed_live_state(state: Path) -> None:
    (state / "auth").mkdir(parents=True)
    (state / "auth" / "profile.json").write_text(json.dumps({"key": SECRET}), encoding="utf-8")
    (state / "config.json").write_text(json.dumps({
        "hooks": {"session.start": [{"command": "touch /tmp/never"}]},
        "providers": {"openai": {"enabled": True, "api_key": SECRET}},
        "execution": {"auto_resume_window_seconds": -1},
    }), encoding="utf-8")
    from openprogram.state.store.session.session_log import encode_record

    chat = state / "projects" / "default" / "chats" / "seeded"
    chat.mkdir(parents=True)
    (chat / "main.jsonl").write_bytes(encode_record(1, "meta.patch", {"set": {"title": "seeded"}}))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("trial")
    state = root / "home" / ".openprogram"
    layout = Layout(state / "runtime")
    layout.versions.mkdir(parents=True)
    make_base(layout)
    launcher = compile_launcher(root / "launcher")
    build_version(layout, "version-old", OLD_COMMIT)
    build_version(layout, "version-new", NEW_COMMIT)
    _seed_live_state(state)
    return root, state, layout, launcher


def _record(store: SelfUpdateStore, update_id: str, *, repo: Path):
    store.create(UpdateRequest(
        update_id=update_id, session_id="session-1", origin_turn_id="turn-1", origin_assistant_id="assistant-1",
        agent_id="main", repo=str(repo), worktree_id="wt-1", base_sha=OLD_COMMIT, candidate_sha=NEW_COMMIT,
        changed_paths=("openprogram/example.py",), pre_update_evidence=("pytest:tests/unit/example.py",),
        goal="Add the requested behavior", assertions=("The public entry returns the new result",),
        controller_version="version-old",
    ))
    return store.load(update_id)


def _request(built, update_id: str, candidate: str = "version-new") -> TrialRequest:
    _root, state, layout, launcher = built
    store = SelfUpdateStore(state / "self-updates")
    active = store.load_active()
    if active is not None:
        store.transition(active.request.update_id, UpdatePhase.ABORTED)
    record = _record(store, update_id, repo=layout.root)
    return TrialRequest(layout=layout, record=record, store=store, candidate=candidate, previous="version-old",
                        live_state=state, launcher=launcher, browser_python=Path(sys.executable))


def _fingerprint(state: Path) -> dict[str, tuple[int, int]]:
    skipped = {"runtime", "self-updates"}
    return {
        str(path.relative_to(state)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in state.rglob("*")
        if path.is_file() and path.relative_to(state).parts[0] not in skipped
    }


def test_a_good_candidate_passes_and_nothing_live_changes(built):
    _root, state, layout, _launcher = built
    before = _fingerprint(state)
    request = _request(built, "su_trial_good")
    result = run_trial(request)
    assert result.passed, (result.failed_check, result.reason, (result.evidence / "candidate.log").read_text()[-3000:])
    assert result.isolation == "sandbox"
    assert result.checks == ["data_copy", "baseline", "cold_start", "system_probe", "browser_probe", "full_turn",
                             "tests", "desktop_smoke", "schema_guard", "updater_self_test", "rollback_rehearsal",
                             "verifier"]
    assert _fingerprint(state) == before
    assert not (layout.trials / "su_trial_good").exists()
    log = (result.evidence / "candidate.log").read_text()
    assert "trial: background services" in log
    for line in ("scheduler: running", "always-on Agents: running", "subscription catalog refresh running"):
        assert line not in log
    assert SECRET not in "".join(path.read_text(errors="replace") for path in result.evidence.iterdir())
    turn = json.loads((result.evidence / "06-full_turn.json").read_text())
    assert turn["status"] == "passed" and turn["tool"]
    rehearsed = json.loads((result.evidence / "11-rollback_rehearsal.json").read_text())
    assert rehearsed["status"] == "passed" and rehearsed["messages"] >= 2


def test_a_candidate_that_will_not_start_fails_and_keeps_only_logs(built):
    _root, state, layout, _launcher = built

    def broken(site: Path) -> None:
        path = site / "openprogram" / "operations" / "worker" / "runner.py"
        path.write_text('raise RuntimeError("this candidate cannot start")\n', encoding="utf-8")

    build_version(layout, "version-broken", NEW_COMMIT, patch=broken)
    before = _fingerprint(state)
    request = _request(built, "su_trial_broken", candidate="version-broken")
    result = run_trial(request)
    assert not result.passed and result.failed_check == "cold_start"
    assert "exited during start" in result.reason
    assert _fingerprint(state) == before
    left = {path.name for path in (layout.trials / "su_trial_broken").iterdir()}
    assert "home" not in left and "fake-root" not in left and "tmp" not in left
    assert "cannot start" in (result.evidence / "candidate.log").read_text()
