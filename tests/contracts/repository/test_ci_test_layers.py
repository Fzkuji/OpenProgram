from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def _workflow() -> dict:
    return yaml.safe_load(
        (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )


def _run_commands(job: dict) -> str:
    return "\n".join(
        str(step.get("run", ""))
        for step in job.get("steps", [])
        if isinstance(step, dict)
    )


def test_ci_assigns_each_test_runtime_to_an_explicit_job() -> None:
    jobs = _workflow()["jobs"]
    assert {
        "quality",
        "unit",
        "component",
        "integration",
        "e2e",
        "web",
        "cli",
        "desktop",
        "browser",
        "coverage",
    } <= set(jobs)
    assert jobs["unit"]["strategy"]["fail-fast"] is False
    assert jobs["unit"]["strategy"]["matrix"]["python-version"] == [
        "3.11",
        "3.12",
        "3.13",
    ]

    quality = _run_commands(jobs["quality"])
    assert "ruff check" in quality
    assert "tests/contracts" in quality
    assert "systemd-analyze verify" in quality
    assert "from openprogram.worker.services.systemd import _build_unit" in quality
    assert "scripts.docs_site.build" in quality
    assert "tests/unit" in _run_commands(jobs["unit"])
    assert "tests/component" in _run_commands(jobs["component"])
    assert "tests/integration" in _run_commands(jobs["integration"])
    assert '-m "not browser" tests/e2e' in _run_commands(jobs["e2e"])

    web = _run_commands(jobs["web"])
    assert all(command in web for command in (
        "npm test", "npx tsc --noEmit", "npm run check", "npm run build",
    ))
    cli = _run_commands(jobs["cli"])
    assert all(command in cli for command in (
        "npm run typecheck",
        "npm test",
        "npm run build",
        "npm run build:standalone",
        "smoke-ink-tui-pty.py node dist/index-standalone.cjs",
    ))
    assert "npm run check" in _run_commands(jobs["desktop"])
    browser = _run_commands(jobs["browser"])
    assert "playwright install --with-deps chromium" in browser
    assert "npm run build" in browser
    assert "-m browser tests/e2e/web" in browser


def test_ci_runs_every_cli_step_from_the_apps_workspace() -> None:
    steps = _workflow()["jobs"]["cli"]["steps"]
    setup_node = next(
        step for step in steps
        if str(step.get("uses", "")).startswith("actions/setup-node@")
    )
    run_steps = [step for step in steps if step.get("run")]

    assert setup_node["with"]["cache-dependency-path"] == (
        "package-lock.json"
    )
    assert [step["run"] for step in run_steps] == [
        "npm ci --workspace apps/cli --include-workspace-root --ignore-scripts",
        "npm run typecheck",
        "npm test",
        "npm run build",
        "npm run build:standalone",
        "python3 ../../scripts/release/smoke-ink-tui-pty.py node dist/index-standalone.cjs",
    ]
    assert run_steps[0].get("working-directory") is None
    assert {step.get("working-directory") for step in run_steps[1:]} == {"apps/cli"}


def test_ci_python_jobs_use_the_checked_lock() -> None:
    jobs = _workflow()["jobs"]
    for name in (
        "quality", "unit", "component", "integration", "e2e", "browser", "coverage",
    ):
        commands = _run_commands(jobs[name])
        assert "uv sync --locked" in commands, name
        assert "uv run --locked" in commands, name
        assert "pip install" not in commands, name


def test_ci_enforces_the_verified_unit_coverage_floor() -> None:
    workflow = _workflow()
    coverage = workflow["jobs"]["coverage"]
    commands = _run_commands(coverage)
    assert "coverage run --branch --source=openprogram -m pytest -q tests/unit" in commands
    assert (
        "coverage report --show-missing --precision=6 --fail-under=40"
        in commands
    )
    assert "coverage xml -o coverage.xml" in commands
    steps = coverage["steps"]
    upload_indexes = [
        index for index, step in enumerate(steps)
        if isinstance(step, dict) and str(step.get("uses", "")).startswith(
            "actions/upload-artifact@"
        )
    ]
    assert len(upload_indexes) == 1
    upload_index = upload_indexes[0]
    assert steps[upload_index]["with"]["path"] == "coverage.xml"
    xml_index = next(
        index for index, step in enumerate(steps)
        if "coverage xml -o coverage.xml" in str(step.get("run", ""))
    )
    floor_index = next(
        index for index, step in enumerate(steps)
        if "--fail-under=40" in str(step.get("run", ""))
    )
    assert xml_index < upload_index < floor_index


def test_contributor_commands_match_required_ci_entrypoints() -> None:
    contributing = (ROOT / ".github" / "CONTRIBUTING.md").read_text(
        encoding="utf-8"
    )
    for command in (
        "uv run --locked --extra dev python -m pytest -q tests/contracts",
        "uv run --locked --extra dev python -m pytest -q tests/unit",
        "uv run --locked --extra dev python -m pytest -q tests/component",
        "uv run --locked --extra dev python -m pytest -q tests/integration",
        'uv run --locked --extra dev python -m pytest -q -m "not browser" tests/e2e',
        "npm test",
        "npm run typecheck",
        "npm run check",
        "npm run build",
        "playwright install --with-deps chromium",
        "-m browser tests/e2e/web",
        "coverage run --branch --source=openprogram -m pytest -q tests/unit",
        "coverage xml -o coverage.xml",
        "coverage report --show-missing --precision=6 --fail-under=40",
        "python -m pytest -q -n 4 tests/contracts tests/unit tests/component tests/integration tests/e2e",
    ):
        assert command in contributing
