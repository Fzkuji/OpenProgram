"""Run a Harbor evaluation of OpenProgram against a written target, beside a control.

    .venv/bin/python -m scripts.benchmarks.frontier_eval run --target fh-v1-tb21
    .venv/bin/python -m scripts.benchmarks.frontier_eval resume --job op-fh-v1-tb21
    .venv/bin/python -m scripts.benchmarks.frontier_eval summary --job op-fh-v1-tb21
    .venv/bin/python -m scripts.benchmarks.frontier_eval check --target fh-v1-tb21

A target (``targets/<name>.json`` beside this file) is the whole method of
one evaluation and where each part of it was published: the dataset and its
version, the tasks, attempts per task, the retry policy, the wall-clock cap
per trial, the model, the reasoning effort, the control harness and its
pinned version, the Harbor version and the prices tokens are counted at.
The launcher runs exactly that. Comparability comes from the control:
OpenProgram and the control harness run in the same job on the same tasks,
model, effort and network. Any departure (a subset of tasks, fewer attempts,
a timeout cap, no control, an environment the target does not count as
comparable) is written into the run's record with
``methodology_comparable: false`` and the reason.

``run`` checks the prerequisites (Harbor's version, the container
environment, the provider key, free disk), builds a wheel of one commit of
this repository (``--ref``, default ``stable``) with the dependency
versions of its ``uv.lock`` and the commit written into it
(``openprogram/_build_info.py``), writes the job configuration and starts
``harbor run -c``. Every container installs that same wheel, so an
unpublished local commit can be evaluated. ``resume`` continues an
interrupted job with ``harbor job resume`` on the same wheel.

Retries are the launcher's, not Harbor's (Harbor deletes a trial's folder
before it retries it): the job runs with ``max_retries: 0``, and once
Harbor exits the launcher moves every trial the target's retry policy
retries to ``jobs/<job>.retried/<trial>/<n>/``, writes its line in the cost
ledger and runs ``harbor job resume``, up to the target's ``max_retries``
rounds. Every attempt of both agents stays on disk, so first-attempt pass
rates are counted the same way for both.

Both stop the job once the cost of every attempt so far reaches
``--budget-usd`` (retried attempts included, from the job's cost ledger),
and write a summary when Harbor exits: per agent, pass rates (all
attempts, first attempts, the first valid attempt of each task), retries,
tokens, cost as reported and at the target's prices, cost per pass,
container hours; and the paired comparison with the control.

The provider key comes from the environment variable Harbor reads for
the model (``OPENAI_API_KEY`` for ``openai/...``), or, with
``--key-profile``, from that OpenProgram profile's stored credential. It is
passed to Harbor's process only and never printed.

Files live under ``--root`` (default ``~/.openprogram-eval``): ``builds/<commit>/``
holds a wheel, its ``constraints.txt`` and ``build.json``; ``jobs/<job>/``
is Harbor's job directory, with ``openprogram-summary.json`` and
``openprogram-summary.md`` beside Harbor's own files; ``jobs/<job>.config.json``
is the job configuration Harbor ran, ``jobs/<job>.run.json`` the target,
commit, wheel hash, departures, budget and retry rounds of the run,
``jobs/<job>.attempts.jsonl`` the tokens and cost of every attempt, and
``jobs/<job>.retried/`` the attempts that were retried.

Nothing here imports Harbor; Harbor runs as its own program.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[2]
TARGETS_DIR = Path(__file__).with_name("targets")
DEFAULT_TARGET = "fh-v1-tb21"
DEFAULT_ROOT = Path("~/.openprogram-eval")
#: The Harbor release the adapter was written and checked against.
HARBOR_VERSION = "0.25.0"
#: Free space wanted on the disk that holds ``--root``.
MIN_FREE_GB = 20.0
RUN_FILE_SUFFIX = ".run.json"
LEDGER_SUFFIX = ".attempts.jsonl"
SUMMARY_JSON = "openprogram-summary.json"
SUMMARY_MD = "openprogram-summary.md"
AGENT_IMPORT_PATH = "scripts.benchmarks.harbor_agent.agent:OpenProgramAgent"

#: Environment variable Harbor reads the key from, by the provider in ``-m``.
KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

#: Trial errors that say nothing about the agent: the container, the
#: provider or the network failed. The summary counts them apart from the
#: agent's failures, whatever the reward; which of them Harbor retries is
#: the target's ``retry`` policy.
INFRA_ERRORS = frozenset({
    "ApiRateLimitError", "ApiInternalServerError", "ApiOverloadedError",
    "ApiConnectionError", "ApiConnectionClosedError", "ApiResponseStalledError",
    "NetworkConnectionError", "EnvironmentStartTimeoutError",
    "AgentSetupTimeoutError", "SandboxBuildFailedError", "HealthcheckError",
    "ComposeLoadError", "AgentAuthenticationError", "ApiKeyRejectedError",
    "ModelNotFoundError", "ApiUsageLimitError", "SandboxLikelyOutOfMemoryError",
    "InfrastructureTimeout",
})
#: Errors of a verifier that ran. A solution that hangs or is slow under the
#: tests ends in a timeout as well, so these count against the agent unless
#: the verifier's output shows its own setup failed.
VERIFIER_ERRORS = frozenset({
    "VerifierTimeoutError", "RewardFileNotFoundError", "RewardFileEmptyError",
    "VerifierOutputParseError",
})
#: What each environment does with a trial's network, recorded in the run.
EGRESS_POLICY = {
    "docker": "local Docker: open network, a task's allow_internet=false refused",
    "runta": "Runta: the runtime allowlist (agents and verifiers may download packages)",
}
#: A trial Harbor did not finish because the job was stopped.
CANCELLED_ERRORS = frozenset({"CancelledError", "KeyboardInterrupt"})
#: Lines a task's ``tests/test.sh`` prints when its own setup fails before
#: any test runs: most tasks download uv from GitHub at verification time.
_VERIFIER_SETUP_FAILED = re.compile(
    r"failed to download https://github\.com/astral-sh/uv"
    r"|\buvx?: command not found"
    r"|(?:error:|×) Failed to (?:fetch|download)"
    r"|Could not resolve host|Temporary failure resolving",
    re.IGNORECASE | re.MULTILINE)
#: pytest's closing line (``=== 2 passed in 0.14s ===``): the tests ran.
_TESTS_RAN = re.compile(r"^=+ .*\b(?:passed|failed|errors?|skipped|no tests ran)\b.*"
                        r" in [\d.]+s", re.MULTILINE)


class EvalError(RuntimeError):
    """A prerequisite is missing or an option cannot work."""


# ------------------------------------------------------------------ targets

#: Reasoning efforts a target can name (Harbor's Codex adapter accepts these).
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
#: Exceptions Harbor's ``RetryConfig`` does not retry unless a target says
#: otherwise (``harbor/models/job/config.py``, Harbor 0.25.0): a timeout,
#: a verifier that ran, or a cause another attempt cannot change.
HARBOR_RETRY_EXCLUDED = frozenset({
    "AgentTimeoutError", "VerifierTimeoutError", "RewardFileNotFoundError",
    "RewardFileEmptyError", "VerifierOutputParseError", "ApiUsageLimitError",
    "AgentSafetyRefusalError", "AgentAuthenticationError", "ModelNotFoundError",
    "UnsupportedModelParametersError",
})
#: Which of an agent's rates a target's published figure is, by its
#: ``selection``: the first valid attempt of each task (FrontierHarness), or
#: every scored attempt (the Terminal-Bench leaderboard).
SELECTION_RATES = {"first_valid_attempt": "first_valid_pass_rate",
                   "all_attempts": "pass_rate"}
_TARGET_KEYS = ("name", "sources", "dataset", "tasks", "attempts", "retry", "selection",
                "trial_wall_clock_cap_min", "overrides_allowed", "model",
                "reasoning_effort", "control", "harbor_version",
                "comparable_environments", "prices")
#: Why a run of an older build is not comparable: its ``openprogram
#: --print`` takes no ``--effort``, so OpenProgram runs at its Agent's
#: default (medium in a fresh home) while the control is given the
#: target's effort. :func:`wheel_supports_effort` tells the builds apart.
OPENPROGRAM_EFFORT_DEVIATION = (
    "OpenProgram's reasoning effort is not set: this build's --print takes no --effort, "
    "so it runs at the Agent default while the control runs at the target's")
#: The departure ``--pilot`` records: the target's fixed pilot tasks only.
PILOT_DEVIATION = "pilot: the target's pilot tasks only"


def load_target(name: str) -> dict[str, Any]:
    """The target named ``name`` (in :data:`TARGETS_DIR`) or at that path,
    with its ``path`` and the ``sha256`` of its bytes."""
    path = Path(name) if name.endswith(".json") else TARGETS_DIR / f"{name}.json"
    try:
        raw = path.read_bytes()
        target = json.loads(raw)
    except (OSError, ValueError) as exc:
        known = ", ".join(sorted(p.stem for p in TARGETS_DIR.glob("*.json")))
        raise EvalError(f"no readable target {name!r} (known: {known})") from exc
    missing = [key for key in _TARGET_KEYS if key not in target]
    if missing:
        raise EvalError(f"target {path.name} lacks {', '.join(missing)}")
    if target["reasoning_effort"] not in EFFORTS:
        raise EvalError(f"target {path.name}: reasoning_effort must be one of {EFFORTS}")
    if target["selection"] not in SELECTION_RATES:
        raise EvalError(f"target {path.name}: selection must be one of "
                        f"{tuple(SELECTION_RATES)}")
    if target["model"] not in (target["prices"].get("models") or {}):
        raise EvalError(f"target {path.name} has no price for {target['model']}")
    unknown = sorted(set(pilot_tasks(target)) - set(target["tasks"] or ()))
    if unknown and target["tasks"]:
        raise EvalError(f"target {path.name}: pilot tasks not among its tasks: "
                        f"{', '.join(unknown)}")
    return {**target, "path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def pilot_tasks(target: dict[str, Any]) -> list[str]:
    """The target's pilot tasks (``pilot``): every tier's, in order. A
    target without a pilot has none."""
    pilot = target.get("pilot") or {}
    return [task for tier in pilot.get("tiers") or [] for task in tier.get("tasks") or []]


def retry_policy(target: dict[str, Any]) -> dict[str, Any]:
    """How many rounds of retries the launcher runs (``max_retries``,
    default 2) and which trials they take: Harbor's ``RetryConfig`` with
    the target's ``include_exceptions`` / ``exclude_exceptions``."""
    retry = target.get("retry") or {}
    include = retry.get("include_exceptions")
    exclude = retry.get("exclude_exceptions")
    return {
        "max_retries": int(retry.get("max_retries", 2)),
        "include": None if include is None else frozenset(include),
        "exclude": HARBOR_RETRY_EXCLUDED if exclude is None else frozenset(exclude),
    }


