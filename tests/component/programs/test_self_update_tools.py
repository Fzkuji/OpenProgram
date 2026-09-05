from __future__ import annotations

from dataclasses import replace
import asyncio
import json
from pathlib import Path
import subprocess

import pytest

from openprogram.agent.dispatcher import TurnRequest
from openprogram.self_update import SelfUpdateStore, UpdatePhase
from openprogram.worktree.types import Worktree, WorktreeStatus

from openprogram.programs.tools.system.self_update import (
    SelfUpdateToolError,
    _cancel_update,
    _prepare_update,
    _status_update,
)
from openprogram.programs.tools.system import self_update as self_update_module


@pytest.fixture(autouse=True)
def _isolated_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openprogram import paths
    from openprogram.agent import authority

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "profile")
    authority._reset_owner_cache_for_tests()
    monkeypatch.setattr(self_update_module, "_launch_supervisor", lambda _update_id: None)
    from types import SimpleNamespace
    monkeypatch.setattr("openprogram.agent.internals._model_tools.load_agent_profile", lambda _: {"id": "main"})
    monkeypatch.setattr("openprogram.agent.internals._model_tools.resolve_model", lambda *a: SimpleNamespace(provider="fake", id="fixed"))


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def _candidate(tmp_path: Path) -> tuple[Worktree, str, str]:
    source = tmp_path / "source"
    worktree = tmp_path / "candidate"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "tests@example.com")
    _git(source, "config", "user.name", "Tests")
    (source / "pyproject.toml").write_text(
        "[project]\nname = \"openprogram\"\nversion = \"0.1.0\"\n",
        encoding="utf-8",
    )
    (source / "base.txt").write_text("base\n", encoding="utf-8")
    _git(source, "add", "base.txt", "pyproject.toml")
    _git(source, "commit", "-m", "base")
    base_sha = _git(source, "rev-parse", "HEAD")
    _git(source, "worktree", "add", "-b", "candidate", str(worktree), "HEAD")
    (worktree / "feature.txt").write_text("candidate\n", encoding="utf-8")
    _git(worktree, "add", "feature.txt")
    _git(worktree, "commit", "-m", "candidate")
    candidate_sha = _git(worktree, "rev-parse", "HEAD")
    return (
        Worktree(
            id="wt_candidate",
            source_repo=str(source),
            worktree_path=str(worktree),
            branch_name="candidate",
            base_ref=base_sha,
            status=WorktreeStatus.ACTIVE,
            parent_session="session-1",
        ),
        base_sha,
        candidate_sha,
    )


class _Manager:
    def __init__(self, worktree: Worktree | None) -> None:
        self.worktree = worktree

    def get_worktree(self, worktree_id: str) -> Worktree | None:
        return self.worktree if self.worktree and self.worktree.id == worktree_id else None


def _request(source: str = "web", **overrides: object) -> TurnRequest:
    from openprogram.agent.authority import local_owner_authority

    values = {
        "session_id": "session-1",
        "user_text": "update OpenProgram",
        "agent_id": "main",
        "source": source,
        **local_owner_authority(),
        **overrides,
    }
    return TurnRequest(**values)


def _prepare(
    worktree: Worktree,
    candidate_sha: str,
    store: SelfUpdateStore,
    *,
    req: TurnRequest | None = None,
):
    return _prepare_update(
        worktree_id=worktree.id,
        candidate_sha=candidate_sha,
        goal="Add self-update chat handoff",
        assertions=["The request is durable"],
        iteration_policy=None,
        req=req or _request(),
        assistant_id="turn-1_reply",
        manager=_Manager(worktree),
        store=store,
    )


def test_public_status_does_not_create_an_absent_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SelfUpdateStore(tmp_path / "absent")
    request = _request()
    monkeypatch.setattr(self_update_module, "_turn_context", lambda: (request, "turn-1_reply"))
    monkeypatch.setattr(self_update_module, "SelfUpdateStore", lambda: store)

    result = asyncio.run(self_update_module.self_update_status.execute(
        "status-empty", {}, None, None,
    ))

    assert result.is_error
    assert not store.root.exists(), "A status query must not create the update directory or lock"


