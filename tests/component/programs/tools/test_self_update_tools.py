from __future__ import annotations

from dataclasses import replace
import asyncio
import json
import os
from pathlib import Path
import subprocess

import pytest

from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.operations.self_update import SelfUpdateStore, UpdatePhase
from openprogram.state.worktree.types import Worktree, WorktreeStatus

from openprogram.programs.tools.system.self_update import (
    SelfUpdateToolError,
    _cancel_iteration,
    _prepare_update,
    _status_update,
)
from openprogram.programs.tools.system import self_update as self_update_module


@pytest.fixture(autouse=True)
def _isolated_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openprogram.state import paths
    from openprogram.runtime.permissions import authority

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "profile")
    authority._reset_owner_cache_for_tests()
    monkeypatch.setattr(self_update_module, "_launch_supervisor", lambda *_: None)
    from openprogram.operations.self_update.control import backend
    monkeypatch.setattr(backend, "packaged_runtime", lambda: True)
    from types import SimpleNamespace
    monkeypatch.setattr("openprogram.runtime.conversation.model_tools.load_agent_profile", lambda _: {"id": "main"})
    monkeypatch.setattr("openprogram.runtime.conversation.model_tools.resolve_model", lambda *a: SimpleNamespace(provider="fake", id="fixed"))


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
    # The verifier intentionally ignores user Git configuration. Keep fixture
    # checkout normalization identical with and without the host's autocrlf.
    _git(source, "config", "core.autocrlf", "false")
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
    from openprogram.runtime.permissions.authority import local_owner_authority

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

    result = asyncio.run(self_update_module.self_update.execute("status-empty", {"action": "status"}, None, None,
    ))

    assert result.is_error
    assert not store.root.exists(), "A status query must not create the update directory or lock"


