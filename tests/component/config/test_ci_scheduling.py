from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def _workflow(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def test_ci_replaces_only_checks_for_the_same_workflow_event_and_branch():
    workflow = _workflow("ci.yml")
    assert workflow["concurrency"] == {
        "group": "${{ github.workflow }}-${{ github.event_name }}-${{ github.event.pull_request.number || github.ref }}",
        "cancel-in-progress": True,
    }
    # Faster feedback must not come from removing native architecture coverage.
    for job in ("windows-core", "windows-desktop"):
        matrix = workflow["jobs"][job]["strategy"]["matrix"]["include"]
        assert {entry["arch"] for entry in matrix} == {"x64", "arm64"}


def test_manual_and_scheduled_install_checks_have_unique_concurrency_groups():
    workflow = _workflow("windows-install-smoke.yml")
    assert workflow["concurrency"] == {
        "group": "${{ github.workflow }}-${{ github.event_name }}-${{ github.event_name == 'pull_request' && github.event.pull_request.number || github.event_name == 'push' && github.ref || github.run_id }}",
        "cancel-in-progress": "${{ github.event_name == 'push' || github.event_name == 'pull_request' }}",
    }
    matrix = workflow["jobs"]["install-ps1"]["strategy"]["matrix"]["include"]
    assert {entry["arch"] for entry in matrix} == {"x64", "arm64"}