def test_public_status_projects_verified_runtime_not_unbound_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time
    from openprogram.self_update.recovery import SYSTEM_CHECKS

    worktree, _base_sha, candidate_sha = _candidate(tmp_path)
    store = SelfUpdateStore(tmp_path / "state")
    prepared = _prepare(worktree, candidate_sha, store)
    update_id = prepared["update_id"]
    for phase in (UpdatePhase.STAGING, UpdatePhase.READY, UpdatePhase.ACTIVATING):
        store.transition(update_id, phase)
    gate = dict(schema=1, candidate_sha=candidate_sha, attempt=1,
                worker_pid=12345, verified_at=time.time(),
                checks={name: True for name in SYSTEM_CHECKS})
    state = store.transition(update_id, UpdatePhase.VERIFYING, detail={
        "system_gate": gate, "current_revision": "3" * 40,
    })
    request = _request()
    monkeypatch.setattr(self_update_module, "_turn_context", lambda: (request, "turn-1_reply"))
    monkeypatch.setattr(self_update_module, "SelfUpdateStore", lambda: store)

    result = asyncio.run(self_update_module.self_update_status.execute(
        "status-verifying", {"update_id": update_id}, None, None,
    ))
    assert not result.is_error, result.content
    status = json.loads(result.content[0].text)

    assert status["candidate_revision"] == candidate_sha
    assert status["last_verified_runtime"]["candidate_sha"] == candidate_sha
    assert status["last_verified_runtime"]["verified_at"] == gate["verified_at"]
    assert status["state_revision"] == state.revision
    assert status["target_app"] == "/Applications/OpenProgram.app"


@pytest.mark.parametrize("mode", [0o600, 0o644])
def test_public_status_requires_private_verifier_config(tmp_path, monkeypatch, mode):
    worktree, _, candidate_sha = _candidate(tmp_path)
    store = SelfUpdateStore(tmp_path / "state")
    update_id = _prepare(worktree, candidate_sha, store)["update_id"]
    request = _request()
    monkeypatch.setattr(self_update_module, "_turn_context", lambda: (request, "turn-1_reply"))
    monkeypatch.setattr(self_update_module, "SelfUpdateStore", lambda: store)
    path = store.root / update_id / "verifier-config.json"
    path.chmod(mode)
    before = path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode

    result = asyncio.run(self_update_module.self_update_status.execute(
        "status-private-config", {"update_id": update_id}, None, None,
    ))

    assert result.is_error == bool(mode & 0o077)
    if not result.is_error:
        assert json.loads(result.content[0].text)["update_id"] == update_id
    else:
        assert str(store.root) not in str(result.content)
    assert (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode) == before


def test_prepare_pins_git_derived_candidate_and_never_executes_it(tmp_path: Path) -> None:
    worktree, base_sha, candidate_sha = _candidate(tmp_path)
    marker = Path(worktree.worktree_path) / "executed"
    (Path(worktree.worktree_path) / "setup.py").write_text(
        "from pathlib import Path\nPath('executed').write_text('bad')\n",
        encoding="utf-8",
    )
    _git(Path(worktree.worktree_path), "add", "setup.py")
    _git(Path(worktree.worktree_path), "commit", "-m", "metadata trap")
    candidate_sha = _git(Path(worktree.worktree_path), "rev-parse", "HEAD")
    store = SelfUpdateStore(tmp_path / "state")

    result = _prepare(worktree, candidate_sha, store)
    record = store.load(result["update_id"])

    assert result["phase"] == UpdatePhase.PREPARING.value
    assert record.request.base_sha == base_sha
    assert record.request.candidate_sha == candidate_sha
    assert record.request.changed_paths == ("feature.txt", "setup.py")
    assert record.request.origin_turn_id == "turn-1_reply".removesuffix("_reply")
    assert marker.exists() is False


@pytest.mark.parametrize("mutation", ["dirty", "wrong_sha"])
def test_prepare_rejects_dirty_or_non_head_candidate(
    tmp_path: Path, mutation: str
) -> None:
    worktree, base_sha, candidate_sha = _candidate(tmp_path)
    if mutation == "dirty":
        (Path(worktree.worktree_path) / "dirty.txt").write_text("dirty\n")
    else:
        candidate_sha = base_sha

    with pytest.raises(SelfUpdateToolError):
        _prepare(worktree, candidate_sha, SelfUpdateStore(tmp_path / "state"))
    assert (tmp_path / "state" / "active.json").exists() is False


