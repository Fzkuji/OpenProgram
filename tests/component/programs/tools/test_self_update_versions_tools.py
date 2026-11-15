"""``self_update`` on an install that runs from sealed versions."""
from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from openprogram.operations.self_update import SelfUpdateStore, UpdatePhase
from openprogram.operations.self_update.control import backend
from openprogram.operations.self_update.versions import layout as paths
from openprogram.programs.tools.system import self_update as tool
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.state.worktree.context import clear_worktree
from openprogram.state.worktree.manager import _reset_manager_for_tests, get_manager


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True).stdout.strip()


@pytest.fixture
def installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from openprogram.runtime.permissions import authority
    from openprogram.state import paths as state_paths

    state = tmp_path / "state"
    monkeypatch.setattr(state_paths, "get_state_dir", lambda: state)
    authority._reset_owner_cache_for_tests()
    _reset_manager_for_tests()
    layout = paths.Layout(state / "runtime")
    layout.repo.mkdir(parents=True)
    _git(layout.repo, "init", "-b", "local")
    _git(layout.repo, "config", "user.email", "tests@example.com")
    _git(layout.repo, "config", "user.name", "Tests")
    (layout.repo / "base.txt").write_text("base\n")
    _git(layout.repo, "add", "base.txt")
    _git(layout.repo, "commit", "-m", "base")
    monkeypatch.setattr(paths, "current_runtime", lambda *_: paths.CurrentRuntime(layout, "running-version"))
    monkeypatch.setattr(backend, "select", lambda *_: backend.VERSIONS)
    launched: list[str] = []
    monkeypatch.setattr(backend, "launch", lambda name, store, update_id, worktree: launched.append(update_id))
    monkeypatch.setattr("openprogram.runtime.conversation.model_tools.load_agent_profile", lambda _: {"id": "main"})
    monkeypatch.setattr("openprogram.runtime.conversation.model_tools.resolve_model",
                        lambda *a: SimpleNamespace(provider="fake", id="fixed"))
    yield SimpleNamespace(layout=layout, launched=launched, store=SelfUpdateStore())
    clear_worktree()
    _reset_manager_for_tests()


def _request() -> TurnRequest:
    from openprogram.runtime.permissions.authority import local_owner_authority

    return TurnRequest(session_id="session-1", user_text="change it", agent_id="main", source="web",
                       **local_owner_authority())


def _prepare(installed, task: str = "feature"):
    return tool._prepare_version_update(
        task=task, goal="Add a feature", assertions=["It works"], iteration_policy=None, req=_request(),
        assistant_id="turn-1_reply", manager=get_manager(), store=installed.store,
    )


def _begin(installed, task: str = "feature") -> Path:
    return Path(tool._begin_update(task=task, req=_request(), manager=get_manager())["path"])


def _commit(tree: Path, name: str = "feature.txt") -> str:
    (tree / name).write_text("change\n")
    _git(tree, "add", name)
    _git(tree, "commit", "-m", "change")
    return _git(tree, "rev-parse", "HEAD")


def test_begin_opens_a_worktree_from_local_and_makes_it_the_turns_directory(installed) -> None:
    from openprogram.state.worktree.context import current_worktree_path

    tree = _begin(installed)

    assert tree == installed.layout.work / "feature" and (tree / "base.txt").is_file()
    assert _git(tree, "rev-parse", "HEAD") == _git(installed.layout.repo, "rev-parse", "local")
    assert current_worktree_path() == str(tree)
    clear_worktree()


@pytest.mark.parametrize("task", ["../escape", "", None, "a/b"])
def test_begin_refuses_a_name_that_is_not_one_directory(installed, task) -> None:
    with pytest.raises(tool.SelfUpdateToolError, match="task must be"):
        tool._begin_update(task=task, req=_request(), manager=get_manager())


def test_begin_refuses_a_task_that_already_exists(installed) -> None:
    _begin(installed)
    with pytest.raises(tool.SelfUpdateToolError, match="already has a worktree"):
        _begin(installed)


def test_prepare_needs_begin_first(installed) -> None:
    with pytest.raises(tool.SelfUpdateToolError, match="call self_update"):
        _prepare(installed)


def test_prepare_refuses_uncommitted_work(installed) -> None:
    tree = _begin(installed)
    (tree / "loose.txt").write_text("loose\n")

    with pytest.raises(tool.SelfUpdateToolError, match="commit your changes first"):
        _prepare(installed)


def test_prepare_refuses_a_task_with_no_commits(installed) -> None:
    _begin(installed)

    with pytest.raises(tool.SelfUpdateToolError, match="no commits beyond"):
        _prepare(installed)


def test_prepare_starts_the_old_versions_controller(installed) -> None:
    tree = _begin(installed)
    candidate = _commit(tree)

    result = _prepare(installed)

    record = installed.store.load(result["update_id"])
    assert record.request.controller_version == "running-version" and record.request.task == "feature"
    assert record.request.candidate_sha == candidate and record.state.phase is UpdatePhase.PREPARING
    assert installed.launched == [result["update_id"]]


