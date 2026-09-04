"""Durable role selection through the public Goal with scripted providers."""
import importlib
import json

import pytest

from openprogram.agentic_programming.runtime import Runtime
from openprogram.providers.types import Model


@pytest.fixture
def role_goal(tmp_path, monkeypatch):
    from openprogram.agent.session_db import SessionDB
    package = importlib.import_module("openprogram.programs.workflow.goal")
    function = importlib.import_module("openprogram.agentic_programming.function")
    db = SessionDB(tmp_path / "sessions")
    db.create_session("role-session", "main")
    monkeypatch.setattr(package, "_db", lambda: db)
    monkeypatch.setattr(function, "current_session_id", lambda: "role-session")
    monkeypatch.setattr(package, "_emit_goal_notice", lambda *_a, **_kw: None)
    calls = []
    unavailable = set()
    from openprogram.agent import AgentSession
    original_init = AgentSession.__init__

    def capture_session(self, *args, **kwargs):
        selected = kwargs["model"]
        calls.append((selected.provider, selected.id, kwargs.get("api_key")))
        factory.prompts.append(kwargs.get("system_prompt") or "")
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(AgentSession, "__init__", capture_session)

    class ScriptedRuntime(Runtime):
        def __init__(self, provider, model):
            def answer(content, **kwargs):
                text = "\n".join(str(block.get("text", "")) for block in content)
                if "completion judge" in text:
                    return '{"verdict":"unmet","reason":"more work","checklist":[false]}'
                if "completion SPECIFICATION" in text:
                    return '{"spec":"write the article","checklist":["article complete"]}'
                return "article draft"
            super().__init__(call=answer, model=f"{provider}:{model}")
            self.provider_id = provider
            self.api_model = Model(id=model, name=model, provider=provider,
                                   api="completion", base_url="https://test.invalid")
            self.api_key = f"test-key-for-{provider}"

    def factory(provider=None, model=None, **kwargs):
        if provider in unavailable:
            raise ValueError("test provider unavailable")
        runtime = ScriptedRuntime(provider, model)
        factory.created.append(runtime)
        return runtime

    factory.created = []
    factory.prompts = []

    monkeypatch.setattr("openprogram.providers.registry.create_runtime", factory)
    monkeypatch.setattr(package, "judge_model", lambda: "")
    return package, factory, calls, unavailable


def test_goal_resumes_saved_roles_instead_of_new_defaults(role_goal):
    package, factory, calls, _unavailable = role_goal
    package.goal("write the article", runtime=factory("worker", "writer"),
                 judge_model="judge:reviewer", max_rounds=1, timeout_s=17,
                 judge_timeout_s=23)
    saved = package.load_goal("role-session")
    assert saved["roles"]["work"]["provider"] == "worker"
    assert saved["roles"]["judge"]["provider"] == "judge"
    assert "test-key" not in json.dumps(saved)
    assert ("judge", "reviewer", "test-key-for-judge") in calls
    assert all(key == f"test-key-for-{provider}" for provider, _model, key in calls)
    assert not factory.created[0]._closed  # The caller still owns its Runtime.
    assert factory.created[1]._closed  # Goal owns the separately created judge.
    assert saved["roles"]["work"]["timeout_s"] == 17
    assert saved["roles"]["judge"]["timeout_s"] == 23
    frames = []
    package._emit_goal_update(frames.append, "role-session", saved)
    assert frames[0]["data"]["goal"]["roles"] == saved["roles"]
    assert "judge/reviewer" in package._status_text(saved)
    package.apply_goal_action("role-session", "budget", max_turns=2)
    calls.clear()
    package.goal("ignored", resume=True, runtime=factory("new-default", "other"))
    restored = package.load_goal("role-session")
    assert restored["roles"] == saved["roles"]
    assert calls and {row[0] for row in calls} == {"worker", "judge"}
    assert all(key == f"test-key-for-{provider}" for provider, _model, key in calls)


def test_unavailable_saved_role_pauses_before_any_work(role_goal):
    package, factory, calls, unavailable = role_goal
    package.goal("write the article", runtime=factory("worker", "writer"),
                 judge_model="judge:reviewer", max_rounds=1)
    package.apply_goal_action("role-session", "budget", max_turns=2)
    unavailable.add("judge")
    calls.clear()
    with pytest.raises(ValueError, match="unavailable"):
        package.goal("ignored", resume=True, runtime=factory("new-default", "other"))
    saved = package.load_goal("role-session")
    assert saved["status"] == "paused_recoverable"
    assert saved["pause_reason"] == "role_unavailable"
    assert calls == []
    assert factory.created[-1].provider_id == "worker"
    assert factory.created[-1]._closed  # Cleanup also covers partial preparation.