@pytest.mark.parametrize(
    ("source", "overrides"),
    [
        ("cron", {"interaction": "non-interactive"}),
        ("scheduler", {"interaction": "non-interactive"}),
        ("agent_spawn", {"interaction": "non-interactive", "speaker_kind": "runtime"}),
        ("mcp", {"interaction": "non-interactive", "speaker_kind": "client"}),
        ("web", {"authority_tier": "paired", "speaker_kind": "human"}),
        ("web", {"principal_id": "owner/install/ffffffffffffffff"}),
    ],
)
def test_prepare_body_rejects_non_owner_authority(
    tmp_path: Path, source: str, overrides: dict[str, object]
) -> None:
    worktree, _base_sha, candidate_sha = _candidate(tmp_path)
    req = _request(source, **overrides)

    with pytest.raises(SelfUpdateToolError, match="interactive local owner"):
        _prepare(
            worktree,
            candidate_sha,
            SelfUpdateStore(tmp_path / "state"),
            req=req,
        )


def test_prepare_rejects_foreign_current_or_inactive_worktree(tmp_path: Path) -> None:
    worktree, _base_sha, candidate_sha = _candidate(tmp_path)
    store = SelfUpdateStore(tmp_path / "state")

    for invalid in (
        replace(worktree, parent_session="other"),
        replace(worktree, status=WorktreeStatus.KEPT),
        replace(worktree, worktree_path=worktree.source_repo),
    ):
        with pytest.raises(SelfUpdateToolError):
            _prepare(invalid, candidate_sha, store)