def deviations(target: dict[str, Any], *, environment: str, model: str | None = None,
               attempts: int | None = None, tasks: Iterable[str] = (),
               exclude_tasks: Iterable[str] = (), n_tasks: int | None = None,
               agent_timeout_cap_min: float | None = None, control: bool = True,
               agent_env: dict[str, str] | None = None,
               harbor_found: str | None = None, effort: str | None = None,
               effort_supported: bool = True, pilot: bool = False) -> list[str]:
    """Every way a run departs from its target; none means comparable.

    ``effort`` replaces the target's reasoning effort for both agents;
    ``effort_supported`` is false for a build whose ``--print`` takes no
    ``--effort`` (:func:`wheel_supports_effort`). ``pilot`` runs the
    target's pilot tasks (:data:`PILOT_DEVIATION`)."""
    found = [] if effort_supported else [OPENPROGRAM_EFFORT_DEVIATION]
    if effort and effort != target["reasoning_effort"]:
        found.append(f"reasoning effort {effort} instead of {target['reasoning_effort']}")
    if pilot:
        found.append(PILOT_DEVIATION)
    if environment not in target["comparable_environments"]:
        found.append(f"environment {environment} is not one the target counts as "
                     f"comparable ({', '.join(target['comparable_environments'])})")
    if model and model != target["model"]:
        found.append(f"model {model} instead of {target['model']}")
    if attempts is not None and attempts != target["attempts"]:
        found.append(f"{attempts} attempts per task instead of {target['attempts']}")
    if not pilot and (any(t.strip() for t in tasks) or any(t.strip() for t in exclude_tasks)
                      or n_tasks):
        found.append("a subset of the target's tasks")
    if agent_timeout_cap_min is not None:
        found.append(f"agent timeout capped at {agent_timeout_cap_min:g} minutes")
    if not control:
        found.append("no control harness in the job")
    if agent_env:
        found.append(f"agent environment set: {', '.join(sorted(agent_env))}")
    if harbor_found and harbor_found != target["harbor_version"]:
        found.append(f"Harbor {harbor_found} instead of {target['harbor_version']}")
    return found


# --------------------------------------------------------------- job config

def job_config(target: dict[str, Any], *, job_name: str, jobs_dir: Path,
               wheel: Path, constraints: Path, model: str | None = None,
               attempts: int | None = None, concurrency: int | None = None,
               environment: str | None = None, tasks: Iterable[str] = (),
               exclude_tasks: Iterable[str] = (), n_tasks: int | None = None,
               agent_timeout_cap_min: float | None = None,
               provider: str | None = None, install_only: bool = False,
               bundle: Path | None = None, control: bool = True,
               agent_env: dict[str, str] | None = None, effort: str | None = None,
               effort_supported: bool = True, pilot: bool = False) -> dict[str, Any]:
    """The Harbor job configuration for one run of ``target``.

    OpenProgram's agent and, unless ``control`` is false, the target's
    control harness at its pinned version, both on the same model, tasks
    and reasoning effort (``effort``, else the target's). A build whose
    ``--print`` takes no ``--effort`` (``effort_supported`` false) is given
    none and runs at its Agent's default. Task names get the dataset's
    ``task_prefix``. ``agent_timeout_cap_min`` bounds every task's agent
    timeout for both agents (Harbor's ``max_timeout_sec``); a target with
    ``overrides_allowed`` false refuses it. OpenProgram's agent appends
    every attempt's cost to :func:`ledger_path` of the job.

    Harbor retries nothing (``max_retries: 0``): the launcher does
    (:func:`retry_round`), so every attempt is kept. The target's
    ``disable_web_search`` (default false) goes to both agents alike, and
    only when true, so a task's network policy still applies to both as it
    does in the published runs. ``pilot`` runs :func:`pilot_tasks`.
    """
    if agent_timeout_cap_min is not None and not target["overrides_allowed"]:
        raise EvalError(f"target {target['name']} allows no timeout or resource overrides")
    model = model or target["model"]
    effort = effort or target["reasoning_effort"]
    if effort not in EFFORTS:
        raise EvalError(f"reasoning effort must be one of {EFFORTS}, got {effort!r}")
    dataset = {key: value for key, value in target["dataset"].items() if key != "task_prefix"}
    prefix = target["dataset"].get("task_prefix", "")
    chosen = [_task_name(t, prefix)
              for t in (pilot_tasks(target) if pilot else tasks) if t.strip()]
    if chosen:
        dataset["task_names"] = chosen
    elif target["tasks"]:
        dataset["task_names"] = [_task_name(t, prefix) for t in target["tasks"]]
    excluded = [_task_name(t, prefix) for t in exclude_tasks if t.strip()]
    if excluded:
        dataset["exclude_task_names"] = excluded
    if n_tasks is not None:
        dataset["n_tasks"] = int(n_tasks)
    ours: dict[str, Any] = {
        "import_path": AGENT_IMPORT_PATH, "model_name": model,
        "kwargs": {"wheel": str(wheel), "constraints": str(constraints),
                   "cost_ledger": str(ledger_path(Path(jobs_dir) / job_name))},
    }
    if bundle is not None:
        ours["kwargs"]["bundle"] = str(bundle)
    if provider:
        ours["kwargs"]["provider"] = provider
    if effort_supported:
        ours["kwargs"]["reasoning_effort"] = effort
    if agent_env:
        ours["env"] = dict(agent_env)
    agents = [ours]
    if control:
        agents.append({"name": target["control"]["agent"], "model_name": model,
                       "kwargs": {"version": target["control"]["version"],
                                  "reasoning_effort": effort}})
    if target.get("disable_web_search"):
        for agent in agents:
            agent["kwargs"]["disable_web_search"] = True
    if agent_timeout_cap_min is not None:
        for agent in agents:
            agent["max_timeout_sec"] = float(agent_timeout_cap_min) * 60.0
    config: dict[str, Any] = {
        "job_name": job_name, "jobs_dir": str(jobs_dir),
        "n_attempts": int(attempts if attempts is not None else target["attempts"]),
        "n_concurrent_trials": int(concurrency or 4),
        "quiet": True,
        **(target.get("harbor") or {}),
        "retry": {**target["retry"], "max_retries": 0},
        "environment": {"type": environment or "docker"},
        "agents": agents,
        "datasets": [dataset],
    }
    if install_only:
        config["install_only"] = True
    return config


def _task_name(name: str, prefix: str) -> str:
    name = name.strip()
    return name if not prefix or name.startswith(prefix) else f"{prefix}{name}"


def key_env_name(model: str, override: str | None = None) -> str:
    """The variable Harbor reads the provider key from for ``model``."""
    if override:
        return override
    provider = model.partition("/")[0]
    try:
        return KEY_ENV[provider]
    except KeyError:
        raise EvalError(
            f"no known key variable for provider {provider!r}; pass --key-env"
        ) from None


#: The variable that names another base directory for OpenProgram's state
#: (tests and CI set it instead of moving the home directory).
OPENPROGRAM_HOME_ENV = "OPENPROGRAM_HOME"


