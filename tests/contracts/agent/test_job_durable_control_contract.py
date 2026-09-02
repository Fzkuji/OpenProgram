"""Source-level public cutover contracts for Job durable control.

These checks are intentionally structural: removal of the old wire aliases and
the presence of every public surface are repository contracts, not helper
implementation details.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_legacy_job_lifecycle_controls_are_removed_from_public_surfaces() -> None:
    public_sources = (
        "openprogram/agent/job/runner.py",
        "openprogram/programs/tools/agents/agent/job_stop/job_stop.py",
        "openprogram/programs/tools/agents/agent/agent/agent.py",
        "apps/server/openprogram_server/_webui/ws_actions/job.py",
        "apps/server/openprogram_server/_webui/ws_actions/sub_agent.py",
        "apps/cli/src/ws/client.ts",
        "apps/cli/src/screens/repl/useWsEvents.ts",
    )
    old_tokens = ("cancel_job", "job_stop", "spawn_sub_agent")
    for path in public_sources:
        source = _source(path)
        assert not any(token in source for token in old_tokens), path


def test_job_runner_delegates_activation_to_agent_production_driver() -> None:
    runner = _source("openprogram/agent/job/runner.py")
    assert "AgentProductionDriver" in runner
    assert "_execute_agent_turn" not in runner
    assert not (ROOT / "openprogram/agent/job/driver.py").exists()


def test_execution_cli_declares_the_four_canonical_control_verbs() -> None:
    from openprogram.cli.parser import build_parser

    parser = build_parser()
    for verb in ("pause", "continue", "step", "cancel"):
        parsed = parser.parse_args(
            ["execution", verb, "job-execution-1", "--expected-version", "1"],
        )
        assert parsed.command == "execution"
        assert parsed.execution_verb == verb
        assert parsed.execution_id == "job-execution-1"


def test_rest_registers_every_canonical_execution_control_route() -> None:
    from fastapi import FastAPI

    from openprogram.webui.routes import lifecycle

    app = FastAPI()
    lifecycle.register(app)
    routes = {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", ())
    }
    assert {
        ("POST", "/api/execution/pause"),
        ("POST", "/api/execution/continue"),
        ("POST", "/api/execution/step"),
        ("POST", "/api/execution/cancel"),
    } <= routes


def test_job_surface_sources_use_execution_envelope_and_cursor() -> None:
    for path in (
        "apps/cli/src/commands/jobResource.ts",
        "apps/cli/src/screens/repl/pickerRouter.tsx",
        "apps/cli/src/screens/repl/useWsEvents.ts",
        "apps/web/lib/job-resource.ts",
        "apps/web/lib/net/ws-events.ts",
        "apps/web/components/right-sidebar/branches/branch-item.tsx",
    ):
        source = _source(path)
        assert "execution_id" in source, path
        assert "event_cursor" in source or "cursor" in source, path


def test_non_transport_adapters_preserve_a_job_execution_mapping() -> None:
    for path in (
        "openprogram/acp/server.py",
        "openprogram/channels/_conversation.py",
        "openprogram/scheduler/service.py",
    ):
        source = _source(path)
        assert "execution" in source, path
        assert "session" in source or "job" in source, path