def test_prepare_rejects_worktree_from_another_git_common_directory(
    tmp_path: Path,
) -> None:
    worktree, _base_sha, candidate_sha = _candidate(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init")
    _git(other, "config", "user.email", "tests@example.com")
    _git(other, "config", "user.name", "Tests")
    (other / "pyproject.toml").write_text(
        "[project]\nname = \"openprogram\"\nversion = \"0.1.0\"\n",
        encoding="utf-8",
    )
    (other / "base.txt").write_text("base\n", encoding="utf-8")
    _git(other, "add", ".")
    _git(other, "commit", "-m", "base")
    forged = replace(worktree, source_repo=str(other))

    with pytest.raises(SelfUpdateToolError, match="linked to its source"):
        _prepare(forged, candidate_sha, SelfUpdateStore(tmp_path / "state"))


def test_prepare_rejects_symlinked_registered_worktree(tmp_path: Path) -> None:
    worktree, _base_sha, candidate_sha = _candidate(tmp_path)
    link = tmp_path / "candidate-link"
    link.symlink_to(worktree.worktree_path, target_is_directory=True)
    forged = replace(worktree, worktree_path=str(link))

    with pytest.raises(SelfUpdateToolError, match="must not contain symlinks"):
        _prepare(forged, candidate_sha, SelfUpdateStore(tmp_path / "state"))


def test_prepare_disables_repository_fsmonitor_hook(tmp_path: Path) -> None:
    worktree, _base_sha, candidate_sha = _candidate(tmp_path)
    candidate = Path(worktree.worktree_path)
    marker = tmp_path / "fsmonitor-executed"
    hook = candidate / "fsmonitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    hook.chmod(0o755)
    _git(candidate, "add", "fsmonitor.sh")
    _git(candidate, "commit", "-m", "add fsmonitor probe")
    candidate_sha = _git(candidate, "rev-parse", "HEAD")
    _git(candidate, "config", "core.fsmonitor", str(hook))

    _prepare(worktree, candidate_sha, SelfUpdateStore(tmp_path / "state"))

    assert marker.exists() is False


def test_prepare_aborts_if_head_changes_during_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree, _base_sha, candidate_sha = _candidate(tmp_path)
    candidate = Path(worktree.worktree_path)
    store = SelfUpdateStore(tmp_path / "state")
    original_git = self_update_module._git
    head_reads = 0

    def racing_git(cwd: Path, *args: str) -> str:
        nonlocal head_reads
        value = original_git(cwd, *args)
        if cwd == candidate and args == ("rev-parse", "HEAD"):
            head_reads += 1
            if head_reads == 1:
                (candidate / "later.txt").write_text("later\n", encoding="utf-8")
                _git(candidate, "add", "later.txt")
                _git(candidate, "commit", "-m", "concurrent change")
        return value

    monkeypatch.setattr(self_update_module, "_git", racing_git)

    with pytest.raises(SelfUpdateToolError, match="candidate"):
        _prepare(worktree, candidate_sha, store)

    assert store.load_active() is None
    records = [path.name for path in (tmp_path / "state").glob("su_*")]
    assert len(records) == 1
    assert store.load(records[0]).state.phase is UpdatePhase.ABORTED


@pytest.mark.parametrize(
    ("goal", "assertions"),
    [("", ["one"]), ("goal", []), ("goal", "not-a-list")],
)
def test_prepare_rejects_malformed_acceptance_contract(
    tmp_path: Path, goal: str, assertions: object
) -> None:
    worktree, _base_sha, candidate_sha = _candidate(tmp_path)

    with pytest.raises(SelfUpdateToolError):
        _prepare_update(
            worktree_id=worktree.id,
            candidate_sha=candidate_sha,
            goal=goal,
            assertions=assertions,  # type: ignore[arg-type]
            iteration_policy=None,
            req=_request(),
            assistant_id="turn-1_reply",
            manager=_Manager(worktree),
            store=SelfUpdateStore(tmp_path / "state"),
        )


def test_prepare_atomically_rejects_second_active_update(tmp_path: Path) -> None:
    worktree, _base_sha, candidate_sha = _candidate(tmp_path)
    store = SelfUpdateStore(tmp_path / "state")
    _prepare(worktree, candidate_sha, store)

    with pytest.raises(SelfUpdateToolError, match="active update"):
        _prepare(worktree, candidate_sha, store)


def test_prepare_aborts_request_when_supervisor_submission_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree, _base_sha, candidate_sha = _candidate(tmp_path)
    store = SelfUpdateStore(tmp_path / "state")
    monkeypatch.setattr(
        self_update_module,
        "_launch_supervisor",
        lambda _update_id: (_ for _ in ()).throw(RuntimeError("launch denied")),
    )

    with pytest.raises(SelfUpdateToolError, match="supervisor launch failed"):
        _prepare(worktree, candidate_sha, store)

    assert store.load_active() is None
    update_dirs = list((tmp_path / "state").glob("su_*"))
    assert len(update_dirs) == 1
    assert store.load(update_dirs[0].name).state.phase is UpdatePhase.ABORTED


def test_status_and_cancel_are_scoped_to_origin_session(tmp_path: Path) -> None:
    worktree, _base_sha, candidate_sha = _candidate(tmp_path)
    store = SelfUpdateStore(tmp_path / "state")
    prepared = _prepare(worktree, candidate_sha, store)

    status = _status_update(
        update_id=prepared["update_id"], req=_request(), store=store
    )
    assert status["phase"] == "preparing"
    assert status["candidate_revision"] == candidate_sha
    assert status["target_app"] == "/Applications/OpenProgram.app"
    assert status["rollback_available"] is False
    assert status["verifier_verdict"] is None

    foreign = _request(session_id="other")
    with pytest.raises(SelfUpdateToolError, match="origin session"):
        _status_update(update_id=prepared["update_id"], req=foreign, store=store)
    with pytest.raises(SelfUpdateToolError, match="origin session"):
        _cancel_update(
            update_id=prepared["update_id"], reason="stop", req=foreign, store=store
        )

    cancelled = _cancel_update(
        update_id=prepared["update_id"], reason="owner requested", req=_request(), store=store
    )
    assert cancelled["phase"] == "aborted"
    assert store.load(prepared["update_id"]).state.phase is UpdatePhase.ABORTED


def test_prepare_publishes_bound_verifier_config_before_launch(tmp_path, monkeypatch):
    from openprogram.self_update.verifier_config import load_verifier_config, config_evidence
    worktree, _, candidate_sha = _candidate(tmp_path)
    store = SelfUpdateStore(tmp_path / "state")
    seen = []
    def launch(update_id):
        record = store.load(update_id)
        config = load_verifier_config(store, record)
        assert config_evidence(config) in record.request.pre_update_evidence
        assert config["model_override"] == "fake/fixed"
        assert config["tools_override"] == ["read", "glob", "grep", "list", "self_update_observe"]
        seen.append(update_id)
    monkeypatch.setattr(self_update_module, "_launch_supervisor", launch)
    result = _prepare(worktree, candidate_sha, store)
    assert seen == [result["update_id"]]


def test_snapshot_write_failure_does_not_publish_partial_update(tmp_path, monkeypatch):
    worktree, _, candidate_sha = _candidate(tmp_path)
    store = SelfUpdateStore(tmp_path / "state")
    original_write = store._write_json
    def fail_config(path, value):
        if path.name == "verifier-config.json":
            raise OSError("disk write failed")
        return original_write(path, value)
    monkeypatch.setattr(store, "_write_json", fail_config)
    monkeypatch.setattr(self_update_module, "_launch_supervisor", lambda _: pytest.fail("partial update launched"))
    with pytest.raises(OSError, match="disk write failed"):
        _prepare(worktree, candidate_sha, store)
    assert store.load_active() is None
    assert not list(store.root.glob("su_*"))