def profile_dir(profile: str) -> Path:
    """OpenProgram's data directory for a profile (``default`` is the stable one).

    Under ``OPENPROGRAM_HOME`` the default profile's directory is that
    directory and a named profile's is ``<it>-<profile>`` beside it, so a
    launcher started with it set never reads the real profiles in the
    home directory. Otherwise ``~/.openprogram`` and ``~/.openprogram-<profile>``.
    """
    base = os.environ.get(OPENPROGRAM_HOME_ENV, "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".openprogram"
    return root if profile in ("", "default") else root.with_name(f"{root.name}-{profile}")


def stored_api_key(profile: str, provider: str) -> str:
    """The active API key stored for ``provider`` in an OpenProgram profile.

    Reads ``auth/<provider>/default.json`` and returns the active
    credential's key; never logs it.
    """
    path = profile_dir(profile) / "auth" / provider / "default.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvalError(f"no stored credential for {provider} in profile {profile!r}") from exc
    credentials = [c for c in data.get("credentials") or [] if isinstance(c, dict)]
    active = data.get("active_credential_id")
    credentials.sort(key=lambda c: c.get("credential_id") != active)
    for credential in credentials:
        payload = credential.get("payload") or {}
        value = payload.get("auth_value")
        if payload.get("kind") == "api_key" and isinstance(value, str) and value.strip():
            return value.strip()
    raise EvalError(f"profile {profile!r} holds no API key for {provider}")


# ------------------------------------------------------------ prerequisites

def harbor_version(harbor: str) -> str:
    out = subprocess.run([harbor, "--version"], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise EvalError(f"`{harbor} --version` failed: {out.stderr.strip()[:200]}")
    return out.stdout.strip().split()[-1] if out.stdout.strip() else ""


def docker_facts() -> dict[str, Any]:
    out = subprocess.run(["docker", "info", "--format", "{{json .}}"],
                         capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise EvalError("Docker is not running (`docker info` failed)")
    info = json.loads(out.stdout)
    return {"server_version": info.get("ServerVersion"), "cpus": info.get("NCPU"),
            "memory_gb": round((info.get("MemTotal") or 0) / 2**30, 1),
            "architecture": info.get("Architecture")}


def check_prerequisites(*, harbor: str, environment: str, key_present: bool,
                        root: Path, allow_harbor_version: bool = False,
                        min_free_gb: float = MIN_FREE_GB) -> list[str]:
    """Problems that stop a run (raised) and warnings (returned)."""
    warnings: list[str] = []
    if shutil.which(harbor) is None and not Path(harbor).is_file():
        raise EvalError(f"harbor not found ({harbor}); install it with "
                        f"`uv tool install \"harbor[runta]=={HARBOR_VERSION}\"` or pass --harbor")
    version = harbor_version(harbor)
    if version != HARBOR_VERSION:
        message = f"Harbor {version} found; the adapter is checked against {HARBOR_VERSION}"
        if not allow_harbor_version:
            raise EvalError(message + " (pass --allow-harbor-version to run anyway)")
        warnings.append(message)
    if not key_present:
        raise EvalError("no provider key: export it or pass --key-profile")
    if environment == "docker":
        facts = docker_facts()
        if facts["architecture"] not in ("x86_64", "amd64"):
            warnings.append(
                f"Docker runs on {facts['architecture']}: the x86_64 task images run "
                "emulated, slower than the tasks were timed for")
        warnings.append(f"Docker has {facts['cpus']} CPUs and {facts['memory_gb']} GB; "
                        "tasks asking for more get what Docker has")
    root.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(root).free / 2**30
    if free_gb < min_free_gb:
        raise EvalError(f"{free_gb:.0f} GB free under {root}; at least {min_free_gb:.0f} GB wanted")
    return warnings


# -------------------------------------------------------------------- build

def resolve_commit(ref: str) -> str:
    out = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--verify", f"{ref}^{{commit}}"],
                         capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise EvalError(f"git ref not found: {ref}")
    return out.stdout.strip()


def build_wheel(commit: str, builds_dir: Path, *, ref: str = "") -> dict[str, Any]:
    """A wheel of ``commit`` and its locked constraints, built once.

    The source is ``git archive`` of the commit, so uncommitted files never
    reach it and the checkout is not touched. Before ``uv build`` the
    commit and the build time are written into the source tree as
    ``openprogram/_build_info.py`` (:func:`stamp_build_info`), so the
    installed ``openprogram --version`` and every run header name the
    commit without the launcher's records beside them. A cached build made
    before that is built again.
    """
    target = builds_dir / commit
    info_path = target / "build.json"
    if info_path.is_file():
        info = json.loads(info_path.read_text(encoding="utf-8"))
        if (Path(info["wheel"]).is_file() and sha256(Path(info["wheel"])) == info["wheel_sha256"]
                and info.get("build_info")):
            return info
    builds_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=builds_dir, prefix=".build-") as tmp:
        src = Path(tmp) / "src"
        dist = Path(tmp) / "dist"
        src.mkdir()
        archive = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", commit],
                                 capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(src)], input=archive, check=True)
        built_at = _now()
        stamp_build_info(src, commit, built_at)
        subprocess.run(["uv", "build", "--wheel", str(src), "-o", str(dist)],
                       check=True, capture_output=True)
        subprocess.run(["uv", "export", "--frozen", "--no-hashes", "--no-emit-project",
                        "--quiet", "-o", str(dist / "constraints.txt")],
                       cwd=src, check=True, capture_output=True)
        wheels = sorted(dist.glob("openprogram-*.whl"))
        if len(wheels) != 1:
            raise EvalError(f"expected one wheel, built {[w.name for w in wheels]}")
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(dist), str(target))
    wheel = target / wheels[0].name
    if not wheel_build_info(wheel):
        raise EvalError(f"the wheel of {commit[:12]} does not carry {BUILD_INFO_IN_WHEEL}")
    info = {"commit": commit, "ref": ref, "wheel": str(wheel),
            "wheel_sha256": sha256(wheel),
            "constraints": str(target / "constraints.txt"),
            "built_at": built_at, "build_info": True}
    info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info


#: Where a launcher build names its commit (``interfaces/cli/build_info.py``).
BUILD_INFO_IN_WHEEL = "openprogram/_build_info.py"


def stamp_build_info(src: Path, commit: str, built_at: str) -> Path:
    """Write :data:`BUILD_INFO_IN_WHEEL` into an unpacked source tree."""
    from openprogram.interfaces.cli.build_info import module_source

    path = Path(src) / BUILD_INFO_IN_WHEEL
    path.write_text(module_source(commit, built_at), encoding="utf-8")
    return path


