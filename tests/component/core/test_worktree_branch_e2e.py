"""End to end: a branch or a sub-agent runs in its own git worktree.

Every test drives a real temporary git repository: create the worktree
for one branch, work in it through the real file tools with relative
paths, then merge it back (or discard it) and check the checkout.
"""
from __future__ import annotations

import asyncio
import importlib
import subprocess
from pathlib import Path

import pytest

from openprogram.host import sandbox
from openprogram.state.worktree.context import reset_worktree, set_worktree
from openprogram.state.worktree.manager import WorktreeManager, _reset_manager_for_tests
from openprogram.state.worktree.types import WorktreeStatus


def _git(cwd, *args) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    (path / "a.txt").write_text("alpha\n")
    (path / "src").mkdir()
    (path / "src" / "app.py").write_text("print('hi')\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")


@pytest.fixture
def state(tmp_path, monkeypatch):
    home = tmp_path / "state"
    home.mkdir()
    for target in (
        "openprogram.state.paths.get_state_dir",
        "openprogram.state.paths.ensure_state_dir",
        "openprogram.state.paths.get_state_dir",
    ):
        monkeypatch.setattr(target, lambda: home)
    # The branch a lookup is for comes from the test, never a real store.
    monkeypatch.setattr("openprogram.state.worktree.store.current_branch_for", lambda _sid: None)
    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {})
    monkeypatch.setattr(sandbox, "_process_policy_override", sandbox._NO_PROCESS_POLICY)
    _reset_manager_for_tests()
    yield home
    _reset_manager_for_tests()


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    _init_repo(path)
    return path


def _tool_text(tool, args) -> str:
    result = asyncio.run(tool.execute("c", args, None, None))
    return "".join(getattr(block, "text", "") for block in result.content)


@pytest.fixture
def bound(state, repo):
    """A worktree of ``repo`` for branch ``b1`` of conversation ``s1``,
    bound the way a turn on that branch binds it."""
    wt = WorktreeManager().create_worktree(
        str(repo), label="feature", parent_session="s1", parent_branch="b1",
    )
    token = set_worktree(wt.worktree_path)
    yield wt
    reset_worktree(token)


# --- which branch runs where ------------------------------------------------


def test_branch_worktree_serves_only_its_branch(state, repo):
    mgr = WorktreeManager()
    own = mgr.create_worktree(str(repo), label="one", parent_session="s1", parent_branch="b1")
    assert own.base_sha == _git(repo, "rev-parse", "HEAD").strip()
    assert mgr.find_active_for_session("s1", "b1").id == own.id
    assert mgr.find_active_for_session("s1", "b2") is None
    shared = mgr.create_worktree(str(repo), label="all", parent_session="s1")
    # A branch with no worktree of its own runs in the conversation-wide one.
    assert mgr.find_active_for_session("s1", "b2").id == shared.id
    assert mgr.find_active_for_session("s1", "b1").id == own.id


def test_turn_location_follows_the_branch(state, repo, monkeypatch):
    from openprogram.runtime.conversation.session_settings import workdir

    monkeypatch.setattr(workdir, "_main_project", lambda _sid: None)
    monkeypatch.setattr("openprogram.state.paths.get_default_workdir", lambda: repo)
    wt = WorktreeManager().create_worktree(str(repo), parent_session="s1", parent_branch="b1")
    on_b1 = workdir.runtime_location_for("s1", use_context=False, branch_id="b1")
    on_b2 = workdir.runtime_location_for("s1", use_context=False, branch_id="b2")
    assert on_b1["workdir"] == wt.worktree_path
    assert on_b1["worktree"]["id"] == wt.id
    assert on_b2["workdir"] == str(repo)
    assert on_b2["worktree"] is None


def test_a_job_turn_keeps_its_own_worktree(state, repo):
    from openprogram.runtime.conversation.turn_runtime.turn_context import _job_worktree
    from openprogram.runtime.jobs.runner.shared import _current_job_id

    mgr = WorktreeManager()
    wt = mgr.create_worktree(str(repo), parent_session="s1", parent_job="job_1", parent_branch="b1")
    token = _current_job_id.set("job_1")
    try:
        assert _job_worktree(mgr).id == wt.id
    finally:
        _current_job_id.reset(token)
    assert _job_worktree(mgr) is None
    # A sub-agent's worktree is never its parent branch's working folder.
    assert mgr.find_active_for_session("s1", "b1") is None