def test_paused_role_edit_is_used_by_public_resume_without_resetting_progress(role_goal):
    package, factory, calls, _unavailable = role_goal
    package.goal("write the article", runtime=factory("worker", "writer"),
                 judge_model="judge:reviewer", max_rounds=1)
    before = package.load_goal("role-session")
    changed = package.apply_goal_action("role-session", "roles", roles={
        "judge": {"provider": "new-judge", "model": "new-reviewer", "effort": "high", "timeout_s": 19},
    })
    for key in ("goal_id", "revision", "checklist", "usage", "budget", "questions"):
        assert changed.get(key) == before.get(key)
    assert not changed.get("roles")
    frames = []
    package._emit_goal_update(frames.append, "role-session", changed)
    assert frames[0]["data"]["goal"]["role_requests"] == changed["role_requests"]
    package.apply_goal_action("role-session", "budget", max_turns=2)
    calls.clear()
    package.goal("ignored", resume=True, runtime=factory("unrelated-default", "other"))
    saved = package.load_goal("role-session")
    assert saved["roles"]["judge"]["provider"] == "new-judge"
    assert saved["roles"]["judge"]["timeout_s"] == 19
    assert saved["roles"]["work"] == before["roles"]["work"]
    assert any(provider == "new-judge" for provider, _model, _key in calls)


def test_initial_role_failure_keeps_selection_for_retry(role_goal):
    package, factory, calls, unavailable = role_goal
    unavailable.add("judge")
    with pytest.raises(ValueError, match="unavailable"):
        package.goal("write the article", runtime=factory("worker", "writer"),
                     judge_model="judge:reviewer", max_rounds=1)
    assert calls == []
    unavailable.clear()
    package.goal("ignored", resume=True, runtime=factory("new-default", "other"))
    saved = package.load_goal("role-session")
    assert saved["roles"]["work"]["provider"] == "worker"
    assert saved["roles"]["judge"]["provider"] == "judge"
    assert saved.get("roles_origin") != "legacy-resolved"


def test_provider_slash_selector_keeps_colon_in_model_id(role_goal):
    package, factory, calls, _unavailable = role_goal
    package.goal("write the article", runtime=factory("worker", "writer"),
                 judge_model="judge/reviewer:variant", max_rounds=1)
    assert ("judge", "reviewer:variant", "test-key-for-judge") in calls


def test_same_model_namespace_does_not_replace_explicit_auth_route(role_goal):
    package, factory, calls, _unavailable = role_goal
    subscribed = factory("subscription", "same-model")
    subscribed.model = "api-route:same-model"
    subscribed.api_model = subscribed.api_model.model_copy(update={"provider": "api-route"})
    package.goal("write the article", runtime=subscribed,
                 judge_model="api-route:same-model", max_rounds=1)
    saved = package.load_goal("role-session")
    assert saved["roles"]["work"]["provider"] == "subscription"
    assert saved["roles"]["judge"]["provider"] == "api-route"
    assert ("api-route", "same-model", "test-key-for-api-route") in calls


def test_initial_failure_binds_bare_model_to_original_provider(role_goal):
    package, factory, calls, unavailable = role_goal
    unavailable.add("judge")
    with pytest.raises(ValueError, match="unavailable"):
        package.goal("write the article", runtime=factory("worker", "writer"),
                     model="writer2", judge_model="judge:reviewer", max_rounds=1)
    unavailable.clear()
    package.goal("ignored", resume=True, runtime=factory("new-default", "other"))
    assert package.load_goal("role-session")["roles"]["work"]["provider"] == "worker"
    assert ("worker", "writer2", "test-key-for-worker") in calls


def test_cross_provider_role_retains_callers_system_constraints(role_goal):
    package, factory, _calls, _unavailable = role_goal
    runtime = factory("worker", "writer")
    runtime.system = "CALLER_CONSTRAINT_828_DO_NOT_PUBLISH"
    package.goal("write the article", runtime=runtime,
                 judge_model="judge:reviewer", max_rounds=1)
    assert len(factory.prompts) >= 3
    assert all("CALLER_CONSTRAINT_828_DO_NOT_PUBLISH" in text for text in factory.prompts)
