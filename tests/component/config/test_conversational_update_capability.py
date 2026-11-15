"""Chat source updates must not reserve a request without a controller backend."""
import asyncio
from types import SimpleNamespace

import pytest

from openprogram.host._platform import environment
from openprogram.operations.self_update.control import backend
from openprogram.programs.tools.system import self_update as update_tools


@pytest.mark.parametrize("platform, expected", [
    ("win32", None), ("linux", None), ("freebsd14", None), ("darwin", "launchd"),
])
def test_source_update_backend_is_separate_from_worker_service(monkeypatch, platform, expected):
    monkeypatch.setattr(environment, "_sys", SimpleNamespace(platform=platform))
    assert environment.conversational_update_backend() == expected
    if platform in {"win32", "linux"}:
        assert environment.worker_service_backend() is not None


def test_the_macos_limit_lives_inside_the_packaged_app_backend(monkeypatch):
    monkeypatch.setattr(environment, "conversational_update_backend", lambda: None)
    with pytest.raises(backend.BackendUnavailable, match="No update request was created") as refusal:
        backend.require_host(backend.LEGACY_APP)
    assert "openprogram upgrade" in str(refusal.value)
    backend.require_host(backend.DEV_STABLE)
    backend.require_host(backend.CHECKOUT)


def test_a_published_mac_app_is_sent_to_settings_before_any_request(monkeypatch):
    monkeypatch.setattr(environment, "conversational_update_backend", lambda: "launchd")
    monkeypatch.setattr(backend, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(backend, "_installed_app", lambda: "/Applications/OpenProgram.app")
    monkeypatch.setattr(backend.subprocess, "run", lambda *_a, **_o: SimpleNamespace(
        returncode=0, stderr="Authority=Developer ID Application: Example (TEAM)\nAuthority=Apple Root CA\n",
    ))
    with pytest.raises(backend.BackendUnavailable, match="Check for updates") as refusal:
        backend.require_host(backend.LEGACY_APP)
    assert "No update request was created" in str(refusal.value)


def test_a_locally_signed_app_keeps_the_agent_update(monkeypatch):
    monkeypatch.setattr(environment, "conversational_update_backend", lambda: "launchd")
    monkeypatch.setattr(backend, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(backend, "_installed_app", lambda: "/Applications/OpenProgram.app")
    monkeypatch.setattr(backend.subprocess, "run", lambda *_a, **_o: SimpleNamespace(
        returncode=0, stderr="Signature=adhoc\n",
    ))
    backend.require_host(backend.LEGACY_APP)


@pytest.mark.parametrize("name, arguments", [
    ("retry", dict(update_id="su_example", candidate_sha="a" * 40)),
])
def test_a_retry_on_a_host_without_a_controller_explains_itself(monkeypatch, name, arguments):
    from openprogram.runtime.agent.turn_request import TurnRequest

    monkeypatch.setattr(environment, "conversational_update_backend", lambda: None)
    monkeypatch.setattr(backend, "select", lambda source=None: backend.LEGACY_APP)
    monkeypatch.setattr(update_tools, "_turn_context", lambda: (TurnRequest(session_id="s", user_text="u", agent_id="main", source="web"), "a"))

    def unexpected_state(*args, **kwargs):
        pytest.fail("the refusal must not create a manager, store or request")

    monkeypatch.setattr(update_tools, "SelfUpdateStore", unexpected_state)
    monkeypatch.setattr(update_tools, "get_manager", unexpected_state)
    result = asyncio.run(update_tools.self_update.execute(
        "capability-preflight", {"action": name, **arguments}, None, None))
    assert result.is_error
    message = "\n".join(part.text for part in result.content)
    assert "No update request was created" in message and "Desktop release updater" in message


def test_a_retry_in_a_developer_checkout_activates_nothing(monkeypatch):
    from openprogram.runtime.agent.turn_request import TurnRequest

    monkeypatch.setattr(backend, "select", lambda source=None: backend.CHECKOUT)
    monkeypatch.setattr(update_tools, "_turn_context", lambda: (TurnRequest(session_id="s", user_text="u", agent_id="main", source="web"), "a"))
    result = asyncio.run(update_tools.self_update.execute(
        "capability-preflight", {"action": "retry", "update_id": "su_example", "candidate_sha": "a" * 40}, None, None))
    assert not result.is_error
    assert "activates nothing" in "\n".join(part.text for part in result.content)
