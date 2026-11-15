"""Owned renderer inventory, real Program spawn and the registered screen tool listing its tabs."""

from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path

from openprogram import Agent
from openprogram.runtime.agent.runtime import Runtime
from openprogram.runtime.execution import ExecutionStore
from openprogram.state.store import SessionStore
import openprogram.runtime.context.session_db as session_db
import openprogram.runtime.execution as execution
import openprogram.runtime.agent.runtime.factory as runtime_factory
import openprogram.state.store.session.session_store as session_store

HOME = Path(os.environ["HOME"])
DB = SessionStore(HOME / "sessions")
STORE = ExecutionStore(HOME / "execution.sqlite3")
# Only the probe process and its spawned child carry the receipt variable.
# Anything else that imports this file (pytest given its path) keeps its own
# stores and provider instead of inheriting these for the rest of the run.
if os.environ.get("PAGE_PROBE_RECEIPT"):
    session_db.default_db = lambda: DB
    session_store.shared._default_store = DB
    execution.default_store = lambda: STORE
    runtime_factory.create_runtime = lambda **_kw: Runtime(call=lambda *_a, **_k: "unused")


# The call that lists the built-in browser's tabs.
LIST_TABS = {"target": "browser", "action": "tabs", "args": {"all": True}}


def _decided(decision):
    return decision.outcome, decision.code


