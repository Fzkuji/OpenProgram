from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def _workflow() -> dict:
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())


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
        "web",
        "cli",
        "desktop",
        "browser",
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
    assert "tools.docs_site.build" in quality
    assert "tests/unit" in _run_commands(jobs["unit"])
    assert "tests/component" in _run_commands(jobs["component"])
    assert "tests/integration" in _run_commands(jobs["integration"])

    web = _run_commands(jobs["web"])
    assert all(command in web for command in (
        "npm test", "npx tsc --noEmit", "npm run check", "npm run build",
    ))
    cli = _run_commands(jobs["cli"])
    assert all(command in cli for command in (
        "npm run typecheck", "npm test", "npm run build",
    ))
    assert "npm run check" in _run_commands(jobs["desktop"])
    browser = _run_commands(jobs["browser"])
    assert "playwright install --with-deps chromium" in browser
    assert "npm run build" in browser
    assert "-m browser tests/e2e/web" in browser


def test_ci_python_jobs_use_the_checked_lock() -> None:
    jobs = _workflow()["jobs"]
    for name in ("quality", "unit", "component", "integration", "browser"):
        commands = _run_commands(jobs[name])
        assert "uv sync --locked" in commands, name
        assert "uv run --locked" in commands, name
        assert "pip install" not in commands, name


def test_contributor_commands_match_required_ci_entrypoints() -> None:
    contributing = (ROOT / "CONTRIBUTING.md").read_text()
    for command in (
        "uv run --locked --extra dev python -m pytest -q tests/contracts",
        "uv run --locked --extra dev python -m pytest -q tests/unit",
        "uv run --locked --extra dev python -m pytest -q tests/component",
        "uv run --locked --extra dev python -m pytest -q tests/integration",
        "npm test",
        "npm run typecheck",
        "npm run check",
        "npm run build",
        "playwright install --with-deps chromium",
        "-m browser tests/e2e/web",
    ):
        assert command in contributing