def test_public_status_projects_verified_runtime_not_unbound_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time
    from openprogram.operations.self_update.control.recovery import SYSTEM_CHECKS

    worktree, _base_sha, candidate_sha = _candidate(tmp_path)
    store = SelfUpdateStore(tmp_path / "state")
    prepared = _prepare(worktree, candidate_sha, store)
    update_id = prepared["update_id"]
    for phase in (UpdatePhase.BUILDING, UpdatePhase.READY, UpdatePhase.ACTIVATING):
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

    result = asyncio.run(self_update_module.self_update.execute("status-verifying", {"action": "status", "update_id": update_id}, None, None,
    ))
    assert not result.is_error, result.content
    status = json.loads(result.content[0].text)

    assert status["candidate_revision"] == candidate_sha
    assert status["last_verified_runtime"]["candidate_sha"] == candidate_sha
    assert status["last_verified_runtime"]["verified_at"] == gate["verified_at"]
    assert status["state_revision"] == state.revision
    assert status["target_app"] == "/Applications/OpenProgram.app"
    assert status["progress"] == "Checking that the new version works on your real setup."
    assert list(status)[0] == "progress"


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

    result = asyncio.run(self_update_module.self_update.execute("status-private-config", {"action": "status", "update_id": update_id}, None, None,
    ))

    # Windows keeps inherited ACLs; POSIX mode bits are not access authority.
    assert result.is_error == (os.name != "nt" and bool(mode & 0o077))
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
    "overrides",
    [
        {"authority_tier": "paired", "speaker_kind": "human"},
        {"principal_id": "owner/install/ffffffffffffffff"},
    ],
)
def test_prepare_body_rejects_anyone_but_the_owner(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    from openprogram.runtime.permissions.refusal import Refused

    worktree, _base_sha, candidate_sha = _candidate(tmp_path)
    req = _request("web", **overrides)

    with pytest.raises(Refused, match="the owner"):
        _prepare(
            worktree,
            candidate_sha,
            SelfUpdateStore(tmp_path / "state"),
            req=req,
        )


@pytest.mark.parametrize(
    ("source", "overrides"),
    [
        ("cron", {"interaction": "non-interactive"}),
        ("scheduler", {"interaction": "non-interactive"}),
        ("agent_spawn", {"interaction": "non-interactive", "speaker_kind": "runtime"}),
        ("mcp", {"interaction": "non-interactive", "speaker_kind": "client"}),
    ],
)
def test_prepare_body_accepts_the_owner_with_nobody_attending(
    tmp_path: Path, source: str, overrides: dict[str, object]
) -> None:
    worktree, _base_sha, candidate_sha = _candidate(tmp_path)
    store = SelfUpdateStore(tmp_path / "state")
    prepared = _prepare(worktree, candidate_sha, store, req=_request(source, **overrides))
    assert store.load(prepared["update_id"]).state.phase is UpdatePhase.PREPARING


def test_prepare_rejects_foreign_current_or_inactive_worktree(tmp_path: Path) -> None:
    worktree, _base_sha, candidate_sha = _candidate(tmp_path)
    store = SelfUpdateStore(tmp_path / "state")

    for invalid in (
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


def test_status_and_cancel_work_from_any_conversation_of_the_owner(tmp_path: Path) -> None:
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
    assert _status_update(update_id=prepared["update_id"], req=foreign, store=store)["phase"] == "preparing"

    cancelled = _cancel_iteration(update_id=prepared["update_id"], req=foreign, store=store)
    assert cancelled == {"root_id": prepared["update_id"], "status": "stopped"}
    assert store.load(prepared["update_id"]).state.phase is UpdatePhase.ABORTED


def test_prepare_publishes_bound_verifier_config_before_launch(tmp_path, monkeypatch):
    from openprogram.operations.self_update.verification.verifier_config import load_verifier_config
    from openprogram.operations.self_update.verification.verifier_config import config_evidence
    worktree, _, candidate_sha = _candidate(tmp_path)
    store = SelfUpdateStore(tmp_path / "state")
    seen = []
    def launch(_name, _store, update_id, _worktree):
        record = store.load(update_id)
        config = load_verifier_config(store, record)
        assert config_evidence(config) in record.request.pre_update_evidence
        assert config["model_override"] == "fake/fixed"
        assert config["tools_override"] == ["read", "glob", "grep", "list", "self_update_observe"]
        seen.append(update_id)
    monkeypatch.setattr(self_update_module, "_launch_supervisor", launch)
    result = _prepare(worktree, candidate_sha, store)
    assert seen == [result["update_id"]]


def test_a_config_frozen_with_another_fallback_still_loads(tmp_path, monkeypatch):
    from openprogram.operations.self_update.verification import verifier_config
    worktree, _, candidate_sha = _candidate(tmp_path)
    store = SelfUpdateStore(tmp_path / "state")
    monkeypatch.setattr(self_update_module, "_launch_supervisor", lambda *_: None)
    current = verifier_config._response_format
    monkeypatch.setattr(verifier_config, "_response_format",
                        lambda request, attempt: {**current(request, attempt), "fallback": "auto"})
    result = _prepare(worktree, candidate_sha, store)
    monkeypatch.setattr(verifier_config, "_response_format", current)

    config = verifier_config.load_verifier_config(store, store.load(result["update_id"]))
    assert config["response_format"]["fallback"] == "auto"


def test_a_config_frozen_without_the_evidence_pattern_still_loads(tmp_path, monkeypatch):
    from copy import deepcopy
    from openprogram.operations.self_update.verification import verifier_config
    worktree, _, candidate_sha = _candidate(tmp_path)
    store = SelfUpdateStore(tmp_path / "state")
    monkeypatch.setattr(self_update_module, "_launch_supervisor", lambda *_: None)
    current = verifier_config._response_format
    def unpatterned(request, attempt):
        frozen = deepcopy(current(request, attempt))
        assertion = frozen["schema"]["properties"]["assertions"]["items"]
        assertion["properties"]["evidence_refs"]["items"] = {"type": "string", "minLength": 1}
        return frozen
    monkeypatch.setattr(verifier_config, "_response_format", unpatterned)
    result = _prepare(worktree, candidate_sha, store)
    monkeypatch.setattr(verifier_config, "_response_format", current)

    verifier_config.load_verifier_config(store, store.load(result["update_id"]))


def test_a_verdict_citing_a_file_instead_of_an_observation_fails_the_schema(tmp_path):
    from openprogram.models.providers.structured_output import normalize_response_format, parse_and_validate_json
    from openprogram.operations.self_update.verification import verifier_config
    worktree, _, candidate_sha = _candidate(tmp_path)
    store = SelfUpdateStore(tmp_path / "state")
    request = store.load(_prepare(worktree, candidate_sha, store)["update_id"]).request
    output = normalize_response_format(verifier_config._response_format(request, 1))
    observed = "observation:" + "a" * 32 + ":" + "b" * 64
    def verdict(*refs):
        return json.dumps(dict(schema=1, update_id=request.update_id, candidate_sha=candidate_sha, attempt=1,
                               verdict="fail", assertions=[dict(id="acceptance-1", status="fail", entry="test:python",
                               observation="The marker is missing", evidence_refs=list(refs), observed_at=1.0)]))

    parse_and_validate_json(verdict(observed), output)
    with pytest.raises(Exception):
        parse_and_validate_json(verdict(observed, "/Applications/OpenProgram.app/page.html:80"), output)


def test_snapshot_write_failure_does_not_publish_partial_update(tmp_path, monkeypatch):
    worktree, _, candidate_sha = _candidate(tmp_path)
    store = SelfUpdateStore(tmp_path / "state")
    original_write = store._write_json
    def fail_config(path, value):
        if path.name == "verifier-config.json":
            raise OSError("disk write failed")
        return original_write(path, value)
    monkeypatch.setattr(store, "_write_json", fail_config)
    monkeypatch.setattr(self_update_module, "_launch_supervisor", lambda *_: pytest.fail("partial update launched"))
    with pytest.raises(OSError, match="disk write failed"):
        _prepare(worktree, candidate_sha, store)
    assert store.load_active() is None
    assert not list(store.root.glob("su_*"))


def _with_stable_branch(worktree, base_sha):
    source = Path(worktree.source_repo)
    _git(source, "branch", "stable", base_sha)
    _git(source, "branch", "-f", "dev", base_sha)


def test_with_a_stable_branch_prepare_saves_a_request_for_the_detached_controller(tmp_path, monkeypatch):
    worktree, base_sha, candidate_sha = _candidate(tmp_path)
    _with_stable_branch(worktree, base_sha)
    launched = []
    monkeypatch.setattr(self_update_module, "_launch_supervisor",
                        lambda name, store, update_id, candidate: launched.append((name, update_id, candidate)))
    store = SelfUpdateStore(tmp_path / "state")

    result = _prepare(worktree, candidate_sha, store)

    assert result["backend"] == "dev_stable" and result["phase"] == "preparing"
    [(name, update_id, candidate)] = launched
    assert name == "dev_stable" and update_id == result["update_id"]
    assert candidate == Path(worktree.worktree_path).resolve()
    record = store.load(update_id)
    assert "backend:dev_stable" in record.request.pre_update_evidence
    assert record.request.timeout_seconds == 2 * 3600


def test_with_a_stable_branch_a_candidate_from_an_older_commit_is_accepted(tmp_path):
    worktree, base_sha, candidate_sha = _candidate(tmp_path)
    source = Path(worktree.source_repo)
    _git(source, "branch", "stable", base_sha)
    (source / "elsewhere.txt").write_text("moved on\n")
    _git(source, "add", "elsewhere.txt")
    _git(source, "commit", "-q", "-m", "dev moved")
    _git(source, "branch", "-f", "dev", "HEAD")
    store = SelfUpdateStore(tmp_path / "state")

    result = _prepare(worktree, candidate_sha, store)

    assert result["backend"] == "dev_stable"
    # Only the candidate's own paths, not what dev gained since it forked.
    assert "elsewhere.txt" not in result["changed_paths"]
    assert store.load(result["update_id"]).request.base_sha == _git(source, "rev-parse", "dev")


def _undo(store: SelfUpdateStore, target: str, manager: object) -> dict:
    return self_update_module._undo_update(
        target=target, req=_request(), assistant_id="assistant-1", manager=manager, store=store,
    )


def test_with_a_stable_branch_undo_reverts_on_a_worktree_of_dev_and_lands_it(tmp_path, monkeypatch):
    worktree, base_sha, candidate_sha = _candidate(tmp_path)
    _with_stable_branch(worktree, base_sha)
    source = Path(worktree.source_repo)
    _git(source, "merge", "-q", "--ff-only", candidate_sha)
    _git(source, "branch", "-f", "dev", "HEAD")
    monkeypatch.setenv("OPENPROGRAM_SOURCE_REPO", str(source))
    created: list[Worktree] = []

    class Manager(_Manager):
        def create_worktree(self, repository: str, *, base_ref: str, label: str, parent_session: str) -> Worktree:
            path = tmp_path / label
            _git(Path(repository), "worktree", "add", "-q", "-b", label, str(path), base_ref)
            self.worktree = Worktree(
                id="wt_undo", source_repo=repository, worktree_path=str(path), branch_name=label,
                base_ref=base_ref, status=WorktreeStatus.ACTIVE, parent_session=parent_session,
            )
            created.append(self.worktree)
            return self.worktree

    store = SelfUpdateStore(tmp_path / "state")
    result = _undo(store, candidate_sha[:12], Manager(None))

    assert result["backend"] == "dev_stable" and result["changed_paths"] == ["feature.txt"]
    record = store.load(result["update_id"])
    assert record.request.goal == f"Undo commit {candidate_sha[:12]}: candidate"
    reverted = Path(created[0].worktree_path)
    assert not (reverted / "feature.txt").exists()
    assert _git(reverted, "rev-parse", "HEAD") == record.request.candidate_sha != candidate_sha


def test_undo_of_a_commit_dev_does_not_have_changes_nothing(tmp_path, monkeypatch):
    worktree, base_sha, candidate_sha = _candidate(tmp_path)
    _with_stable_branch(worktree, base_sha)
    monkeypatch.setenv("OPENPROGRAM_SOURCE_REPO", worktree.source_repo)
    with pytest.raises(self_update_module.SelfUpdateToolError, match="not part of the code"):
        _undo(SelfUpdateStore(tmp_path / "state"), candidate_sha, _Manager(None))


def test_a_conflicting_undo_on_dev_stops_and_discards_its_worktree(tmp_path, monkeypatch):
    worktree, base_sha, candidate_sha = _candidate(tmp_path)
    _with_stable_branch(worktree, base_sha)
    source = Path(worktree.source_repo)
    _git(source, "merge", "-q", "--ff-only", candidate_sha)
    (source / "feature.txt").write_text("later edit\n", encoding="utf-8")
    _git(source, "add", "feature.txt")
    _git(source, "commit", "-q", "-m", "later")
    _git(source, "branch", "-f", "dev", "HEAD")
    monkeypatch.setenv("OPENPROGRAM_SOURCE_REPO", str(source))
    discarded: list[str] = []

    class Manager(_Manager):
        def create_worktree(self, repository: str, *, base_ref: str, label: str, parent_session: str) -> Worktree:
            path = tmp_path / label
            _git(Path(repository), "worktree", "add", "-q", "-b", label, str(path), base_ref)
            return Worktree(id="wt_undo", source_repo=repository, worktree_path=str(path), branch_name=label,
                            base_ref=base_ref, status=WorktreeStatus.ACTIVE, parent_session=parent_session)

        def discard_worktree(self, worktree_id: str, *, force: bool) -> None:
            discarded.append(worktree_id)

    with pytest.raises(self_update_module.SelfUpdateToolError, match="feature.txt"):
        _undo(SelfUpdateStore(tmp_path / "state"), candidate_sha, Manager(None))
    assert discarded == ["wt_undo"]


def test_undo_in_a_developer_checkout_activates_nothing(tmp_path, monkeypatch):
    from openprogram.operations.self_update.control import backend

    monkeypatch.setattr(backend, "select", lambda *_: backend.CHECKOUT)
    assert _undo(SelfUpdateStore(tmp_path / "state"), "abc1234", _Manager(None))["activated"] is False


def test_undo_in_a_packaged_app_says_it_has_nothing_to_go_back_to(tmp_path, monkeypatch):
    from openprogram.operations.self_update.control import backend

    monkeypatch.setattr(backend, "select", lambda *_: backend.LEGACY_APP)
    with pytest.raises(self_update_module.SelfUpdateToolError, match="keeps no earlier versions"):
        _undo(SelfUpdateStore(tmp_path / "state"), "abc1234", _Manager(None))


def test_undo_needs_a_target(tmp_path):
    with pytest.raises(self_update_module.SelfUpdateToolError, match="target is required"):
        _undo(SelfUpdateStore(tmp_path / "state"), "  ", _Manager(None))


def test_undo_under_the_version_store_resolves_the_target_before_handing_off(tmp_path, monkeypatch):
    from openprogram.operations.self_update.control import backend, undo

    monkeypatch.setattr(backend, "select", lambda *_: backend.VERSIONS)
    monkeypatch.setattr(backend, "require_host", lambda _name: None)
    seen: list[object] = []
    plan = undo.UndoPlan(undo.UNDO_TO, "v1", "a" * 40, "b" * 40, "subject", version="v1")
    monkeypatch.setattr(undo, "plan", lambda layout, target: seen.append((layout.root, target)) or plan)
    monkeypatch.setattr(self_update_module, "_submit_version_undo", lambda value, **_: {"plan": value})

    assert _undo(SelfUpdateStore(tmp_path / "state"), " v1 ", _Manager(None)) == {"plan": plan}
    assert seen == [(tmp_path / "profile" / "runtime", "v1")]


def test_a_controller_that_cannot_start_aborts_the_saved_request(tmp_path, monkeypatch):
    worktree, base_sha, candidate_sha = _candidate(tmp_path)
    _with_stable_branch(worktree, base_sha)

    def fail(*_):
        raise RuntimeError("no runtime to copy")

    monkeypatch.setattr(self_update_module, "_launch_supervisor", fail)
    store = SelfUpdateStore(tmp_path / "state")
    with pytest.raises(SelfUpdateToolError, match="no runtime to copy"):
        _prepare(worktree, candidate_sha, store)
    assert store.load_active() is None


def test_a_developer_checkout_activates_nothing(tmp_path, monkeypatch):
    from openprogram.operations.self_update.control import backend

    worktree, _, candidate_sha = _candidate(tmp_path)
    monkeypatch.setattr(backend, "packaged_runtime", lambda: False)
    store = SelfUpdateStore(tmp_path / "state")

    result = _prepare(worktree, candidate_sha, store)

    assert result["backend"] == "checkout" and result["activated"] is False
    assert store.load_active() is None