def source_identity():
    import hashlib
    import importlib

    root = Path(__file__).resolve().parents[2]
    paths = {}
    for name in (
        "openprogram.runtime.conversation.turn_runtime.runtime_attach",
        "openprogram.runtime.conversation.process_runner",
        "openprogram.runtime.conversation.turn_request_context",
    ):
        path = Path(importlib.import_module(name).__file__).resolve()
        assert path.is_relative_to(root), path
        paths[name] = {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return paths


class PageScopeProbeAgent(Agent):
    method_options = {
        "page_scope_probe": {"name": "page_scope_probe", "tool": True},
    }

    async def page_scope_probe(self, runtime=None):
        from openprogram.runtime.agent.turn_request import (
            get_turn_request,
            inner_turn_request,
        )
        from openprogram.programs import get_agent_tool
        req = get_turn_request()
        inner = inner_turn_request("program")
        native = get_agent_tool("computer_use_agent")
        gated = runtime._gate_inner_tools([native])[0]

        async def calls():
            try:
                listed = await gated.execute("list-owned", dict(LIST_TABS), None, None)
            except RuntimeError as exc:
                if os.environ.get("PAGE_PROBE_SCENARIO") != "stale_binding":
                    raise
                return {"listed_error": True, "listed_text": str(exc)}
            return {
                "listed_error": listed.is_error,
                "listed_text": "".join(c.text for c in listed.content if hasattr(c, "text")),
            }

        result = await calls()
        if os.environ.get("PAGE_PROBE_SCENARIO") == "old_recovery":
            from openprogram.runtime.permissions.policy import permission_decision
            from openprogram.models.providers.types import AgentTool
            async def unexpected_action(*_args):
                raise AssertionError("policy inspection must not execute a browser action")
            action = AgentTool(name="browser_page", label="Page", description="", parameters={}, execute=unexpected_action)
            result["action_policy"] = _decided(permission_decision(action, inner, {"action": "click"}))
        result.update(
            pid=os.getpid(),
            interaction=req.interaction,
            speaker=req.speaker_kind,
            child_scope=req.surface_context,
            inner_scope=inner.surface_context,
            child_mode=req.permission_mode,
            inner_mode=inner.permission_mode,
        )
        result["source_identity"] = source_identity()
        return json.dumps(result)


page_scope_probe = PageScopeProbeAgent().page_scope_probe


def main():
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.turn_runtime import (
        _wrap_agentic_runtime_block,
    )
    from openprogram.runtime.permissions.authority import local_owner_authority
    from openprogram.programs import get_agent_tool
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions import webtab

    scenario = os.environ.get("PAGE_PROBE_SCENARIO", "available")
    ws = object()
    calls = []

    def renderer(owner, cmd, *args, **kwargs):
        calls.append({**dict(cmd), "fixture_window_id": "owned-window" if owner is ws else "foreign-window"})
        assert owner is ws
        if scenario == "capture_fail":
            return {"ok": False, "error": "owned capture failure"}
        if cmd["op"] == "list":
            return {
                "ok": True,
                "window_id": "owned-window",
                "pages": [
                    {
                        "tab_id": "owned-tab",
                        "target_id": "owned-target",
                        "url": "about:blank",
                        "title": "Owned Page",
                        "visible": True,
                        "focused": True,
                        "region": "center",
                    }
                ],
            }
        if cmd["op"] in {"activate", "resolve"}:
            return {
                "ok": True,
                "window_id": "owned-window",
                "tab_id": "owned-tab",
                "target_id": "owned-target",
                "input_scale": 1,
            }
        raise AssertionError(cmd)

    webtab.request_on_ws = renderer
    if scenario != "no_app":
        server._ws_connections.append(ws)
        asyncio.run(webtab.handle_webtab_register(ws, {"window_id": "owned-window"}))
    DB.create_session("page-scope", "main", source="test")
    req = TurnRequest(
        session_id="page-scope",
        user_text="",
        agent_id="main",
        source="test",
        permission_mode="auto",
        profile_snapshot={},
        **local_owner_authority(),
    )
    execution_token = None
    if scenario == "old_recovery":
        from tests.component.agent.security.test_recovery_permissions import orphan, admit_owner_turn
        from openprogram.runtime.execution.run_control import set_current_execution_id
        req.source = "web"
        req.user_text = "Inspect owned Page"
        orphan(STORE, session=req.session_id)
        activation = admit_owner_turn(STORE, req)
        execution_token = set_current_execution_id(activation.execution_id)
    owned_context = None
    if scenario in {
        "existing",
        "stale_binding",
        "plan",
        "active_plan",
        "live_plan",
        "live_deny",
        "live_ask",
    }:
        from openprogram.runtime.conversation import surface_context
        import copy

        owned_context = surface_context.capture_pages()
        req.surface_context = copy.deepcopy(owned_context)
        if scenario == "stale_binding":
            req.surface_context["surfaces"][0]["binding_id"] = (
                "stale-inherited-binding"
            )
            # Recovery must keep the originally authorized window even when
            # a different window connects after its binding becomes stale.
            foreign_ws = object()
            server._ws_connections.append(foreign_ws)
            asyncio.run(webtab.handle_webtab_register(foreign_ws, {"window_id": "foreign-window"}))
        if scenario == "plan":
            req.permission_mode = "plan"
    if scenario == "disabled":
        req.surface_context = {"surfaces": [{"enabled": False}]}
    if scenario == "empty":
        req.surface_context = {}
    if scenario in {"deny", "ask"}:
        from types import SimpleNamespace

        req.permission_rules = SimpleNamespace(
            allow=[],
            deny=["computer_use_agent"] if scenario == "deny" else [],
            ask=["computer_use_agent"] if scenario == "ask" else [],
        )
    if scenario == "active_plan":
        req.permission_mode = "plan"
    if scenario in {"live_plan", "live_deny", "live_ask"}:
        req.source = "web"
        from openprogram.runtime.permissions.state import update_permission

        update_permission(
            req.session_id,
            "plan" if scenario == "live_plan" else "auto",
            0,
            local_owner_authority(),
            db=DB,
        )
        if scenario in {"live_deny", "live_ask"}:
            from openprogram.runtime.conversation.session_config import save_session_run_config

            behavior = scenario.removeprefix("live_")
            save_session_run_config(
                req.session_id,
                agent_id="main",
                permission_rules={behavior: ["computer_use_agent"]},
            )
    from openprogram.runtime.permissions.lifecycle import current_permission_request
    from openprogram.runtime.permissions.policy import permission_decision
    from openprogram.runtime.conversation import plan_mode

    parent_effective = current_permission_request(req)
    parent_decision = permission_decision(
        get_agent_tool("computer_use_agent"), parent_effective, dict(LIST_TABS)
    )
    tool = get_agent_tool("page_scope_probe")
    assert tool is page_scope_probe._agent_tool
    wrapped = _wrap_agentic_runtime_block(tool, req, lambda _e: None, "anchor")
    result = asyncio.run(wrapped.execute("scope-call", {}, None, None))
    if execution_token is not None:
        from openprogram.runtime.execution.run_control import reset_current_execution_id
        reset_current_execution_id(execution_token)
    text = "".join(c.text for c in result.content if not c.text.startswith("[notice]"))
    data = json.loads(text) if not result.is_error else {"outer_error": text}
    if owned_context is not None:
        surface_context.release_bindings(owned_context)
    data.update(
        parent_request_mode=req.permission_mode,
        parent_effective_mode=parent_effective.permission_mode,
        parent_active_plan=plan_mode.is_plan_mode(req.session_id),
        parent_decision=_decided(parent_decision),
        parent_identity=source_identity(),
        parent_pid=os.getpid(),
        renderer_calls=calls,
        remaining_bindings=len(webtab._bindings),
        scenario=scenario,
    )
    Path(os.environ["PAGE_PROBE_RECEIPT"]).write_text(json.dumps(data))


if __name__ == "__main__":
    main()