def test_environment_block_names_the_worktree(bound, repo):
    from openprogram.runtime.context.prompt_assembly.components import environment_block, worktree_notice

    notice = worktree_notice(bound.worktree_path)
    assert str(repo) in notice and bound.branch_name in notice
    assert "re-read" in notice
    assert worktree_notice(str(repo)) == ""
    assert "Isolated worktree" in environment_block()


# --- every file tool works in the bound worktree ----------------------------


def test_file_tools_resolve_relative_paths_in_the_worktree(bound, repo):
    from openprogram.programs.tools.files.apply_patch import execute as apply_patch
    from openprogram.programs.tools.files.edit import edit
    from openprogram.programs.tools.files.glob import glob_tool
    from openprogram.programs.tools.files.grep import grep
    from openprogram.programs.tools.files.list import list_dir
    from openprogram.programs.tools.files.read import read
    from openprogram.programs.tools.files.write import write

    tree = Path(bound.worktree_path)

    out = _tool_text(write, {"file_path": "notes/new.txt", "content": "fresh line\n"})
    assert "Error" not in out, out
    assert (tree / "notes" / "new.txt").read_text() == "fresh line\n"
    assert not (repo / "notes").exists()

    assert "alpha" in _tool_text(read, {"file_path": "a.txt"})
    out = _tool_text(edit, {"file_path": "a.txt", "old_string": "alpha", "new_string": "beta"})
    assert "Error" not in out, out
    assert (tree / "a.txt").read_text() == "beta\n"
    assert (repo / "a.txt").read_text() == "alpha\n"

    listing = _tool_text(list_dir, {"path": "src"})
    assert "app.py" in listing
    found = _tool_text(glob_tool, {"pattern": "**/*.txt"})
    assert str(tree / "notes" / "new.txt") in found
    assert str(repo) + "/" not in found.replace(str(tree), "")
    hits = _tool_text(grep, {"pattern": "fresh line"})
    assert "new.txt" in hits
    assert "new.txt" in _tool_text(grep, {"pattern": "fresh", "path": "notes"})

    patched = apply_patch("*** Begin Patch\n*** Add File: src/extra.py\n+x = 1\n*** End Patch")
    assert "Error" not in patched, patched
    assert (tree / "src" / "extra.py").read_text() == "x = 1\n"
    assert not (repo / "src" / "extra.py").exists()


def test_bash_starts_in_the_worktree(bound):
    from openprogram.programs.tools.files.bash.bash import bash

    out = _tool_text(bash, {"command": "pwd"})
    assert Path(out.strip().splitlines()[-1]).resolve() == Path(bound.worktree_path).resolve()


def test_a_relative_read_is_checked_where_it_is_read(bound, monkeypatch):
    from openprogram.programs._host import execution

    seen = []
    monkeypatch.setattr(sandbox, "validate_read_path", lambda p: seen.append(p))
    assert execution.dispatch_sandbox_error("read", {"file_path": "a.txt"},
                                            path_params={"file_path": "read"}) is None
    assert seen == [str(Path(bound.worktree_path).resolve() / "a.txt")] or seen == [
        str(Path(bound.worktree_path) / "a.txt")]


def test_an_absolute_path_outside_the_worktree_warns(bound, repo):
    from openprogram.programs.tools.files.glob import glob_tool

    out = _tool_text(glob_tool, {"pattern": "*.txt", "path": "../"})
    assert out.startswith("[outside worktree]")


# --- what it holds, merging back, discarding ---------------------------------


def test_summary_counts_committed_and_uncommitted_work(bound):
    mgr = WorktreeManager()
    tree = Path(bound.worktree_path)
    (tree / "a.txt").write_text("changed\n")
    _git(tree, "commit", "-qam", "edit a")
    (tree / "b.txt").write_text("untracked\n")
    summary = mgr.summarize(mgr.get_worktree(bound.id))
    assert summary == {"commits": 1, "files": ["a.txt", "b.txt"], "dirty": True, "exists": True}