def wheel_build_info(wheel: Path) -> str | None:
    """The commit a wheel says it was built from, or ``None``."""
    import zipfile

    try:
        with zipfile.ZipFile(wheel) as archive:
            text = archive.read(BUILD_INFO_IN_WHEEL).decode("utf-8", errors="replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    match = re.search(r"^COMMIT = ['\"]([0-9a-f]+)['\"]", text, re.MULTILINE)
    return match.group(1) if match else None


#: The file of a wheel that declares ``--print``'s options.
_CLI_PARSER_IN_WHEEL = "openprogram/interfaces/cli/parser.py"


def wheel_supports_effort(wheel: Path) -> bool:
    """Whether the build's ``openprogram --print`` takes ``--effort``: its
    parser declares the flag. A build without it would refuse the run
    command with a usage error, so it is given no effort instead."""
    import zipfile

    try:
        with zipfile.ZipFile(wheel) as archive:
            text = archive.read(_CLI_PARSER_IN_WHEEL).decode("utf-8", errors="replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return False
    return '"--effort"' in text


#: Builder image for the prebuilt install: Debian 11, glibc 2.31, older than
#: every task image, so the wheels uv picks there load in all of them.
BUNDLE_BUILDER = "buildpack-deps:bullseye-curl"
BUNDLE_NAME = "openprogram-bundle-x86_64.tar.gz"


def build_bundle(build: dict[str, Any], *, builder: str = BUNDLE_BUILDER) -> dict[str, Any]:
    """Install the build once in a ``linux/amd64`` container and pack it.

    The container runs the adapter's own install command on the wheel and
    constraints, so the bundle is exactly what a trial would have
    installed; each trial then unpacks it offline. Files go in and out with
    ``docker cp``, which works where bind mounts are not shared.
    """
    from scripts.benchmarks.harbor_agent import commands

    target = Path(build["wheel"]).parent
    bundle = target / BUNDLE_NAME
    if build.get("bundle_sha256") and bundle.is_file() and sha256(bundle) == build["bundle_sha256"]:
        return build
    wheel = Path(build["wheel"])
    remote_wheel = commands.wheel_remote_path(wheel.name)
    script = (commands.install_command(remote_wheel)
              + f" && tar -C / -czf /tmp/{BUNDLE_NAME} installed-agent/openprogram"
              + " && getconf GNU_LIBC_VERSION")
    name = f"openprogram-bundle-{build['commit'][:12]}-{os.getpid()}"
    subprocess.run(["docker", "pull", "--platform", "linux/amd64", "-q", builder],
                   check=True, capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", name, "--platform", "linux/amd64",
                    builder, "sleep", "infinity"], check=True, capture_output=True)
    try:
        subprocess.run(["docker", "exec", name, "mkdir", "-p", str(commands.WHEEL_DIR)],
                       check=True, capture_output=True)
        subprocess.run(["docker", "cp", str(wheel), f"{name}:{remote_wheel}"],
                       check=True, capture_output=True)
        subprocess.run(["docker", "cp", build["constraints"],
                        f"{name}:{commands.CONSTRAINTS_PATH}"], check=True, capture_output=True)
        run = subprocess.run(["docker", "exec", name, "sh", "-c", script],
                             capture_output=True, text=True, check=False)
        if run.returncode != 0:
            raise EvalError("building the install bundle failed:\n" + run.stderr[-2000:])
        glibc = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else ""
        partial = bundle.with_suffix(".partial")
        subprocess.run(["docker", "cp", f"{name}:/tmp/{BUNDLE_NAME}", str(partial)],
                       check=True, capture_output=True)
        partial.replace(bundle)
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
    digest = subprocess.run(["docker", "image", "inspect", "--format",
                             "{{index .RepoDigests 0}}", builder],
                            capture_output=True, text=True, check=False).stdout.strip()
    build = {**build, "bundle": str(bundle), "bundle_sha256": sha256(bundle),
             "bundle_builder": digest or builder, "bundle_glibc": glibc}
    (target / "build.json").write_text(json.dumps(build, indent=2) + "\n", encoding="utf-8")
    return build


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ------------------------------------------------------------------ summary

def trial_results(job_dir: Path) -> list[dict[str, Any]]:
    """Every finished trial's ``result.json`` in a job directory."""
    return [result for _, result in _finished_trials(job_dir)]


def _finished_trials(job_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    """``(trial folder, result)`` of every finished trial."""
    found = []
    for path in sorted(job_dir.glob("*/result.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("trial_name") and data.get("finished_at"):
            found.append((path.parent, data))
    return found


def ledger_path(job_dir: Path) -> Path:
    """The cost ledger every attempt appends to, beside the job directory.

    Harbor deletes a trial's directory before retrying it, and ``resume
    --rerun-infra`` deletes the trials it reruns; the ledger keeps what
    those attempts cost.
    """
    return job_dir.parent / f"{job_dir.name}{LEDGER_SUFFIX}"


def _result_attempt_id(result: dict[str, Any]) -> str:
    """The ledger id :func:`drop_infrastructure_trials` gives a trial result:
    one attempt of a trial name, told apart by when it started."""
    return f"result:{result.get('trial_name')}:{result.get('started_at')}"


def _kept_attempt(result: dict[str, Any], attempts: dict[str, dict[str, Any]],
                  ours_last: dict[str, str]) -> str | None:
    """The ledger line of the attempt a present trial result is, or ``None``.

    A line the drop wrote for exactly this attempt; else, for OpenProgram,
    the last line its agent wrote for the trial (it writes one per attempt,
    the present one last). A control writes none, so its present result
    has a line only when it was dropped and not rerun.
    """
    own = _result_attempt_id(result)
    if own in attempts:
        return own
    if _agent_name(result) == OUR_AGENT:
        return ours_last.get(str(result.get("trial_name")))
    return None


def _ledger(job_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """The ledger's attempts by id, and OpenProgram's last own line per trial."""
    from scripts.benchmarks.harbor_agent import commands

    attempts = commands.read_attempts(ledger_path(job_dir))
    ours_last: dict[str, str] = {}
    for attempt_id, line in attempts.items():
        if not attempt_id.startswith("result:") and line.get("agent", OUR_AGENT) == OUR_AGENT:
            ours_last[str(line["trial"])] = attempt_id
    return attempts, ours_last


def _costs(job_dir: Path, trials: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Cost of every attempt: the ledger's lines, plus present trials that
    have none (:func:`_kept_attempt`).

    ``discarded_*`` are the ledger's attempts no present trial result is:
    Harbor deleted them to retry, or ``--rerun-infra`` dropped them. A
    control's attempts Harbor retried on its own are missing, as only
    OpenProgram's agent writes the ledger.
    """
    trials = trial_results(job_dir) if trials is None else trials
    attempts, ours_last = _ledger(job_dir)
    total = sum(_num(a.get("cost_usd")) for a in attempts.values())
    by_agent: dict[str, float] = {}
    for line in attempts.values():
        name = str(line.get("agent") or OUR_AGENT)
        by_agent[name] = by_agent.get(name, 0.0) + _num(line.get("cost_usd"))
    kept: set[str] = set()
    for result in trials:
        line = _kept_attempt(result, attempts, ours_last)
        if line is not None:
            kept.add(line)
            continue
        cost = _num((result.get("agent_result") or {}).get("cost_usd"))
        total += cost
        by_agent[_agent_name(result)] = by_agent.get(_agent_name(result), 0.0) + cost
    discarded_by_agent: dict[str, float] = {}
    for attempt_id, line in attempts.items():
        if attempt_id not in kept:
            name = str(line.get("agent") or OUR_AGENT)
            discarded_by_agent[name] = (discarded_by_agent.get(name, 0.0)
                                        + _num(line.get("cost_usd")))
    return {"total": total, "by_agent": by_agent,
            "discarded_attempts": len(attempts) - len(kept),
            "discarded_cost": sum(discarded_by_agent.values()),
            "discarded_by_agent": discarded_by_agent,
            "unpriced_attempts": sum(a.get("cost_usd") is None
                                     and _num(a.get("n_input_tokens")) > 0
                                     for a in attempts.values())}


def spent_usd(job_dir: Path) -> float:
    """Cost of every attempt of the job so far, deleted retries included."""
    return _costs(job_dir)["total"]


def verifier_setup_failure(trial_dir: Path | None) -> str | None:
    """The line showing the verifier's own setup failed before any test ran.

    Most tasks' ``tests/test.sh`` downloads uv (and through it Python and
    pytest) when it runs; a network failure there writes reward 0 or no
    reward though no test ran. ``None`` when the tests ran or the output
    shows no such failure.
    """
    if trial_dir is None:
        return None
    try:
        text = (trial_dir / "verifier" / "test-stdout.txt").read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _VERIFIER_SETUP_FAILED.search(text)
    if match is None or _TESTS_RAN.search(text):
        return None
    start = text.rfind("\n", 0, match.start()) + 1
    end = text.find("\n", match.end())
    return text[start:end if end >= 0 else None].strip()[:200]


def classify(result: dict[str, Any], trial_dir: Path | None = None) -> tuple[str, str]:
    """``(outcome, detail)`` of one trial.

    ``passed``: the verifier's reward is 1. ``infrastructure``: evidence
    that the container, provider or network failed, not the agent: an
    error of :data:`INFRA_ERRORS` (whatever the reward, as Harbor's retry
    and ``--rerun-infra`` treat it), a provider or network cause in
    OpenProgram's usage summary (a stalled stream, a model list the
    provider refused before the turn), the verifier's
    own setup failing before any test ran, or a finished turn whose
    verifier never started. ``cancelled``: the job was stopped before the
    trial ended. ``agent``: every other failure: a wrong answer, the
    agent's timeout, a verifier timeout or missing reward after the tests
    ran, or OpenProgram exiting with an error. ``trial_dir`` is the
    trial's folder, read for the verifier's output.
    """
    from scripts.benchmarks.harbor_agent import commands

    reward = _reward(result)
    exception = (result.get("exception_info") or {}).get("exception_type")
    run = ((result.get("agent_result") or {}).get("metadata") or {}).get("openprogram") or {}
    if exception in CANCELLED_ERRORS:
        return "cancelled", exception
    if exception in INFRA_ERRORS:
        return "infrastructure", exception
    kind = commands.error_kind(run)
    if kind is not None:
        # Recorded before the adapter raised the matching Harbor error.
        return "infrastructure", (
            "model stream stalled" if kind == "stalled"
            else f"{kind} before the turn" if run.get("ended") == commands.ENDED_NOT_STARTED
            else f"{kind} in the turn")
    if reward is not None and reward >= 1.0:
        return "passed", "reward 1" + (f" ({exception})" if exception else "")
    if verifier_setup_failure(trial_dir):
        return "infrastructure", "verifier setup failed"
    if (trial_dir is not None and reward is None and run.get("ended") == "completed"
            and exception not in VERIFIER_ERRORS
            and not (trial_dir / "verifier" / "test-stdout.txt").exists()):
        # The agent finished its turn and the verifier never ran (its
        # environment or network policy could not start).
        return "infrastructure", f"verifier did not run ({exception or 'no reward'})"
    if exception == "AgentTimeoutError":
        return "agent", "agent timeout"
    if exception:
        return "agent", exception
    if reward is None:
        return "infrastructure", "no reward"
    return "agent", f"reward {reward:g}"


def summarize(job_dir: Path, run_info: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pass rates, tokens, cost and time of a job, per agent and paired."""
    run_info = run_info if run_info is not None else (_read_json(run_file(job_dir)) or {})
    target = _run_target(run_info)
    found = _finished_trials(job_dir)
    costs = _costs(job_dir, [result for _, result in found])
    job = _read_json(job_dir / "result.json") or {}
    per_task: dict[str, dict[str, Any]] = {}
    rows = []
    for trial_dir, result in found:
        outcome, detail = classify(result, trial_dir)
        agent = result.get("agent_result") or {}
        meta = (agent.get("metadata") or {}).get("openprogram") or {}
        row = {
            "trial": result.get("trial_name"),
            "task": result.get("task_name"),
            "agent": _agent_name(result),
            "outcome": outcome,
            "detail": detail,
            "reward": _reward(result),
            "input_tokens": int(_num(agent.get("n_input_tokens"))),
            "cached_tokens": int(_num(agent.get("n_cache_tokens"))),
            "output_tokens": int(_num(agent.get("n_output_tokens"))),
            "cost_usd": agent.get("cost_usd"),
            "started_at": result.get("started_at"),
            "setup_min": _minutes(result.get("agent_setup")),
            "agent_min": _minutes(result.get("agent_execution")),
            "verifier_min": _minutes(result.get("verifier")),
            "trial_min": _minutes(result),
            "ended": meta.get("ended"),
            "model_requests": meta.get("model_requests"),
            "tool_calls": meta.get("tool_calls"),
            "error": (verifier_setup_failure(trial_dir) if detail == "verifier setup failed"
                      else _first_line((result.get("exception_info") or {})
                                       .get("exception_message"))),
        }
        rows.append(row)
        task = per_task.setdefault(row["task"], {"trials": 0, "passed": 0, "agent": 0,
                                                  "infrastructure": 0, "cancelled": 0,
                                                  "cost_usd": 0.0})
        task["trials"] += 1
        task[outcome] += 1
        task["cost_usd"] += _num(row["cost_usd"])
    for task in per_task.values():
        scored = task["trials"] - task["infrastructure"] - task["cancelled"]
        task["pass_rate"] = task["passed"] / scored if scored else None
    totals_input = sum(r["input_tokens"] for r in rows)
    totals_cached = sum(r["cached_tokens"] for r in rows)
    scored_rows = [r for r in rows if r["outcome"] in ("passed", "agent")]
    agent_minutes = [r["agent_min"] for r in rows if r["agent_min"] is not None]
    wall = _span_min(job.get("started_at"), job.get("finished_at") or job.get("updated_at"))
    names = sorted({r["agent"] for r in rows}, key=lambda name: (name != OUR_AGENT, name))
    previous = _previous_attempts(job_dir, found)
    # A job the launcher retried keeps every attempt of every agent; in an
    # older one Harbor retried and deleted the control's, so only
    # OpenProgram's own ledger lines tell its earlier attempts.
    every_agent = "retry_rounds" in run_info
    harbor_retries = (job.get("stats") or {}).get("n_retries")
    retries = (sum(len(lines) for lines in previous.values()) if every_agent
               else harbor_retries)
    ours_retries = sum(len(lines) for trial, lines in previous.items()
                       if trial in {r["trial"] for r in rows if r["agent"] == OUR_AGENT})
    others = [name for name in names if name != OUR_AGENT]

    def agent_retries(name: str) -> int | None:
        if every_agent or name == OUR_AGENT:
            trials = {r["trial"] for r in rows if r["agent"] == name}
            return sum(len(lines) for trial, lines in previous.items() if trial in trials)
        if len(others) == 1:
            return max(0, int(_num(harbor_retries)) - ours_retries)
        return None

    agents = {
        name: _agent_summary(
            [r for r in rows if r["agent"] == name], target=target,
            previous=previous if every_agent or name == OUR_AGENT else None,
            retries=agent_retries(name),
            cost_usd=costs["by_agent"].get(name, 0.0),
            retried_cost_usd=costs["discarded_by_agent"].get(name, 0.0))
        for name in names
    }
    control = run_info.get("control")
    return {
        "job": job_dir.name,
        "run": run_info,
        "n_total_trials": job.get("n_total_trials"),
        "trials": len(rows),
        "passed": sum(r["outcome"] == "passed" for r in rows),
        "agent_failures": sum(r["outcome"] == "agent" for r in rows),
        "infrastructure_errors": sum(r["outcome"] == "infrastructure" for r in rows),
        "cancelled": sum(r["outcome"] == "cancelled" for r in rows),
        "retries": retries,
        "pass_rate": (sum(r["outcome"] == "passed" for r in scored_rows) / len(scored_rows)
                      if scored_rows else None),
        "input_tokens": totals_input,
        "cached_tokens": totals_cached,
        "cached_share": totals_cached / totals_input if totals_input else None,
        "output_tokens": sum(r["output_tokens"] for r in rows),
        # Every attempt, including those Harbor deleted to retry them.
        "cost_usd": round(costs["total"], 4),
        "trials_cost_usd": round(sum(_num(r["cost_usd"]) for r in rows), 4),
        "discarded_attempts": costs["discarded_attempts"],
        "discarded_cost_usd": round(costs["discarded_cost"], 4),
        "unpriced_trials": sum(r["cost_usd"] is None and r["input_tokens"] > 0 for r in rows),
        "wall_min": wall,
        "agent_min_median": statistics.median(agent_minutes) if agent_minutes else None,
        "agents": agents,
        "paired": (paired_comparison(rows, OUR_AGENT, control)
                   if control and control in agents and OUR_AGENT in agents else None),
        "tasks": dict(sorted(per_task.items())),
        "rows": rows,
    }


#: The name Harbor gives OpenProgram's agent (``OpenProgramAgent.name()``).
OUR_AGENT = "openprogram"


def _agent_name(result: dict[str, Any]) -> str:
    info = result.get("agent_info") or {}
    if info.get("name"):
        return str(info["name"])
    agent = ((result.get("config") or {}).get("agent") or {})
    if agent.get("import_path") == AGENT_IMPORT_PATH or not agent.get("name"):
        return OUR_AGENT
    return str(agent["name"])


def _run_target(run_info: dict[str, Any]) -> dict[str, Any] | None:
    path = run_info.get("target_path")
    if not path:
        return None
    try:
        return load_target(path)
    except EvalError:
        return None


def _previous_attempts(job_dir: Path, found: list[tuple[Path, dict[str, Any]]]
                       ) -> dict[str, list[dict[str, Any]]]:
    """Each present trial's earlier attempts, by trial name: its ledger
    lines but the one of the attempt whose result is there
    (:func:`_kept_attempt`). OpenProgram writes a line per attempt; a
    control's earlier attempts have the lines the launcher wrote when it
    set them aside."""
    attempts, ours_last = _ledger(job_dir)
    previous: dict[str, list[dict[str, Any]]] = {}
    for _, result in found:
        trial = str(result.get("trial_name"))
        kept = _kept_attempt(result, attempts, ours_last)
        previous[trial] = [line for attempt_id, line in attempts.items()
                           if str(line.get("trial")) == trial and attempt_id != kept
                           and str(line.get("agent") or OUR_AGENT) == _agent_name(result)]
    return previous


def _agent_summary(rows: list[dict[str, Any]], *, target: dict[str, Any] | None,
                   previous: dict[str, list[dict[str, Any]]] | None,
                   retries: int | None, cost_usd: float,
                   retried_cost_usd: float = 0.0) -> dict[str, Any]:
    """One agent's share of a job.

    ``first_attempt_pass_rate`` counts a trial passed only when no attempt
    before the one that passed ended for the agent's own reason, so a
    retried crash cannot hide; an attempt retried for the provider, the
    network or the container (its ledger line names the cause) does not
    count against it. It needs each trial's earlier attempts
    (``previous``, :func:`_previous_attempts`): every agent's in a job the
    launcher retried, only OpenProgram's in an older one (``None``
    otherwise).
    ``first_valid_pass_rate`` is FrontierHarness's measure: per task, the
    earliest attempt that was scored; ``target_rate`` names the rate the
    target's published figure is. Repriced cost is tokens at the target's
    prices, the same for every agent; ``cost_per_pass_usd`` divides the
    repriced cost of the trials Harbor kept by their passes, the same
    attempts for every agent, and ``retried_cost_usd`` is the reported
    cost of the attempts it did not keep.
    """
    scored = [r for r in rows if r["outcome"] in ("passed", "agent")]
    passed = sum(r["outcome"] == "passed" for r in rows)
    first_valid: dict[str, bool] = {}
    for row in sorted(scored, key=lambda r: (str(r["started_at"] or ""), str(r["trial"]))):
        first_valid.setdefault(row["task"], row["outcome"] == "passed")
    first_attempt = None
    retried_kinds: dict[str, int] = {}
    if previous is not None:
        clean = sum(r["outcome"] == "passed"
                    and all(line.get("error_kind") for line in previous.get(r["trial"], []))
                    for r in scored)
        first_attempt = clean / len(scored) if scored else None
        for trial in {r["trial"] for r in rows}:
            for line in previous.get(trial, []):
                kind = line.get("error_kind") or "agent"
                retried_kinds[kind] = retried_kinds.get(kind, 0) + 1
    prices = ((target or {}).get("prices") or {})
    price = next(iter((prices.get("models") or {}).values()), None)
    repriced = [_reprice(r, price, prices.get("unit_tokens", 1_000_000)) for r in rows]
    kept_cost = sum(repriced) if price else sum(_num(r["cost_usd"]) for r in rows)
    cap = (target or {}).get("trial_wall_clock_cap_min")
    trial_minutes = [r["trial_min"] for r in rows if r["trial_min"] is not None]
    return {
        "trials": len(rows),
        "passed": passed,
        "agent_failures": sum(r["outcome"] == "agent" for r in rows),
        "infrastructure_errors": sum(r["outcome"] == "infrastructure" for r in rows),
        "cancelled": sum(r["outcome"] == "cancelled" for r in rows),
        "pass_rate": passed / len(scored) if scored else None,
        "first_attempt_pass_rate": first_attempt,
        "first_valid_pass_rate": (sum(first_valid.values()) / len(first_valid)
                                  if first_valid else None),
        "target_rate": SELECTION_RATES.get((target or {}).get("selection")),
        "retries": retries,
        "retried_kinds": retried_kinds,
        "cost_usd": round(cost_usd, 4),
        "retried_cost_usd": round(retried_cost_usd, 4),
        "repriced_cost_usd": (round(sum(repriced), 4) if price else None),
        "rows_repriced_usd": repriced if price else [],
        "cost_per_pass_usd": kept_cost / passed if passed else None,
        "container_hours": round(sum(trial_minutes) / 60.0, 3),
        "over_wall_clock_cap": (sum(m > cap for m in trial_minutes) if cap else None),
    }


def _reprice(row: dict[str, Any], price: dict[str, float] | None, unit: float) -> float:
    if not price:
        return 0.0
    uncached = max(0, row["input_tokens"] - row["cached_tokens"])
    return (uncached * price["input"] + row["cached_tokens"] * price["cached_input"]
            + row["output_tokens"] * price["output"]) / unit


def paired_comparison(rows: list[dict[str, Any]], ours: str, control: str) -> dict[str, Any]:
    """OpenProgram against the control, attempt by attempt on each task.

    The n-th scored attempt of each agent on a task form a pair. McNemar's
    exact test looks at the pairs where only one passed;
    ``min_detectable_difference`` is the smallest pass-rate gap this many
    pairs could show (5% two-sided, 80% power). Attempts on one task are
    not independent, so both read optimistic when tasks are few.
    """
    def scored(agent: str) -> dict[str, list[bool]]:
        by_task: dict[str, list[bool]] = {}
        for row in sorted((r for r in rows if r["agent"] == agent
                           and r["outcome"] in ("passed", "agent")),
                          key=lambda r: (str(r["started_at"] or ""), str(r["trial"]))):
            by_task.setdefault(row["task"], []).append(row["outcome"] == "passed")
        return by_task

    a, b = scored(ours), scored(control)
    pairs = ours_only = control_only = 0
    for task in a.keys() & b.keys():
        for mine, theirs in zip(a[task], b[task]):
            pairs += 1
            ours_only += mine and not theirs
            control_only += theirs and not mine
    return {
        "pairs": pairs, "tasks": len(a.keys() & b.keys()),
        "ours_only": ours_only, "control_only": control_only,
        "difference": (ours_only - control_only) / pairs if pairs else None,
        "p_value": mcnemar_p(ours_only, control_only),
        "min_detectable_difference": min_detectable_difference(
            pairs, (ours_only + control_only) / pairs if pairs else 0.0),
    }


def mcnemar_p(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value of ``b`` and ``c`` discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def min_detectable_difference(pairs: int, discordant: float) -> float | None:
    """Smallest paired pass-rate gap ``pairs`` pairs detect, 5% two-sided at
    80% power, when ``discordant`` of the pairs disagree."""
    if pairs <= 0 or discordant <= 0:
        return None
    return (1.959964 + 0.841621) * math.sqrt(discordant / pairs)


def summary_markdown(summary: dict[str, Any]) -> str:
    run = summary.get("run") or {}
    lines = [
        f"# {summary['job']}",
        "",
        f"- Target: `{run.get('target', '?')}`, sha256 `{str(run.get('target_sha256', '?'))[:12]}`; "
        f"comparable: {'yes' if run.get('methodology_comparable') else 'no'}",
    ]
    lines += [f"  - {reason}" for reason in run.get("deviations") or []]
    lines += [
        f"- Build: `{str(run.get('commit', '?'))[:12]}` ({run.get('ref', '?')}), "
        f"wheel sha256 `{str(run.get('wheel_sha256', '?'))[:12]}`",
        f"- Model: `{run.get('model', '?')}`, effort `{run.get('reasoning_effort', '?')}`, "
        f"control `{run.get('control') or 'none'}`, environment `{run.get('environment', '?')}`",
        f"- Trials: {summary['trials']} of {summary.get('n_total_trials') or '?'}; "
        f"passed {summary['passed']}, agent failures {summary['agent_failures']}, "
        f"infrastructure errors {summary['infrastructure_errors']}, "
        f"cancelled {summary['cancelled']}, retries {summary.get('retries') or 0}",
        f"- Pass rate (passed / passed + agent failures): {_pct(summary['pass_rate'])}",
        f"- Tokens: input {summary['input_tokens']:,} (cached {summary['cached_tokens']:,}, "
        f"{_pct(summary['cached_share'])}), output {summary['output_tokens']:,}",
        f"- Cost: ${summary['cost_usd']:.2f} for every attempt, "
        f"${summary.get('discarded_cost_usd', 0.0):.2f} of it in "
        f"{summary.get('discarded_attempts', 0)} retried or rerun attempts"
        + (f" ({summary['unpriced_trials']} trials unpriced)" if summary["unpriced_trials"] else ""),
        f"- Wall time: {_fmt_min(summary['wall_min'])}; median agent run "
        f"{_fmt_min(summary['agent_min_median'])}",
    ]
    measure = next((a["target_rate"] for a in (summary.get("agents") or {}).values()
                    if a.get("target_rate")), None)
    if measure:
        column = {"first_valid_pass_rate": "First valid", "pass_rate": "Pass rate"}[measure]
        lines.append(f"- The target publishes the '{column}' rate of the table below")
    paired = summary.get("paired")
    if paired:
        lines.append(
            f"- Paired with the control (McNemar): {paired['pairs']} pairs on "
            f"{paired['tasks']} tasks, OpenProgram alone passed {paired['ours_only']}, "
            f"the control alone {paired['control_only']}, p = {paired['p_value']:.3f}; "
            f"smallest gap this run can show {_pct(paired['min_detectable_difference'])}")
    lines += ["", "| Agent | Trials | Passed | Agent failures | Infra errors | Pass rate | "
              "First attempt | First valid | Retries | Cost | At target prices | "
              "Per pass | Container h |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, agent in (summary.get("agents") or {}).items():
        lines.append(
            f"| {name} | {agent['trials']} | {agent['passed']} | {agent['agent_failures']} | "
            f"{agent['infrastructure_errors']} | {_pct(agent['pass_rate'])} | "
            f"{_pct(agent['first_attempt_pass_rate'])} | {_pct(agent['first_valid_pass_rate'])} | "
            f"{'-' if agent['retries'] is None else agent['retries']} | "
            f"${agent['cost_usd']:.2f}"
            + (f" (${agent['retried_cost_usd']:.2f} retried)" if agent["retried_cost_usd"] else "")
            + f" | {_usd(agent['repriced_cost_usd'])} | "
            f"{_usd(agent['cost_per_pass_usd'])} | {agent['container_hours']:.2f} |")
    lines += [
        "",
        "| Task | Trials | Passed | Agent failures | Infra errors | Pass rate | Cost |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, task in summary["tasks"].items():
        lines.append(f"| {name} | {task['trials']} | {task['passed']} | {task['agent']} | "
                     f"{task['infrastructure']} | {_pct(task['pass_rate'])} | "
                     f"${task['cost_usd']:.2f} |")
    lines += ["", "| Trial | Agent | Outcome | Detail | Setup | Agent run | Requests | "
              "Input (cached) | Output | Cost |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in summary["rows"]:
        cost = row["cost_usd"]
        lines.append(
            f"| {row['trial']} | {row['agent']} | {row['outcome']} | {row['detail']} | "
            f"{_fmt_min(row['setup_min'])} | {_fmt_min(row['agent_min'])} | "
            f"{row['model_requests'] if row['model_requests'] is not None else '-'} | "
            f"{row['input_tokens']:,} ({row['cached_tokens']:,}) | {row['output_tokens']:,} | "
            f"{'-' if cost is None else f'${cost:.3f}'} |")
    return "\n".join(lines) + "\n"


def write_summary(job_dir: Path) -> dict[str, Any]:
    summary = summarize(job_dir)
    (job_dir / SUMMARY_JSON).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (job_dir / SUMMARY_MD).write_text(summary_markdown(summary), encoding="utf-8")
    return summary


# ------------------------------------------------------------- run / resume

def run_harbor(command: list[str], *, env: dict[str, str], job_dir: Path,
               budget_usd: float | None, poll_sec: float = 20.0,
               stop_grace_sec: float = 300.0, outcome: dict[str, Any] | None = None) -> int:
    """Run Harbor; stop it (SIGINT, as Ctrl-C) once finished trials cost
    ``budget_usd``. A stopped job resumes with ``resume``. ``outcome``
    receives ``stopped``: ``budget`` or ``interrupted`` when it was."""
    outcome = outcome if outcome is not None else {}
    process = subprocess.Popen(command, env=env, cwd=REPO)
    stopping_since: float | None = None
    try:
        while True:
            try:
                return process.wait(timeout=poll_sec)
            except subprocess.TimeoutExpired:
                pass
            if budget_usd is None:
                continue
            spent = spent_usd(job_dir)
            if stopping_since is None and spent >= budget_usd:
                print(f"[frontier_eval] budget reached: ${spent:.2f} of ${budget_usd:.2f}; "
                      "stopping Harbor", flush=True)
                process.send_signal(signal.SIGINT)
                stopping_since = time.monotonic()
                outcome["stopped"] = "budget"
            elif stopping_since is not None and time.monotonic() - stopping_since > stop_grace_sec:
                process.terminate()
    except KeyboardInterrupt:
        outcome["stopped"] = "interrupted"
        process.send_signal(signal.SIGINT)
        return process.wait()


def harbor_env(key_env: str, key: str) -> dict[str, str]:
    env = dict(os.environ)
    env[key_env] = key
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(REPO), env.get("PYTHONPATH")) if p)
    return env


def _resolve_key(args: argparse.Namespace, model: str) -> tuple[str, str]:
    name = key_env_name(model, args.key_env)
    if args.key_profile:
        return name, stored_api_key(args.key_profile, model.partition("/")[0])
    return name, os.environ.get(name, "")


def cmd_check(args: argparse.Namespace) -> int:
    target = load_target(args.target)
    name, key = _resolve_key(args, args.model or target["model"])
    warnings = check_prerequisites(harbor=args.harbor, environment=args.env,
                                   key_present=bool(key), root=args.root,
                                   allow_harbor_version=args.allow_harbor_version)
    for warning in warnings:
        print(f"[frontier_eval] note: {warning}")
    print(f"[frontier_eval] ready: Harbor {harbor_version(args.harbor)}, key in {name}, "
          f"build {resolve_commit(args.ref)[:12]} ({args.ref}), target {target['name']}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    target = load_target(args.target)
    model = args.model or target["model"]
    name, key = _resolve_key(args, model)
    for warning in check_prerequisites(harbor=args.harbor, environment=args.env,
                                       key_present=bool(key), root=args.root,
                                       allow_harbor_version=args.allow_harbor_version):
        print(f"[frontier_eval] note: {warning}", flush=True)
    jobs_dir = args.root / "jobs"
    job_dir = jobs_dir / args.job
    if job_dir.exists() or ledger_path(job_dir).exists():
        raise EvalError(f"job {args.job} exists under {jobs_dir}; use `resume` or another --job")
    commit = resolve_commit(args.ref)
    print(f"[frontier_eval] building {commit[:12]} ({args.ref})", flush=True)
    build = build_wheel(commit, args.root / "builds", ref=args.ref)
    if not args.no_bundle:
        print("[frontier_eval] packing the install for linux/amd64 (once per commit)", flush=True)
        build = build_bundle(build)
    effort_supported = wheel_supports_effort(Path(build["wheel"]))
    if not effort_supported:
        print(f"[frontier_eval] note: build {commit[:12]} takes no --effort; OpenProgram "
              "runs at its Agent default and the run is not comparable", flush=True)
    if args.pilot and (args.tasks or args.exclude_tasks or args.n_tasks):
        raise EvalError("--pilot runs the target's pilot tasks: not with --tasks, "
                        "--exclude-tasks or --n-tasks")
    if args.pilot and not pilot_tasks(target):
        raise EvalError(f"target {target['name']} names no pilot tasks")
    choices = dict(model=model, attempts=args.attempts, tasks=_split(args.tasks),
                   exclude_tasks=_split(args.exclude_tasks), n_tasks=args.n_tasks,
                   agent_timeout_cap_min=args.agent_timeout_cap_min,
                   control=not args.no_control, agent_env=_pairs(args.agent_env),
                   effort=args.effort, effort_supported=effort_supported,
                   pilot=args.pilot)
    effort = args.effort or target["reasoning_effort"]
    config = job_config(
        target, job_name=args.job, jobs_dir=jobs_dir,
        wheel=Path(build["wheel"]), constraints=Path(build["constraints"]),
        concurrency=args.concurrency, environment=args.env,
        provider=args.provider, install_only=args.install_only,
        bundle=None if args.no_bundle else Path(build["bundle"]), **choices)
    departures = deviations(target, environment=args.env,
                            harbor_found=harbor_version(args.harbor), **choices)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    config_path = jobs_dir / f"{args.job}.config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    run_info: dict[str, Any] = {"commit": commit, "ref": args.ref, "wheel": build["wheel"],
                "wheel_sha256": build["wheel_sha256"], "bundle": build.get("bundle"),
                "bundle_sha256": build.get("bundle_sha256") if not args.no_bundle else None,
                "target": target["name"], "target_path": target["path"],
                "target_sha256": target["sha256"],
                "methodology_comparable": not departures, "deviations": departures,
                "model": model, "reasoning_effort": effort,
                "openprogram_effort": effort if effort_supported else None,
                "control": (None if args.no_control else target["control"]["agent"]),
                "control_version": (None if args.no_control
                                    else target["control"]["version"]),
                "environment": args.env, "egress_policy": EGRESS_POLICY.get(
                    args.env, "unknown: not recorded by Harbor"),
                "budget_usd": args.budget_usd,
                "agent_timeout_cap_min": args.agent_timeout_cap_min,
                "pilot": pilot_tasks(target) if args.pilot else None,
                "disable_web_search": bool(target.get("disable_web_search")),
                # Retries are the launcher's (:func:`retry_round`); a job
                # without this key predates that and Harbor retried.
                "retry_rounds": 0,
                "harbor_version": harbor_version(args.harbor), "config": str(config_path),
                "started_at": _now()}
    if args.dry_run:
        print(f"[frontier_eval] config {config_path}", flush=True)
        return subprocess.run([args.harbor, "run", "-c", str(config_path), "--dry-run"],
                              env=harbor_env(name, key), cwd=REPO, check=False).returncode
    _save_run(job_dir, run_info)
    outcome: dict[str, Any] = {}
    status = run_harbor([args.harbor, "run", "-c", str(config_path)],
                        env=harbor_env(name, key), job_dir=job_dir,
                        budget_usd=args.budget_usd, outcome=outcome)
    if not args.install_only:
        status = retry_rounds(job_dir, run_info, harbor=args.harbor,
                              env=harbor_env(name, key), budget_usd=args.budget_usd,
                              policy=retry_policy(target), status=status, outcome=outcome)
    return _finish(job_dir, run_info, status)


def cmd_resume(args: argparse.Namespace) -> int:
    job_dir = args.root / "jobs" / args.job
    run_info = _read_json(run_file(job_dir))
    if not run_info:
        raise EvalError(f"no {run_file(job_dir)}; was the job started by `run`?")
    wheel = Path(run_info["wheel"])
    if not wheel.is_file() or sha256(wheel) != run_info["wheel_sha256"]:
        raise EvalError(f"the job's wheel {wheel} is missing or changed; resuming would mix builds")
    if run_info.get("target_sha256"):
        target = Path(run_info["target_path"])
        if not target.is_file() or sha256(target) != run_info["target_sha256"]:
            raise EvalError(f"the job's target {target} is missing or changed; "
                            "resuming would mix methods")
    if run_info.get("bundle_sha256"):
        bundle = Path(run_info["bundle"])
        if not bundle.is_file() or sha256(bundle) != run_info["bundle_sha256"]:
            raise EvalError(f"the job's install bundle {bundle} is missing or changed; "
                            "resuming would mix builds")
    name, key = _resolve_key(args, run_info["model"])
    check_prerequisites(harbor=args.harbor, environment=run_info["environment"],
                        key_present=bool(key), root=args.root,
                        allow_harbor_version=args.allow_harbor_version)
    command = [args.harbor, "job", "resume", "-p", str(job_dir)]
    if args.rerun_infra:
        for name in drop_infrastructure_trials(job_dir):
            print(f"[frontier_eval] rerunning {name}", flush=True)
    budget = args.budget_usd if args.budget_usd is not None else run_info.get("budget_usd")
    run_info["resumed_at"] = _now()
    _save_run(job_dir, run_info)
    outcome: dict[str, Any] = {}
    status = run_harbor(command, env=harbor_env(name, key), job_dir=job_dir, budget_usd=budget,
                        outcome=outcome)
    if "retry_rounds" in run_info:
        target = _run_target(run_info)
        status = retry_rounds(job_dir, run_info, harbor=args.harbor,
                              env=harbor_env(name, key), budget_usd=budget,
                              policy=retry_policy(target or {}), status=status,
                              outcome=outcome)
    return _finish(job_dir, run_info, status)


def retry_rounds(job_dir: Path, run_info: dict[str, Any], *, harbor: str,
                 env: dict[str, str], budget_usd: float | None, policy: dict[str, Any],
                 status: int, outcome: dict[str, Any]) -> int:
    """The launcher's retries, after Harbor ran the job: up to
    ``policy["max_retries"]`` rounds over the whole job (counted in
    ``run_info["retry_rounds"]``, so a resumed job goes on from where it
    was). Each round moves the trials :func:`retryable` picks aside
    (:func:`retry_round`) and runs ``harbor job resume``, which runs them
    again. No round starts once the budget is spent or Harbor was stopped,
    and each round is held to the same budget. Returns Harbor's last exit
    status."""
    while not outcome.get("stopped") and run_info.get("retry_rounds", 0) < policy["max_retries"]:
        if budget_usd is not None and spent_usd(job_dir) >= budget_usd:
            print(f"[frontier_eval] budget reached: no retry round", flush=True)
            break
        retried = retry_round(job_dir, policy)
        if not retried:
            break
        run_info["retry_rounds"] = run_info.get("retry_rounds", 0) + 1
        _save_run(job_dir, run_info)
        print(f"[frontier_eval] retry round {run_info['retry_rounds']}: "
              f"{', '.join(retried)}", flush=True)
        status = run_harbor([harbor, "job", "resume", "-p", str(job_dir)], env=env,
                            job_dir=job_dir, budget_usd=budget_usd, outcome=outcome)
    return status


def retried_dir(job_dir: Path) -> Path:
    """Where the attempts the launcher retried are kept: beside Harbor's job
    folder, never inside it, where ``harbor job resume`` deletes any folder
    without a ``result.json``."""
    return job_dir.parent / f"{job_dir.name}.retried"


def retryable(result: dict[str, Any], trial_dir: Path | None,
              policy: dict[str, Any]) -> bool:
    """Whether a finished trial is retried: the trials Harbor's
    ``RetryConfig`` retries (an exception not excluded, and named when the
    policy names the ones it includes), and a verifier whose own setup
    failed before any test ran. A trial the job's stop cancelled is not a
    retry: ``resume`` runs it again."""
    exception = (result.get("exception_info") or {}).get("exception_type")
    if exception in CANCELLED_ERRORS:
        return False
    if exception:
        if exception in policy["exclude"]:
            return False
        return policy["include"] is None or exception in policy["include"]
    return verifier_setup_failure(trial_dir) is not None


def retry_round(job_dir: Path, policy: dict[str, Any]) -> list[str]:
    """Move the trials :func:`retryable` picks to :func:`retried_dir`, each
    with its ledger line, so ``harbor job resume`` runs them again."""
    return _set_aside(job_dir, lambda result, trial_dir: retryable(result, trial_dir, policy),
                      archive_to=retried_dir(job_dir))


def drop_infrastructure_trials(job_dir: Path, archive_to: Path | None = None) -> list[str]:
    """Set aside the trials :func:`classify` counts as infrastructure or
    cancelled, so ``harbor job resume`` runs them again (``resume
    --rerun-infra``).

    Harbor's own ``-f`` filter sees only the exception type, which misses a
    verifier whose setup failed (reward 0, no exception). Each trial is
    moved under ``archive_to`` (:func:`retried_dir` when not given) and its
    attempt is in the ledger with the reason it was dropped (see
    :func:`_set_aside`).
    """
    return _set_aside(
        job_dir,
        lambda result, trial_dir: classify(result, trial_dir)[0] in ("infrastructure",
                                                                     "cancelled"),
        archive_to=archive_to or retried_dir(job_dir))


def _set_aside(job_dir: Path, pick, *, archive_to: Path) -> list[str]:
    """Move every finished trial ``pick(result, trial_dir)`` takes to
    ``archive_to/<trial>/<n>/`` (``n`` counts that trial's set-aside
    attempts from 1), after writing its ledger line.

    The line's ``error_kind`` is why the attempt does not count: the
    provider cause, else ``infrastructure`` or ``cancelled``, for a trial
    :func:`classify` does not hold against the agent; ``None`` for one it
    does (a crash, a refused call), so a retry never hides it. An attempt
    without a line gets one under :func:`_result_attempt_id` (a control's
    attempts, which no adapter of ours records), so its cost stays
    counted when Harbor reruns the trial under the same name;
    OpenProgram's own line is written again with the reason.
    """
    from scripts.benchmarks.harbor_agent import commands

    ledger = ledger_path(job_dir)
    attempts, ours_last = _ledger(job_dir)
    moved = []
    for trial_dir, result in _finished_trials(job_dir):
        if not pick(result, trial_dir):
            continue
        outcome = classify(result, trial_dir)[0]
        name = str(result["trial_name"])
        agent = result.get("agent_result") or {}
        meta = (agent.get("metadata") or {}).get("openprogram") or {}
        reason = ((commands.error_kind(meta) or outcome)
                  if outcome in ("infrastructure", "cancelled") else None)
        kept = _kept_attempt(result, attempts, ours_last)
        if kept is not None:
            line = attempts[kept]
            if reason and not line.get("error_kind"):
                _append_line(ledger, {**line, "error_kind": reason})
        elif agent:
            ledger.parent.mkdir(parents=True, exist_ok=True)
            commands.append_attempt(
                ledger, trial=name, attempt=_result_attempt_id(result),
                agent=_agent_name(result), error_kind=reason,
                fields={key: agent.get(key) for key in (
                    "cost_usd", "n_input_tokens", "n_cache_tokens", "n_output_tokens")}
                | {"metadata": agent.get("metadata") or {}})
        home = archive_to / name
        home.mkdir(parents=True, exist_ok=True)
        number = 1 + sum(1 for entry in home.iterdir() if entry.is_dir())
        shutil.move(str(trial_dir), str(home / str(number)))
        moved.append(name)
    return moved


def _append_line(ledger: Path, line: dict[str, Any]) -> None:
    """Write a ledger line again; the reader keeps the last line of an attempt."""
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")


def cmd_summary(args: argparse.Namespace) -> int:
    job_dir = args.root / "jobs" / args.job
    if not job_dir.is_dir():
        raise EvalError(f"no job directory {job_dir}")
    write_summary(job_dir)
    print((job_dir / SUMMARY_MD).read_text(encoding="utf-8"))
    return 0


def _finish(job_dir: Path, run_info: dict[str, Any], status: int) -> int:
    run_info["harbor_exit"] = status
    run_info["finished_at"] = _now()
    _save_run(job_dir, run_info)
    if job_dir.is_dir():
        write_summary(job_dir)
        print((job_dir / SUMMARY_MD).read_text(encoding="utf-8"), flush=True)
    return status


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="frontier_eval", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--root", type=lambda s: Path(s).expanduser().resolve(),
                        default=DEFAULT_ROOT.expanduser(),
                        help="folder for builds and jobs (default ~/.openprogram-eval)")
        sp.add_argument("--harbor", default=os.environ.get("HARBOR", "harbor"),
                        help="Harbor executable (default: harbor on PATH, or $HARBOR)")
        sp.add_argument("--allow-harbor-version", action="store_true",
                        help=f"run with a Harbor other than {HARBOR_VERSION}")
        sp.add_argument("--key-profile", metavar="PROFILE",
                        help="take the provider key from this OpenProgram profile's credentials")
        sp.add_argument("--key-env", metavar="VAR",
                        help="variable Harbor reads the key from (default by provider)")

    check = sub.add_parser("check", help="check the prerequisites only")
    common(check)
    check.add_argument("--target", default=DEFAULT_TARGET,
                       help=f"target name in targets/ or a path (default {DEFAULT_TARGET})")
    check.add_argument("--model", help="model instead of the target's")
    check.add_argument("--env", default="docker")
    check.add_argument("--ref", default="stable")

    run = sub.add_parser("run", help="build, check and start a job")
    common(run)
    run.add_argument("--target", default=DEFAULT_TARGET,
                     help=f"target name in targets/ or a path (default {DEFAULT_TARGET})")
    run.add_argument("--model", help="PROVIDER/MODEL_ID instead of the target's (not comparable)")
    run.add_argument("--effort", choices=EFFORTS,
                     help="reasoning effort for both agents instead of the target's "
                          "(not comparable to the target)")
    run.add_argument("--provider", help="OpenProgram provider id when it differs from -m's")
    run.add_argument("--job", help="job name (default op-<target>)")
    run.add_argument("--ref", default="stable", help="commit or branch to build (default stable)")
    run.add_argument("--env", default="docker", help="docker (default) or runta")
    run.add_argument("--no-control", action="store_true",
                     help="leave the target's control harness out (not comparable)")
    run.add_argument("--attempts", type=int,
                     help="attempts per task instead of the target's (not comparable)")
    run.add_argument("--concurrency", type=int, help="trials at once (default 4)")
    run.add_argument("--tasks", help="comma-separated task names or globs to include")
    run.add_argument("--exclude-tasks", help="comma-separated task names or globs to leave out")
    run.add_argument("--n-tasks", type=int, help="at most this many tasks")
    run.add_argument("--pilot", action="store_true",
                     help="run the target's fixed pilot tasks only (recorded as a departure)")
    run.add_argument("--agent-timeout-cap-min", type=float,
                     help="cap every task's agent timeout (not comparable to published runs)")
    run.add_argument("--budget-usd", type=float,
                     help="stop the job once its attempts, retries included, cost this much")
    run.add_argument("--agent-env", action="append", default=[], metavar="KEY=VALUE",
                     help="environment variable for the agent's run (repeatable)")
    run.add_argument("--no-bundle", action="store_true",
                     help="install from the network in each container instead of a prebuilt bundle")
    run.add_argument("--install-only", action="store_true",
                     help="install OpenProgram in each task container and stop (no model calls)")
    run.add_argument("--dry-run", action="store_true",
                     help="build and write the config, then let Harbor validate it only")

    resume = sub.add_parser("resume", help="continue an interrupted job")
    common(resume)
    resume.add_argument("--job", required=True)
    resume.add_argument("--budget-usd", type=float, help="new budget (default: the run's)")
    resume.add_argument("--rerun-infra", action="store_true",
                        help="drop trials the summary counts as infrastructure errors or "
                             "cancelled (a failed verifier setup included), and run them again")

    summary = sub.add_parser("summary", help="write and print a job's summary")
    common(summary)
    summary.add_argument("--job", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "run" and not args.job:
        args.job = f"op-{Path(args.target).stem}"
    handlers = {"check": cmd_check, "run": cmd_run, "resume": cmd_resume, "summary": cmd_summary}
    try:
        return handlers[args.command](args)
    except EvalError as exc:
        print(f"[frontier_eval] {exc}", file=sys.stderr)
        return 2


# ------------------------------------------------------------------ helpers

def run_file(job_dir: Path) -> Path:
    """The launcher's record of a job, beside Harbor's job directory."""
    return job_dir.parent / f"{job_dir.name}{RUN_FILE_SUFFIX}"


def _save_run(job_dir: Path, run_info: dict[str, Any]) -> None:
    run_file(job_dir).write_text(json.dumps(run_info, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _reward(result: dict[str, Any]) -> float | None:
    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    value = rewards.get("reward")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _num(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _parse_time(value: Any) -> _dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Harbor writes a job's started_at and finished_at in local time without
    # an offset, but the updated_at of a running job and every trial time in
    # UTC with "Z".
    return parsed if parsed.tzinfo else parsed.astimezone()


def _span_min(start: Any, end: Any) -> float | None:
    a, b = _parse_time(start), _parse_time(end)
    return round((b - a).total_seconds() / 60.0, 2) if a and b else None


def _minutes(phase: Any) -> float | None:
    if not isinstance(phase, dict):
        return None
    return _span_min(phase.get("started_at"), phase.get("finished_at"))


def _first_line(text: Any) -> str | None:
    return str(text).strip().splitlines()[0][:200] if text else None


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def _usd(value: float | None) -> str:
    return "-" if value is None else f"${value:.2f}"


def _fmt_min(value: float | None) -> str:
    if value is None:
        return "-"
    minutes = int(value)
    return f"{minutes} m {round((value - minutes) * 60):02d} s"


def _pairs(values: list[str]) -> dict[str, str]:
    pairs = {}
    for value in values:
        key, sep, rest = value.partition("=")
        if not sep or not key.strip():
            raise EvalError(f"--agent-env takes KEY=VALUE, got {value!r}")
        pairs[key.strip()] = rest
    return pairs


def _split(value: str | None) -> list[str]:
    return [part for part in (value or "").split(",") if part.strip()]


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    sys.exit(main())
