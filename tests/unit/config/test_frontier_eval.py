"""The FrontierHarness Eval launcher: job config, key lookup and summary."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts.benchmarks import frontier_eval as fe


def _target(name: str = "fh-v1-tb21") -> dict:
    return fe.load_target(name)


def _config(tmp_path, target=None, **options):
    return fe.job_config(
        target or _target(), job_name="j1", jobs_dir=tmp_path,
        wheel=tmp_path / "openprogram-1.1.1-py3-none-any.whl",
        constraints=tmp_path / "constraints.txt", **options)


def test_the_launcher_loads_without_harbor():
    assert not any(name == "harbor" or name.startswith("harbor.") for name in sys.modules)


@pytest.mark.parametrize("name", ["fh-v1-tb21", "fh-v1.1", "tb2"])
def test_every_target_names_its_sources_effort_control_and_prices(name):
    target = _target(name)
    assert target["sources"] and target["reasoning_effort"] in fe.EFFORTS
    assert target["control"]["version"] and target["harbor_version"] == fe.HARBOR_VERSION
    assert target["model"] in target["prices"]["models"]
    assert len(target["sha256"]) == 64


def test_the_fh_v1_target_is_its_21_terminal_bench_tasks():
    target = _target("fh-v1-tb21")
    assert len(target["tasks"]) == len(set(target["tasks"])) == 21
    assert target["dataset"] == {"name": "terminal-bench", "version": "2.0", "task_prefix": ""}
    assert target["trial_wall_clock_cap_min"] == 90  # run-trials.sh --timeout 5400


def test_a_target_without_a_reasoning_effort_is_refused(tmp_path):
    data = json.loads((fe.TARGETS_DIR / "fh-v1-tb21.json").read_text(encoding="utf-8"))
    del data["reasoning_effort"]
    path = tmp_path / "t.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(fe.EvalError, match="reasoning_effort"):
        fe.load_target(str(path))


def test_the_job_runs_openprogram_beside_the_pinned_control(tmp_path):
    config = _config(tmp_path, concurrency=2, install_only=True)
    ours, control = config["agents"]
    assert ours["import_path"] == fe.AGENT_IMPORT_PATH and "name" not in ours
    assert ours["model_name"] == control["model_name"] == "openai/gpt-5.6-terra"
    assert ours["kwargs"] == {"wheel": str(tmp_path / "openprogram-1.1.1-py3-none-any.whl"),
                              "constraints": str(tmp_path / "constraints.txt"),
                              "cost_ledger": str(tmp_path / "j1.attempts.jsonl"),
                              "reasoning_effort": "medium"}
    assert control["name"] == "codex"
    assert control["kwargs"] == {"version": "0.148.0", "reasoning_effort": "medium"}
    assert fe.ledger_path(tmp_path / "j1") == tmp_path / "j1.attempts.jsonl"
    dataset = config["datasets"][0]
    assert (dataset["name"], dataset["version"]) == ("terminal-bench", "2.0")
    assert "task_prefix" not in dataset and len(dataset["task_names"]) == 21
    assert config["n_attempts"] == 3 and config["n_concurrent_trials"] == 2
    # Harbor retries nothing: the launcher does, and keeps every attempt.
    assert config["retry"] == {"max_retries": 0}
    assert all("disable_web_search" not in agent["kwargs"] for agent in config["agents"])
    assert config["agent_setup_timeout_multiplier"] == 3.0
    assert config["job_name"] == "j1" and config["jobs_dir"] == str(tmp_path)
    assert config["install_only"] is True


def test_choices_narrow_the_targets_tasks_and_cap_both_agents(tmp_path):
    config = _config(tmp_path, _target("fh-v1.1"), attempts=1,
                     tasks=["fix-git", "terminal-bench/music-*"], exclude_tasks=["mailman"],
                     agent_timeout_cap_min=12, control=False, bundle=tmp_path / fe.BUNDLE_NAME)
    (ours,) = config["agents"]
    assert ours["max_timeout_sec"] == 720.0
    assert ours["kwargs"]["bundle"] == str(tmp_path / fe.BUNDLE_NAME)
    assert ours["kwargs"]["wheel"]  # the fallback for other images
    dataset = config["datasets"][0]
    assert (dataset["name"], dataset["ref"]) == ("frontier-harness-eval/frontier-harness-eval",
                                                 "v1.1.0")
    assert dataset["task_names"] == ["terminal-bench/fix-git", "terminal-bench/music-*"]
    assert dataset["exclude_task_names"] == ["terminal-bench/mailman"]
    assert config["n_attempts"] == 1


def test_a_target_that_allows_no_overrides_refuses_a_timeout_cap(tmp_path):
    with pytest.raises(fe.EvalError, match="tb2 allows no"):
        _config(tmp_path, _target("tb2"), agent_timeout_cap_min=12)


def test_every_departure_from_the_target_is_recorded(tmp_path):
    target = _target()
    # Both agents run at the target's effort: nothing departs.
    assert fe.deviations(target, environment="runta") == []
    noted = fe.deviations(target, environment="docker", model="openai/gpt-5.6-sol",
                          attempts=1, tasks=["regex-log"], agent_timeout_cap_min=12,
                          control=False, agent_env={"X": "1"}, effort="high")
    assert len(noted) == 8
    assert "reasoning effort high instead of medium" in noted
    assert any("docker" in reason for reason in noted)
    assert any("control" in reason for reason in noted)


def test_infrastructure_and_verifier_errors_never_overlap():
    # A verifier that ran and timed out or left no reward is not taken off
    # the denominator.
    assert not fe.VERIFIER_ERRORS & fe.INFRA_ERRORS
    assert "AgentTimeoutError" not in fe.INFRA_ERRORS
    assert "NonZeroAgentExitCodeError" not in fe.INFRA_ERRORS


@pytest.mark.parametrize("model, override, expected", [
    ("openai/gpt-5.6-sol", None, "OPENAI_API_KEY"),
    ("deepseek/deepseek-chat", None, "DEEPSEEK_API_KEY"),
    ("kimi/k3", "MY_KEY", "MY_KEY"),
])
def test_key_variable_follows_the_provider(model, override, expected):
    assert fe.key_env_name(model, override) == expected


def test_an_unknown_provider_needs_an_explicit_key_variable():
    with pytest.raises(fe.EvalError, match="--key-env"):
        fe.key_env_name("kimi/k3")


def _store(home: Path, profile_dir: str, provider: str, data: dict) -> None:
    path = home / profile_dir / "auth" / provider / "default.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_the_stored_key_is_the_active_credential_of_that_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(fe.Path, "home", classmethod(lambda cls: tmp_path))
    _store(tmp_path, ".openprogram-dev", "openai", {
        "active_credential_id": "b",
        "credentials": [
            {"credential_id": "a", "payload": {"kind": "api_key", "auth_value": "key-a"}},
            {"credential_id": "b", "payload": {"kind": "api_key", "auth_value": " key-b "}},
        ]})
    _store(tmp_path, ".openprogram", "openai", {
        "credentials": [{"credential_id": "s", "payload": {"kind": "api_key",
                                                           "auth_value": "stable"}}]})
    assert fe.stored_api_key("dev", "openai") == "key-b"
    assert fe.stored_api_key("default", "openai") == "stable"
    with pytest.raises(fe.EvalError, match="no stored credential"):
        fe.stored_api_key("dev", "deepseek")


def test_an_oauth_credential_is_not_taken_as_an_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(fe.Path, "home", classmethod(lambda cls: tmp_path))
    _store(tmp_path, ".openprogram-dev", "openai", {
        "credentials": [{"credential_id": "o", "payload": {"kind": "oauth",
                                                           "auth_value": "token"}}]})
    with pytest.raises(fe.EvalError, match="no API key"):
        fe.stored_api_key("dev", "openai")


def _trial(job: Path, name: str, task: str, *, reward=None, exception=None, cost=0.1,
           inp=1000, cached=800, out=50, finished=True, agent="openprogram",
           started="2026-11-12T01:00:00Z") -> None:
    folder = job / name
    folder.mkdir(parents=True)
    result = {
        "trial_name": name, "task_name": f"terminal-bench/{task}",
        "agent_info": {"name": agent, "version": "1"},
        "verifier_result": None if reward is None else {"rewards": {"reward": reward}},
        "exception_info": None if exception is None else {
            "exception_type": exception, "exception_message": f"{exception}: details\nmore"},
        "agent_result": {"n_input_tokens": inp, "n_cache_tokens": cached,
                         "n_output_tokens": out, "cost_usd": cost,
                         "metadata": {"openprogram": {"ended": "completed",
                                                      "model_requests": 3, "tool_calls": 2}}},
        "started_at": started,
        "finished_at": "2026-11-12T01:10:00Z" if finished else None,
        "agent_setup": {"started_at": "2026-11-12T01:00:00Z",
                        "finished_at": "2026-11-12T01:05:00Z"},
        "agent_execution": {"started_at": "2026-11-12T01:05:00Z",
                            "finished_at": "2026-11-12T01:08:30Z"},
    }
    (folder / "result.json").write_text(json.dumps(result), encoding="utf-8")


@pytest.mark.parametrize("reward, exception, expected", [
    (1.0, None, "passed"),
    (1.0, "AgentTimeoutError", "passed"),
    (0.0, None, "agent"),
    (0.0, "AgentTimeoutError", "agent"),
    (None, "NonZeroAgentExitCodeError", "agent"),
    (None, "AgentSetupTimeoutError", "infrastructure"),
    (0.0, "ApiRateLimitError", "infrastructure"),
    # Harbor retries a stalled trial whatever its reward; the summary does too.
    (1.0, "ApiResponseStalledError", "infrastructure"),
    (None, None, "infrastructure"),
    (None, "CancelledError", "cancelled"),
    # A solution that hangs under the tests also ends in a verifier timeout.
    (None, "VerifierTimeoutError", "agent"),
    (None, "RewardFileNotFoundError", "agent"),
    (None, "RewardFileEmptyError", "agent"),
])
def test_each_trial_is_a_pass_an_agent_failure_or_infrastructure(reward, exception, expected):
    result = {"verifier_result": None if reward is None else {"rewards": {"reward": reward}},
              "exception_info": None if exception is None else {"exception_type": exception}}
    assert fe.classify(result)[0] == expected


def test_a_stalled_stream_and_a_verifier_that_never_ran_are_not_the_agents_failures(tmp_path):
    # Both seen in the sample: the turn ended on OpenProgram's idle bound
    # (recorded as NonZeroAgentExitCodeError), and a completed run whose
    # verifier could not start (RuntimeError building the egress sidecar,
    # no verifier output).
    stalled = {"verifier_result": None,
               "exception_info": {"exception_type": "NonZeroAgentExitCodeError"},
               "agent_result": {"metadata": {"openprogram": {
                   "ended": "failed", "error": "no openai stream event for 300s"}}}}
    ungraded = {"verifier_result": None, "exception_info": {"exception_type": "RuntimeError"},
                "agent_result": {"metadata": {"openprogram": {"ended": "completed"}}}}
    crashed = {"verifier_result": None,
               "exception_info": {"exception_type": "NonZeroAgentExitCodeError"},
               "agent_result": {"metadata": {"openprogram": {"ended": "failed",
                                                             "error": "upstream 500"}}}}
    trial = tmp_path / "t"
    trial.mkdir()
    assert fe.classify(stalled) == ("infrastructure", "model stream stalled")
    not_started = {"verifier_result": None,
                   "exception_info": {"exception_type": "NonZeroAgentExitCodeError"},
                   "agent_result": {"metadata": {"openprogram": {
                       "ended": "not_started", "error": "x (HTTP 429 for https://x)"}}}}
    assert fe.classify(not_started) == ("infrastructure", "rate_limit before the turn")
    assert fe.classify(ungraded, trial) == ("infrastructure",
                                            "verifier did not run (RuntimeError)")
    assert fe.classify(crashed) == ("agent", "NonZeroAgentExitCodeError")


def test_a_verifier_timeout_after_the_turn_finished_counts_against_the_agent(tmp_path):
    # The verifier ran (its output is there) and timed out: the solution may
    # hang under the tests, so the trial stays in the pass rate.
    timed_out = {"verifier_result": None,
                 "exception_info": {"exception_type": "VerifierTimeoutError"},
                 "agent_result": {"metadata": {"openprogram": {"ended": "completed"}}}}
    trial = tmp_path / "t"
    (trial / "verifier").mkdir(parents=True)
    (trial / "verifier" / "test-stdout.txt").write_text(
        "collected 12 items\n/tests/test_outputs.py::test_runs ", encoding="utf-8")
    assert fe.classify(timed_out, trial) == ("agent", "VerifierTimeoutError")
    assert fe.classify(timed_out) == ("agent", "VerifierTimeoutError")


#: The end of sqlite-with-gcov's verifier output in the sample: test.sh could
#: not download uv, so no test ran and it wrote reward 0.
UV_DOWNLOAD_FAILED = """\
Setting up curl (8.5.0-2ubuntu10.16) ...
downloading uv 0.9.5 x86_64-unknown-linux-gnu
curl: (92) HTTP/2 stream 1 was not closed cleanly: PROTOCOL_ERROR (err 1)
failed to download https://github.com/astral-sh/uv/releases/download/0.9.5/uv-x86_64-unknown-linux-gnu.tar.gz
this may be a standard network error, but it may also indicate
/tests/test.sh: line 10: /root/.local/bin/env: No such file or directory
/tests/test.sh: line 19: uvx: command not found
"""


def _verified(job: Path, name: str, stdout: str, *, reward=0.0, exception=None) -> Path:
    _trial(job, name, name.partition("__")[0], reward=reward, exception=exception)
    (job / name / "verifier").mkdir()
    (job / name / "verifier" / "test-stdout.txt").write_text(stdout, encoding="utf-8")
    return job / name


@pytest.mark.parametrize("stdout, exception, expected", [
    (UV_DOWNLOAD_FAILED, None, ("infrastructure", "verifier setup failed")),
    (UV_DOWNLOAD_FAILED, "VerifierTimeoutError", ("infrastructure", "verifier setup failed")),
    ("error: Failed to fetch: `https://pypi.org/simple/pytest/`\n", None,
     ("infrastructure", "verifier setup failed")),
    # The tests ran: a reward of 0 is the agent's, whatever else was printed.
    (UV_DOWNLOAD_FAILED.replace("uvx: command not found", "x")
     + "=================== 9 failed, 25 passed, 2 errors in 18.56s ====================\n",
     None, ("agent", "reward 0")),
    ("============================== 2 failed in 0.14s ===============================\n",
     None, ("agent", "reward 0")),
])
def test_a_verifier_whose_own_setup_failed_is_infrastructure(tmp_path, stdout, exception,
                                                            expected):
    trial = _verified(tmp_path / "j", "sqlite-with-gcov__a", stdout, exception=exception)
    result = json.loads((trial / "result.json").read_text())
    assert fe.classify(result, trial) == expected


def test_the_summary_reads_each_trials_verifier_output(tmp_path):
    job = tmp_path / "jobs" / "j1"
    _verified(job, "sqlite-with-gcov__a", UV_DOWNLOAD_FAILED)
    _verified(job, "fix-git__a", "=== 2 passed in 0.14s ===\n", reward=1.0)
    summary = fe.summarize(job, {})
    row = next(r for r in summary["rows"] if r["trial"] == "sqlite-with-gcov__a")
    assert (row["outcome"], row["detail"]) == ("infrastructure", "verifier setup failed")
    assert "failed to download https://github.com/astral-sh/uv" in row["error"]
    assert summary["pass_rate"] == 1.0


def test_rerun_infra_drops_a_failed_verifier_setup_and_keeps_its_cost(tmp_path):
    job = tmp_path / "jobs" / "j1"
    _verified(job, "sqlite-with-gcov__a", UV_DOWNLOAD_FAILED)
    _verified(job, "bun__a", "=== 9 failed, 25 passed in 18.56s ===\n")
    _trial(job, "mailman__a", "mailman", exception="ApiResponseStalledError", cost=0.3)
    assert sorted(fe.drop_infrastructure_trials(job)) == ["mailman__a", "sqlite-with-gcov__a"]
    assert sorted(p.name for p in job.iterdir()) == ["bun__a"]
    assert fe.spent_usd(job) == pytest.approx(0.1 + 0.1 + 0.3)


def test_cost_of_a_trial_harbor_deleted_to_retry_it_still_counts(tmp_path):
    import shutil

    from scripts.benchmarks.harbor_agent import commands

    job = tmp_path / "jobs" / "j1"
    ledger = fe.ledger_path(job)

    def attempt(trial: str, attempt_id: str, cost: float) -> None:
        commands.append_attempt(ledger, trial=trial, attempt=attempt_id, fields={
            "cost_usd": cost, "n_input_tokens": 10,
            "metadata": {"openprogram": {"ended": "failed"}}})

    # The first attempt stalls after spending 0.16; Harbor deletes its folder
    # (harbor/trial/queue.py, before every retry) and runs it again.
    _trial(job, "freecad__a", "freecad", exception="ApiResponseStalledError", cost=0.16)
    attempt("freecad__a", "one", 0.16)
    assert fe.spent_usd(job) == pytest.approx(0.16)
    shutil.rmtree(job / "freecad__a")
    assert fe.spent_usd(job) == pytest.approx(0.16)
    _trial(job, "freecad__a", "freecad", reward=1.0, cost=0.5)
    attempt("freecad__a", "two", 0.5)
    _trial(job, "fix-git__a", "fix-git", reward=1.0, cost=0.2)  # no ledger line (older adapter)
    assert fe.spent_usd(job) == pytest.approx(0.86)
    summary = fe.summarize(job, {})
    assert summary["cost_usd"] == pytest.approx(0.86)
    assert summary["trials_cost_usd"] == pytest.approx(0.7)
    assert (summary["discarded_attempts"], summary["discarded_cost_usd"]) == (1, 0.16)
    assert "$0.86 for every attempt, $0.16 of it in 1 retried or rerun attempts" in \
        fe.summary_markdown(summary)


def test_the_summary_splits_failures_and_counts_tokens_and_cost(tmp_path):
    job = tmp_path / "jobs" / "j1"
    _trial(job, "fix-git__a", "fix-git", reward=1.0, cost=0.5)
    _trial(job, "fix-git__b", "fix-git", reward=0.0, cost=0.25)
    _trial(job, "mailman__a", "mailman", exception="AgentSetupTimeoutError", cost=None,
           inp=0, cached=0, out=0)
    _trial(job, "mailman__b", "mailman", reward=0.0, exception="AgentTimeoutError", cost=1.0)
    _trial(job, "regex-log__a", "regex-log", finished=False)  # still running: not counted
    (job / "result.json").write_text(json.dumps({
        "n_total_trials": 8, "started_at": "2026-11-12T01:00:00",
        "finished_at": "2026-11-12T02:30:00", "stats": {"n_retries": 2}}), encoding="utf-8")
    summary = fe.summarize(job, {"commit": "abc", "model": "openai/x"})
    assert (summary["trials"], summary["passed"], summary["agent_failures"],
            summary["infrastructure_errors"]) == (4, 1, 2, 1)
    assert summary["pass_rate"] == pytest.approx(1 / 3)
    assert summary["tasks"]["terminal-bench/fix-git"]["pass_rate"] == 0.5
    assert summary["tasks"]["terminal-bench/mailman"]["pass_rate"] == 0.0
    assert summary["input_tokens"] == 3000 and summary["cached_tokens"] == 2400
    assert summary["cached_share"] == pytest.approx(0.8)
    assert summary["cost_usd"] == pytest.approx(1.75)
    assert summary["wall_min"] == 90.0 and summary["retries"] == 2
    assert summary["agent_min_median"] == 3.5
    row = next(r for r in summary["rows"] if r["trial"] == "mailman__b")
    assert row["detail"] == "agent timeout" and row["error"] == "AgentTimeoutError: details"
    assert fe.spent_usd(job) == pytest.approx(1.75)
    text = fe.summary_markdown(summary)
    assert "| terminal-bench/fix-git | 2 | 1 | 1 | 0 | 50% | $0.75 |" in text
    assert "infrastructure errors 1" in text


def test_each_agent_is_summed_up_apart_and_paired_task_by_task(tmp_path):
    from scripts.benchmarks.harbor_agent import commands

    job = tmp_path / "jobs" / "j1"
    ledger = fe.ledger_path(job)
    # Task a: OpenProgram passes twice, the second time after a stalled
    # attempt Harbor retried; Codex passes once and fails once.
    _trial(job, "a__1", "a", reward=1.0, cost=0.5, started="2026-11-12T01:00:00Z")
    _trial(job, "a__2", "a", reward=1.0, cost=0.5, started="2026-11-12T01:01:00Z")
    _trial(job, "a__3", "a", reward=1.0, cost=0.4, agent="codex", inp=2000, cached=1000,
           out=100, started="2026-11-12T01:00:00Z")
    _trial(job, "a__4", "a", reward=0.0, cost=0.4, agent="codex",
           started="2026-11-12T01:01:00Z")
    # Task b: OpenProgram fails, Codex passes.
    _trial(job, "b__1", "b", reward=0.0, cost=0.2)
    _trial(job, "b__2", "b", reward=1.0, cost=0.3, agent="codex")
    for trial, attempt, kind in (("a__1", "x", None), ("a__2", "y", "stalled"),
                                 ("a__2", "z", None), ("b__1", "w", None)):
        commands.append_attempt(ledger, trial=trial, attempt=attempt, agent="openprogram",
                                error_kind=kind, fields={"cost_usd": 0.1, "n_input_tokens": 1})
    (job / "result.json").write_text(json.dumps({"stats": {"n_retries": 2}}), encoding="utf-8")
    target = _target()
    summary = fe.summarize(job, {"target": "fh-v1-tb21", "target_path": target["path"],
                                 "control": "codex"})
    ours, codex = summary["agents"]["openprogram"], summary["agents"]["codex"]
    assert (ours["trials"], ours["passed"], codex["trials"], codex["passed"]) == (3, 2, 3, 2)
    assert ours["retries"] == 1 and ours["retried_kinds"] == {"stalled": 1}
    assert codex["retries"] == 1  # the job's other retry
    # a__2 passed on its second attempt, after a stall that was the provider's.
    assert ours["first_attempt_pass_rate"] == pytest.approx(2 / 3)
    assert codex["first_attempt_pass_rate"] is None  # Harbor keeps no per-trial retries
    # First valid attempt per task: a passes for both, b fails for OpenProgram.
    assert ours["first_valid_pass_rate"] == 0.5 and codex["first_valid_pass_rate"] == 1.0
    # Codex a__3 at the target's prices: 1000 uncached, 1000 cached, 100 out.
    assert codex["rows_repriced_usd"][0] == pytest.approx((1000 * 2.0 + 1000 * 0.2
                                                          + 100 * 12.0) / 1e6)
    assert ours["cost_per_pass_usd"] == pytest.approx(sum(ours["rows_repriced_usd"]) / 2)
    paired = summary["paired"]
    assert (paired["pairs"], paired["ours_only"], paired["control_only"]) == (3, 1, 1)
    assert paired["p_value"] == 1.0
    text = fe.summary_markdown(summary)
    assert "| openprogram | 3 | 2 |" in text and "| codex | 3 | 2 |" in text
    assert "McNemar" in text


def test_the_smallest_difference_a_paired_run_can_show():
    # 21 tasks x 3 attempts with 30% of pairs discordant: about 19 points.
    assert fe.min_detectable_difference(63, 0.3) == pytest.approx(0.193, abs=0.002)
    assert fe.min_detectable_difference(0, 0.3) is None


def test_mcnemar_is_the_exact_binomial_test_of_the_discordant_pairs():
    assert fe.mcnemar_p(0, 0) == 1.0
    assert fe.mcnemar_p(6, 0) == pytest.approx(2 / 64)
    assert fe.mcnemar_p(5, 1) == pytest.approx(2 * 7 / 64)


def test_wall_time_reads_harbors_local_start_against_its_utc_update(tmp_path, monkeypatch):
    import time
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    try:
        job = tmp_path / "jobs" / "j1"
        _trial(job, "fix-git__a", "fix-git", reward=1.0)
        # A running job as Harbor 0.25 writes it: local start, UTC update.
        (job / "result.json").write_text(json.dumps({
            "started_at": "2026-11-12T03:00:00.000001",
            "updated_at": "2026-11-12T11:30:00.000001Z", "finished_at": None}),
            encoding="utf-8")
        assert fe.summarize(job, {})["wall_min"] == 30.0
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_the_summary_is_written_beside_harbors_files(tmp_path):
    job = tmp_path / "jobs" / "j1"
    _trial(job, "fix-git__a", "fix-git", reward=1.0)
    fe._save_run(job, {"commit": "c0ffee", "model": "openai/x", "ref": "stable"})
    fe.write_summary(job)
    assert json.loads((job / fe.SUMMARY_JSON).read_text())["run"]["commit"] == "c0ffee"
    assert (job / fe.SUMMARY_MD).read_text().startswith("# j1\n")
    assert fe.run_file(job) == tmp_path / "jobs" / "j1.run.json"


def test_resume_refuses_a_changed_wheel(tmp_path):
    wheel = tmp_path / "openprogram-1.1.1-py3-none-any.whl"
    wheel.write_bytes(b"one")
    job = tmp_path / "jobs" / "j1"
    job.mkdir(parents=True)
    fe._save_run(job, {"wheel": str(wheel), "wheel_sha256": fe.sha256(wheel),
                       "model": "openai/x", "environment": "docker"})
    wheel.write_bytes(b"two")
    status = fe.main(["resume", "--root", str(tmp_path), "--job", "j1"])
    assert status == 2


def test_check_names_the_harbor_version_it_found(tmp_path, monkeypatch, capsys):
    # `check --allow-harbor-version` on another release used to report the
    # pinned release as the one that was ready.
    harbor = tmp_path / "harbor"
    harbor.write_text("#!/bin/sh\necho 'harbor 0.26.1'\n", encoding="utf-8")
    harbor.chmod(0o755)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    # The free-space check reads the real disk; this test is only about the
    # version, so it must not depend on the machine running it.
    plenty = fe.MIN_FREE_GB * 2 * 2**30
    monkeypatch.setattr(fe.shutil, "disk_usage",
                        lambda _path: type("Usage", (), {"total": plenty, "used": 0, "free": plenty})())
    status = fe.main(["check", "--root", str(tmp_path / "root"), "--harbor", str(harbor),
                      "--allow-harbor-version", "--env", "runta", "--ref", "HEAD",
                      "--target", "fh-v1-tb21"])
    assert status == 0
    out = capsys.readouterr().out
    assert "ready: Harbor 0.26.1," in out
    assert "found; the adapter is checked against 0.25.0" in out


def test_a_control_trial_rerun_after_rerun_infra_keeps_both_costs(tmp_path):
    # Only OpenProgram's agent writes the ledger; the drop writes the
    # control's line, and Harbor reruns the trial under the same name.
    job = tmp_path / "jobs" / "j1"
    _trial(job, "t1__codex", "t1", agent="codex", exception="ApiInternalServerError",
           cost=1.0, started="2026-11-12T01:00:00Z")
    assert fe.drop_infrastructure_trials(job) == ["t1__codex"]
    _trial(job, "t1__codex", "t1", agent="codex", reward=1.0, cost=2.5,
           started="2026-11-12T02:00:00Z")
    costs = fe._costs(job)
    assert costs["total"] == pytest.approx(3.5)
    assert costs["by_agent"] == {"codex": pytest.approx(3.5)}
    assert (costs["discarded_attempts"], costs["discarded_cost"]) == (1, pytest.approx(1.0))
    assert fe.spent_usd(job) == pytest.approx(3.5)


def test_a_retry_for_the_providers_fault_does_not_spoil_the_first_attempt(tmp_path):
    from scripts.benchmarks.harbor_agent import commands

    job = tmp_path / "jobs" / "j1"
    ledger = fe.ledger_path(job)
    # a__1: rate limited, retried, passed. a__2: OpenProgram crashed (no
    # cause the summary names), retried, passed. a__3: dropped by
    # --rerun-infra for a verifier whose setup failed, rerun, passed.
    for name in ("a__1", "a__2"):
        _trial(job, name, "a", reward=1.0)
    _verified(job, "a__3", UV_DOWNLOAD_FAILED)
    fe.drop_infrastructure_trials(job)
    _trial(job, "a__3", "a", reward=1.0, started="2026-11-12T02:00:00Z")
    for trial, attempt, kind in (("a__1", "x", "rate_limit"), ("a__1", "y", None),
                                 ("a__2", "z", None), ("a__2", "w", None),
                                 ("a__3", "v", None)):
        commands.append_attempt(ledger, trial=trial, attempt=attempt, error_kind=kind,
                                fields={"cost_usd": 0.1})
    ours = fe.summarize(job, {})["agents"]["openprogram"]
    assert ours["first_attempt_pass_rate"] == pytest.approx(2 / 3)
    assert ours["retried_kinds"] == {"rate_limit": 1, "agent": 1, "infrastructure": 1}


def test_cost_per_pass_counts_the_same_attempts_for_both_agents(tmp_path):
    from scripts.benchmarks.harbor_agent import commands

    job = tmp_path / "jobs" / "j1"
    ledger = fe.ledger_path(job)
    _trial(job, "a__1", "a", reward=1.0, cost=0.5, inp=1000, cached=0, out=0)
    _trial(job, "a__2", "a", reward=1.0, cost=0.4, agent="codex", inp=1000, cached=0, out=0)
    # OpenProgram's a__1 was retried once; that attempt cost 3.0.
    commands.append_attempt(ledger, trial="a__1", attempt="x", error_kind=None,
                            fields={"cost_usd": 3.0})
    commands.append_attempt(ledger, trial="a__1", attempt="y", error_kind=None,
                            fields={"cost_usd": 0.5})
    target = _target()
    agents = fe.summarize(job, {"target_path": target["path"], "control": "codex"})["agents"]
    ours, codex = agents["openprogram"], agents["codex"]
    # 1000 uncached input tokens at $2 per million, for each agent's surviving pass.
    assert ours["cost_per_pass_usd"] == codex["cost_per_pass_usd"] == pytest.approx(0.002)
    assert ours["cost_usd"] == pytest.approx(3.5) and ours["retried_cost_usd"] == \
        pytest.approx(3.0)
    assert codex["retried_cost_usd"] == pytest.approx(0.0)


@pytest.mark.parametrize("name, rate", [("fh-v1-tb21", "first_valid_pass_rate"),
                                        ("tb2", "pass_rate")])
def test_the_targets_selection_names_the_rate_it_publishes(tmp_path, name, rate):
    job = tmp_path / "jobs" / "j1"
    _trial(job, "a__1", "a", reward=0.0, started="2026-11-12T01:00:00Z")
    _trial(job, "a__2", "a", reward=1.0, started="2026-11-12T02:00:00Z")
    target = _target(name)
    ours = fe.summarize(job, {"target_path": target["path"]})["agents"]["openprogram"]
    assert ours["target_rate"] == rate
    assert ours[rate] == (0.0 if rate == "first_valid_pass_rate" else 0.5)


def test_a_target_with_an_unknown_selection_is_refused(tmp_path):
    data = json.loads((fe.TARGETS_DIR / "fh-v1-tb21.json").read_text(encoding="utf-8"))
    data["selection"] = "best_of_three"
    path = tmp_path / "t.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(fe.EvalError, match="selection"):
        fe.load_target(str(path))


def test_a_provider_failure_inside_the_turn_is_not_said_to_be_before_it():
    failed = {"verifier_result": None,
              "exception_info": {"exception_type": "NonZeroAgentExitCodeError"},
              "agent_result": {"metadata": {"openprogram": {
                  "ended": "failed", "error": "Error code: 429 - rate limit reached"}}}}
    assert fe.classify(failed) == ("infrastructure", "rate_limit in the turn")


def test_the_fh_cap_bounds_a_tasks_whole_run_not_harbors_agent_timeout(tmp_path):
    """run-trials.sh wraps one task's whole `harbor run -r 2` (setup, every
    attempt Harbor retries, the verifier) in `timeout 5400`; Harbor keeps
    each task's own agent timeout (600-1800 s for these 21 tasks). So the
    job applies no agent timeout from the target, and applying none is no
    departure."""
    target = _target()
    assert all("max_timeout_sec" not in agent for agent in _config(tmp_path)["agents"])
    assert fe.deviations(target, environment="runta") == []
    assert "whole run of one task" in " ".join(target["sources"])


def test_the_markdown_names_the_published_rate_and_the_retried_cost(tmp_path):
    from scripts.benchmarks.harbor_agent import commands

    job = tmp_path / "jobs" / "j1"
    _trial(job, "a__1", "a", reward=1.0, cost=0.5)
    for attempt, cost in (("x", 3.0), ("y", 0.5)):
        commands.append_attempt(fe.ledger_path(job), trial="a__1", attempt=attempt,
                                fields={"cost_usd": cost})
    text = fe.summary_markdown(fe.summarize(job, {"target_path": _target()["path"]}))
    assert "The target publishes the 'First valid' rate" in text
    assert "| $3.50 ($3.00 retried) |" in text


# ------------------------------------------------- reasoning effort (G1, G3)

def test_an_effort_override_reaches_both_agents_and_is_a_departure(tmp_path):
    ours, control = _config(tmp_path, effort="high")["agents"]
    assert ours["kwargs"]["reasoning_effort"] == control["kwargs"]["reasoning_effort"] == "high"
    assert fe.deviations(_target(), environment="runta", effort="high") == [
        "reasoning effort high instead of medium"]
    # The target's own effort named again is no departure.
    assert fe.deviations(_target(), environment="runta", effort="medium") == []


def test_effort_none_is_the_same_request_on_each_side():
    # OpenProgram's none sends effort "none", as Codex does: only the
    # effort itself departs.
    assert fe.deviations(_target(), environment="runta", effort="none") == [
        "reasoning effort none instead of medium"]
    assert not hasattr(fe, "NONE_EFFORT_DEVIATION")


def test_a_build_without_the_effort_flag_is_given_none_and_is_not_comparable(tmp_path):
    ours, control = _config(tmp_path, effort_supported=False)["agents"]
    assert "reasoning_effort" not in ours["kwargs"]
    assert control["kwargs"]["reasoning_effort"] == "medium"
    assert fe.deviations(_target(), environment="runta", effort_supported=False) == [
        fe.OPENPROGRAM_EFFORT_DEVIATION]


def test_an_unknown_effort_is_refused(tmp_path):
    with pytest.raises(fe.EvalError, match="reasoning effort"):
        _config(tmp_path, effort="extreme")


def _wheel(path: Path, parser_text: str | None) -> Path:
    import zipfile

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("openprogram/__init__.py", "")
        if parser_text is not None:
            archive.writestr("openprogram/interfaces/cli/parser.py", parser_text)
    return path


def test_the_wheel_says_whether_its_print_takes_an_effort(tmp_path):
    with_flag = _wheel(tmp_path / "a.whl",
                       'parser.add_argument("--effort", dest="print_effort")\n')
    without = _wheel(tmp_path / "b.whl", 'parser.add_argument("--model")\n')
    no_parser = _wheel(tmp_path / "c.whl", None)
    assert fe.wheel_supports_effort(with_flag)
    assert not fe.wheel_supports_effort(without)
    assert not fe.wheel_supports_effort(no_parser)
    assert not fe.wheel_supports_effort(tmp_path / "missing.whl")


def test_this_checkouts_parser_declares_the_effort_flag():
    """The check above reads the parser's own text; it must still find the
    flag in the file a wheel of this checkout ships."""
    text = (fe.REPO / fe._CLI_PARSER_IN_WHEEL).read_text(encoding="utf-8")
    assert '"--effort"' in text


def test_the_run_command_offers_an_effort_override():
    args = fe.parser().parse_args(["run", "--effort", "low"])
    assert args.effort == "low"
    assert fe.parser().parse_args(["run"]).effort is None


# ------------------------------------------------------ OPENPROGRAM_HOME (A12)

def test_profile_dirs_follow_openprogram_home(tmp_path, monkeypatch):
    monkeypatch.setattr(fe.Path, "home", classmethod(lambda cls: tmp_path / "real"))
    monkeypatch.delenv(fe.OPENPROGRAM_HOME_ENV, raising=False)
    assert fe.profile_dir("default") == tmp_path / "real" / ".openprogram"
    assert fe.profile_dir("dev") == tmp_path / "real" / ".openprogram-dev"
    monkeypatch.setenv(fe.OPENPROGRAM_HOME_ENV, str(tmp_path / "iso" / "state"))
    assert fe.profile_dir("default") == tmp_path / "iso" / "state"
    assert fe.profile_dir("") == tmp_path / "iso" / "state"
    assert fe.profile_dir("dev") == tmp_path / "iso" / "state-dev"


def test_the_stored_key_is_read_under_openprogram_home_not_the_real_home(tmp_path, monkeypatch):
    monkeypatch.setattr(fe.Path, "home", classmethod(lambda cls: tmp_path / "real"))
    _store(tmp_path / "real", ".openprogram", "openai", {
        "credentials": [{"credential_id": "r", "payload": {"kind": "api_key",
                                                           "auth_value": "real"}}]})
    _store(tmp_path, "iso", "openai", {
        "credentials": [{"credential_id": "i", "payload": {"kind": "api_key",
                                                           "auth_value": "isolated"}}]})
    monkeypatch.setenv(fe.OPENPROGRAM_HOME_ENV, str(tmp_path / "iso"))
    assert fe.stored_api_key("default", "openai") == "isolated"


# ------------------------------------------------------------ web tools (E6)

def test_a_target_that_turns_web_search_off_does_so_for_both_agents(tmp_path):
    target = {**_target(), "disable_web_search": True}
    ours, control = _config(tmp_path, target)["agents"]
    assert ours["kwargs"]["disable_web_search"] is control["kwargs"]["disable_web_search"] is True


# ---------------------------------------------------------------- pilot (E7)

def test_the_pilot_is_the_targets_eight_tasks_in_three_tiers(tmp_path):
    target = _target()
    assert fe.pilot_tasks(target) == [
        "kv-store-grpc", "dna-insert", "extract-elf", "chess-best-move", "gcode-to-text",
        "regex-log", "sanitize-git-repo", "build-cython-ext"]
    config = _config(tmp_path, pilot=True)
    assert config["datasets"][0]["task_names"] == fe.pilot_tasks(target)
    assert fe.deviations(target, environment="runta", pilot=True) == [fe.PILOT_DEVIATION]
    assert fe.parser().parse_args(["run", "--pilot"]).pilot is True


def test_a_pilot_task_outside_the_target_is_refused(tmp_path):
    data = json.loads((fe.TARGETS_DIR / "fh-v1-tb21.json").read_text(encoding="utf-8"))
    data["pilot"]["tiers"][0]["tasks"].append("not-a-task")
    path = tmp_path / "t.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(fe.EvalError, match="not-a-task"):
        fe.load_target(str(path))


# ------------------------------------------------------- launcher retries (E8)

def _retry_job(tmp_path):
    from scripts.benchmarks.harbor_agent import commands

    job = tmp_path / "jobs" / "j1"
    ledger = fe.ledger_path(job)
    # Ours: a__1 rate limited (retried), b__1 failed on its own (scored).
    _trial(job, "a__1", "a", exception="ApiRateLimitError", cost=0.2)
    commands.append_attempt(ledger, trial="a__1", attempt="x", error_kind="rate_limit",
                            fields={"cost_usd": 0.2})
    _trial(job, "b__1", "b", reward=0.0, cost=0.1)
    commands.append_attempt(ledger, trial="b__1", attempt="w", fields={"cost_usd": 0.1})
    # Codex: a__2 crashed (retried, held against it), c__1 timed out (not retried).
    _trial(job, "a__2", "a", agent="codex", exception="NonZeroAgentExitCodeError",
           reward=0.0, cost=0.3)
    _trial(job, "c__1", "c", agent="codex", exception="AgentTimeoutError", reward=0.0,
           cost=0.1)
    calls = []

    def harbor(command, *, env, job_dir, budget_usd, outcome=None, **_):
        calls.append(command)
        # ``harbor job resume`` runs every trial without a result again.
        if not (job / "a__1").exists():
            _trial(job, "a__1", "a", reward=1.0, cost=0.5, started="2026-11-12T02:00:00Z")
            commands.append_attempt(ledger, trial="a__1", attempt="y",
                                    fields={"cost_usd": 0.5})
        if not (job / "a__2").exists():
            _trial(job, "a__2", "a", agent="codex", reward=1.0, cost=0.4,
                   started="2026-11-12T02:00:00Z")
        return 0

    return job, harbor, calls


def test_the_launcher_retries_after_harbor_and_keeps_every_attempt(tmp_path, monkeypatch):
    job, harbor, calls = _retry_job(tmp_path)
    monkeypatch.setattr(fe, "run_harbor", harbor)
    run_info = {"retry_rounds": 0}

    status = fe.retry_rounds(job, run_info, harbor="harbor", env={}, budget_usd=None,
                             policy=fe.retry_policy(_target()), status=0, outcome={})

    assert status == 0 and run_info["retry_rounds"] == 1
    assert calls == [["harbor", "job", "resume", "-p", str(job)]]
    archive = fe.retried_dir(job)
    assert archive == tmp_path / "jobs" / "j1.retried"
    for trial in ("a__1", "a__2"):
        assert json.loads((archive / trial / "1" / "result.json").read_text())["trial_name"] \
            == trial
    assert not (archive / "c__1").exists() and not (archive / "b__1").exists()

    agents = fe.summarize(job, run_info)["agents"]
    ours, codex = agents["openprogram"], agents["codex"]
    # Ours: a passed after a retry for the provider's fault, b failed.
    assert ours["first_attempt_pass_rate"] == 0.5
    assert ours["retried_kinds"] == {"rate_limit": 1}
    # Codex's earlier attempts are kept too: its crash still counts against it.
    assert codex["first_attempt_pass_rate"] == 0.0
    assert codex["retries"] == 1 and codex["retried_kinds"] == {"agent": 1}
    assert codex["cost_usd"] == pytest.approx(0.3 + 0.4 + 0.1)
    assert codex["retried_cost_usd"] == pytest.approx(0.3)


def test_no_retry_round_once_the_budget_is_spent_or_the_job_was_stopped(tmp_path,
                                                                         monkeypatch):
    job, harbor, calls = _retry_job(tmp_path)
    monkeypatch.setattr(fe, "run_harbor", harbor)
    policy = fe.retry_policy(_target())
    run_info = {"retry_rounds": 0}
    fe.retry_rounds(job, run_info, harbor="harbor", env={}, budget_usd=0.5, policy=policy,
                    status=3, outcome={})
    fe.retry_rounds(job, run_info, harbor="harbor", env={}, budget_usd=None, policy=policy,
                    status=3, outcome={"stopped": "interrupted"})
    assert calls == [] and run_info["retry_rounds"] == 0
    assert (job / "a__1").is_dir()


def test_the_retry_policy_follows_harbors_defaults():
    policy = fe.retry_policy(_target())
    assert policy["max_retries"] == 2 and policy["include"] is None
    assert "AgentTimeoutError" in policy["exclude"]
    assert fe.retryable({"exception_info": {"exception_type": "ApiRateLimitError"}},
                        None, policy)
    assert not fe.retryable({"exception_info": {"exception_type": "CancelledError"}},
                            None, policy)
    assert not fe.retryable({"exception_info": None}, None, policy)


def test_rerun_infra_moves_trials_aside_rather_than_deleting_them(tmp_path):
    job = tmp_path / "jobs" / "j1"
    _trial(job, "mailman__a", "mailman", exception="ApiResponseStalledError", cost=0.3)
    assert fe.drop_infrastructure_trials(job) == ["mailman__a"]
    _trial(job, "mailman__a", "mailman", exception="ApiResponseStalledError", cost=0.3,
           started="2026-11-12T02:00:00Z")
    fe.drop_infrastructure_trials(job)
    kept = fe.retried_dir(job) / "mailman__a"
    assert sorted(p.name for p in kept.iterdir()) == ["1", "2"]


# ------------------------------------------------------------ build info (E4)

def test_a_wheel_names_the_commit_it_was_built_from(tmp_path, monkeypatch):
    import zipfile

    commit = "0123456789abcdef0123456789abcdef01234567"
    seen = {}

    def fake_run(argv, *args, **kwargs):
        if argv[0] == "git":
            return type("Done", (), {"stdout": b""})()
        if argv[0] == "tar":
            (Path(argv[3]) / "openprogram").mkdir(parents=True)
        elif argv[:2] == ["uv", "build"]:
            src, dist = Path(argv[3]), Path(argv[5])
            stamp = src / fe.BUILD_INFO_IN_WHEEL
            seen["stamped_before_build"] = stamp.is_file()
            dist.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(dist / "openprogram-1.0-py3-none-any.whl", "w") as archive:
                archive.writestr(fe.BUILD_INFO_IN_WHEEL, stamp.read_text())
        elif argv[:2] == ["uv", "export"]:
            Path(argv[-1]).write_text("")
        return type("Done", (), {"stdout": b"", "returncode": 0})()

    monkeypatch.setattr(fe.subprocess, "run", fake_run)
    info = fe.build_wheel(commit, tmp_path / "builds")

    assert seen["stamped_before_build"] and info["build_info"] is True
    assert fe.wheel_build_info(Path(info["wheel"])) == commit
    # A cached build is reused; one made before the stamp is built again.
    assert fe.build_wheel(commit, tmp_path / "builds") == info
    stale = {**info}
    del stale["build_info"]
    (tmp_path / "builds" / commit / "build.json").write_text(json.dumps(stale))
    seen.clear()
    fe.build_wheel(commit, tmp_path / "builds")
    assert seen["stamped_before_build"]


def test_a_wheel_without_build_info_names_no_commit(tmp_path):
    assert fe.wheel_build_info(_wheel(tmp_path / "a.whl", None)) is None
    assert fe.wheel_build_info(tmp_path / "missing.whl") is None