def test_merge_commits_pending_work_and_merges_after_the_checkout_moved(bound, repo):
    mgr = WorktreeManager()
    tree = Path(bound.worktree_path)
    (tree / "feature.txt").write_text("from the branch\n")   # never committed by the agent
    (repo / "other.txt").write_text("meanwhile\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "checkout moved on")

    with pytest.raises(Exception, match="worktree_dirty"):
        mgr.merge_worktree(bound.id, strategy="auto")
    merged = mgr.merge_worktree(bound.id, strategy="auto", commit_pending=True)

    assert merged.status == WorktreeStatus.MERGED
    assert (repo / "feature.txt").read_text() == "from the branch\n"
    assert (repo / "other.txt").exists()
    assert not tree.exists()
    assert len(_git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3  # a merge commit


def test_a_conflict_leaves_the_checkout_clean_and_the_worktree_open(bound, repo):
    mgr = WorktreeManager()
    tree = Path(bound.worktree_path)
    (tree / "a.txt").write_text("branch side\n")
    (repo / "a.txt").write_text("checkout side\n")
    _git(repo, "commit", "-qam", "checkout edit")

    with pytest.raises(Exception, match="merge_conflict"):
        mgr.merge_worktree(bound.id, strategy="auto", commit_pending=True)
    current = mgr.get_worktree(bound.id)
    assert current.status == WorktreeStatus.ACTIVE
    assert current.error.startswith("merge_conflict")
    assert _git(repo, "status", "--porcelain").strip() == ""
    assert (repo / "a.txt").read_text() == "checkout side\n"
    assert tree.exists()


def test_discard_removes_the_worktree_and_leaves_the_checkout(bound, repo):
    mgr = WorktreeManager()
    (Path(bound.worktree_path) / "scratch.txt").write_text("x\n")
    head = _git(repo, "rev-parse", "HEAD")
    gone = mgr.discard_worktree(bound.id, force=True)
    assert gone.status == WorktreeStatus.DISCARDED
    assert not Path(bound.worktree_path).exists()
    assert _git(repo, "rev-parse", "HEAD") == head
    assert bound.branch_name not in _git(repo, "branch", "--list")


# --- the worker actions the input box uses -----------------------------------


def test_branch_worktree_actions(state, repo, monkeypatch):
    from openprogram.interfaces.server.ws_actions import worktree as actions

    monkeypatch.setattr("openprogram.runtime.conversation.session_settings.workdir.project_workdir_for",
                        lambda _sid: repo / "src")
    payload = actions.session_worktrees_payload("s1")
    assert payload["source_repo"] == str(repo.resolve()) or Path(payload["source_repo"]).resolve() == repo.resolve()
    assert payload["worktrees"] == []

    first = actions.create_branch_worktree("s1", "b1")
    assert first["error"] is None
    again = actions.create_branch_worktree("s1", "b1")
    assert again["worktree"]["id"] == first["worktree"]["id"]
    rows = actions.session_worktrees_payload("s1")["worktrees"]
    assert [(row["parent_branch"], row["summary"]["files"]) for row in rows] == [("b1", [])]

    monkeypatch.setattr("openprogram.runtime.conversation.session_settings.workdir.project_workdir_for",
                        lambda _sid: Path("/"))
    assert actions.create_branch_worktree("s2", "b9")["error"].startswith("not_a_git_repo")


# --- a sub-agent with isolation="worktree" -------------------------------------


def test_isolated_sub_agent_worktree_lifecycle(state, repo, monkeypatch):
    agent_mod = importlib.import_module("openprogram.programs.tools.agents.agent.agent.agent")

    token = set_worktree(str(repo / "src"))
    try:
        wt, error = agent_mod._isolated_worktree("s1", "helper", "job_x")
    finally:
        reset_worktree(token)
    assert error is None
    assert wt.ephemeral and wt.parent_job == "job_x"
    assert Path(wt.source_repo).resolve() == repo.resolve()

    # Nothing changed: the worktree goes away.
    assert "changed nothing" in agent_mod._worktree_outcome(wt)
    assert WorktreeManager().get_worktree(wt.id).status == WorktreeStatus.DISCARDED

    token = set_worktree(str(repo))
    try:
        kept, _ = agent_mod._isolated_worktree("s1", "helper", "job_y")
    finally:
        reset_worktree(token)
    (Path(kept.worktree_path) / "result.txt").write_text("done\n")
    outcome = agent_mod._worktree_outcome(kept)
    assert "1 file(s) changed" in outcome and "worktree_merge" in outcome
    assert WorktreeManager().get_worktree(kept.id).status == WorktreeStatus.ACTIVE


def test_isolation_needs_a_repository(state, tmp_path):
    agent_mod = importlib.import_module("openprogram.programs.tools.agents.agent.agent.agent")

    plain = tmp_path / "plain"
    plain.mkdir()
    token = set_worktree(str(plain))
    try:
        wt, error = agent_mod._isolated_worktree("s1", "", "job_z")
    finally:
        reset_worktree(token)
    assert wt is None and "needs a git repository" in error


def test_a_worktree_is_never_made_from_the_process_working_directory(state, repo, tmp_path, monkeypatch):
    """A worker started inside a checkout (a test run inside OpenProgram's own,
    a daemon launched from a repository) must not branch that checkout: with
    no turn worktree, project folder or configured folder there is no repository."""
    agent_mod = importlib.import_module("openprogram.programs.tools.agents.agent.agent.agent")
    create_mod = importlib.import_module(
        "openprogram.programs.tools.files.worktree.worktree_create.worktree_create")
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.delenv("OPENPROGRAM_WORKDIR", raising=False)
    monkeypatch.setattr("openprogram.runtime.conversation.session_settings.workdir.project_workdir_for", lambda _sid: plain)

    wt, error = agent_mod._isolated_worktree("s1", "helper", "job_cwd")
    assert wt is None and "needs a git repository" in error
    assert create_mod._resolve_source_repo("", "s1") == str(plain)

    monkeypatch.setattr("openprogram.runtime.conversation.session_settings.workdir.project_workdir_for", lambda _sid: None)
    assert create_mod._resolve_source_repo("", "s1") is None
    assert _git(repo, "branch", "--list", "op/wt/*") == ""


def test_unknown_isolation_is_refused():
    from openprogram.programs.tools.agents.agent.agent.agent import _agent_impl

    assert "unknown isolation" in _agent_impl("x", isolation="container")
    assert "send_message" in _agent_impl("x", isolation="worktree", to="s:h")


# --- a background helper's worktree waits for the helper ----------------------


def test_a_background_writer_gets_a_worktree_and_a_reader_does_not():
    from openprogram.runtime.agent_registry import builtin_agents, delegation
    from openprogram.runtime.agent_registry.manager import AgentSpec

    writer = AgentSpec(id="w", name="Writer")
    explorer = AgentSpec(id="explorer", tools=builtin_agents.builtin("explorer")["tools"])
    assert delegation.isolation_for(writer, requested="", background=True) == ("worktree", True)
    assert delegation.isolation_for(explorer, requested="", background=True) == ("", False)


def test_merge_waits_for_the_helper_that_writes_there(state, repo, monkeypatch):
    from openprogram.runtime.jobs.types import Job, JobStatus

    job = Job(id="job_w", parent_session_id="s1", prompt="edit", agent_id="main",
              status=JobStatus.RUNNING)
    monkeypatch.setattr("openprogram.runtime.jobs.store.load_job",
                        lambda sid, jid: job if (sid, jid) == ("s1", "job_w") else None)
    mgr = WorktreeManager()
    wt = mgr.create_worktree(str(repo), label="helper", parent_session="s1", parent_job="job_w")
    (Path(wt.worktree_path) / "new.txt").write_text("from the helper\n")

    for act in (lambda: mgr.merge_worktree(wt.id, strategy="auto", commit_pending=True),
                lambda: mgr.discard_worktree(wt.id, force=True)):
        with pytest.raises(Exception, match="owner_running"):
            act()
    assert mgr.get_worktree(wt.id).status == WorktreeStatus.ACTIVE
    assert not (repo / "new.txt").exists()

    job.status = JobStatus.COMPLETED
    merged = mgr.merge_worktree(wt.id, strategy="auto", commit_pending=True)
    assert merged.status == WorktreeStatus.MERGED
    assert (repo / "new.txt").read_text() == "from the helper\n"


def test_the_helpers_own_end_discards_while_it_still_counts_as_running(state, repo, monkeypatch):
    from openprogram.runtime.jobs.types import Job, JobStatus

    job = Job(id="job_c", parent_session_id="s1", prompt="edit", agent_id="main",
              status=JobStatus.RUNNING)
    monkeypatch.setattr("openprogram.runtime.jobs.store.load_job", lambda sid, jid: job)
    mgr = WorktreeManager()
    wt = mgr.create_worktree(str(repo), parent_session="s1", parent_job="job_c")
    gone = mgr.discard_worktree(wt.id, force=True, owner_done=True)
    assert gone.status == WorktreeStatus.DISCARDED