def test_a_change_based_on_an_older_commit_is_accepted(installed) -> None:
    tree = _begin(installed)
    _commit(tree)
    (installed.layout.repo / "later.txt").write_text("later\n")
    _git(installed.layout.repo, "add", "later.txt")
    _git(installed.layout.repo, "commit", "-m", "later")

    result = _prepare(installed)

    record = installed.store.load(result["update_id"])
    assert record.request.base_sha == _git(installed.layout.repo, "rev-parse", "local")
    assert record.request.changed_paths == ("feature.txt",)


def test_a_second_update_waits_in_the_queue_and_can_be_cancelled(installed) -> None:
    _commit(_begin(installed, "one"))
    _prepare(installed, "one")
    _commit(_begin(installed, "two"))

    result = _prepare(installed, "two")

    assert result["queued"] is True and result["position"] == 1 and len(installed.launched) == 1
    status = tool._status_update(update_id=None, req=_request(), store=installed.store)
    assert [item["update_id"] for item in status["queued"]] == [result["update_id"]]

    stopped = tool._cancel_iteration(update_id=result["update_id"], req=_request(), store=installed.store)
    assert stopped["phase"] == "aborted" and installed.store.queued() == []


def test_versions_lists_what_is_kept(installed) -> None:
    from openprogram.operations.self_update.versions import pointer

    directory = installed.layout.version("running-version")
    directory.mkdir(parents=True)
    (directory / "version.json").write_text('{"id": "running-version", "commit": "abc", "base": "b"}')
    pointer.create(installed.layout, pointer.Pointer(generation=1, version="running-version", base="b",
                                                     last_good="running-version", last_good_base="b", writer="test"))

    result = tool._list_versions()

    assert result["current"] == "running-version"
    assert result["versions"][0]["current"] is True and result["versions"][0]["last_good"] is True


def _line_of_three(installed):
    """Three commits on local, one file each, and a sealed version of the first."""
    repo = installed.layout.repo
    commits = []
    for name in ("one", "two", "three"):
        (repo / f"{name}.txt").write_text(f"{name}\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", name)
        commits.append(_git(repo, "rev-parse", "HEAD"))
    return commits


def _undo(installed, target: str):
    return tool._undo_update(target=target, req=_request(), assistant_id="turn-1_reply", manager=get_manager(),
                             store=installed.store)


def test_undo_of_one_commit_reverts_it_in_a_registered_worktree(installed) -> None:
    commits = _line_of_three(installed)
    result = _undo(installed, commits[1])
    request = installed.store.load(result["update_id"]).request
    assert request.kind == "undo_commit" and request.task == f"undo-{commits[1][:12]}"
    assert request.base_sha == commits[2] and request.controller_version == "running-version"
    worktree = get_manager().get_worktree(request.worktree_id)
    assert worktree is not None and Path(worktree.worktree_path) == installed.layout.work / request.task
    assert not (Path(worktree.worktree_path) / "two.txt").exists()
    assert (Path(worktree.worktree_path) / "three.txt").exists()
    assert installed.launched == [result["update_id"]]


def test_undo_to_a_retained_version_goes_straight_to_its_trial(installed, monkeypatch) -> None:
    from openprogram.operations.self_update.control import undo
    from openprogram.operations.self_update.control.handoff import release_prepared_update

    commits = _line_of_three(installed)
    plan = undo.UndoPlan(undo.UNDO_TO, "v1", commits[0], commits[2], "one", version="v1")
    monkeypatch.setattr(undo, "plan", lambda layout, target: plan)
    result = _undo(installed, "v1")
    record = installed.store.load(result["update_id"])
    assert record.request.kind == "undo_to" and record.request.candidate_version == "v1"
    assert set(record.request.changed_paths) == {"two.txt", "three.txt"}
    assert not (installed.store.root / result["update_id"] / "source-repair-config.json").exists()
    assert release_prepared_update("session-1", "turn-1_reply", store=installed.store).phase is UpdatePhase.TRIAL


def test_undo_to_the_code_already_running_is_refused(installed, monkeypatch) -> None:
    from openprogram.operations.self_update.control import undo

    commits = _line_of_three(installed)
    monkeypatch.setattr(undo, "plan", lambda layout, target: undo.UndoPlan(undo.UNDO_TO, "v3", commits[2], commits[2], "x", version="v3"))
    with pytest.raises(tool.SelfUpdateToolError, match="already runs"):
        _undo(installed, "v3")
    assert installed.launched == []


def test_the_tool_is_refused_in_plan_mode() -> None:
    from openprogram.programs._host.runtime import _unsafe_in_channel

    assert "plan" in _unsafe_in_channel.get("self_update", ())


def test_a_turn_check_result_is_classified() -> None:
    from openprogram.operations.self_update.control.turn_check import _classify

    assert _classify(SimpleNamespace(failed=False, tool_calls=[{}]))["outcome"] == "ok"
    assert _classify(SimpleNamespace(failed=False, tool_calls=[]))["outcome"] == "inconclusive"
    provider = SimpleNamespace(failed=True, error="429", error_reason="rate_limit", error_retryable=True)
    assert _classify(provider)["outcome"] == "inconclusive"
    broken = SimpleNamespace(failed=True, error="ImportError", error_reason="internal", error_retryable=False)
    assert _classify(broken) == {"outcome": "failed", "error": "ImportError"}
