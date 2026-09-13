"""Turn entry and cleanup preserve the enclosing execution context."""
from contextvars import Context
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openprogram.agent.dispatcher.turn_context import TurnBindings
from openprogram.agent.run_control import get_current_execution_id
from openprogram.agent.surface_context import _current as surface_var
from openprogram.agent.turn_request_context import get_turn_request
from openprogram.programs import _runtime as programs_runtime
from openprogram.store import _current_turn_id


@pytest.fixture
def bind_dependencies(monkeypatch):
    monkeypatch.setattr("openprogram.store.SessionNodeWriter", lambda *_: object())
    monkeypatch.setattr("openprogram.providers.registry.create_runtime", lambda: object())
    monkeypatch.setattr("openprogram.worktree.manager.get_manager", lambda: None)
    monkeypatch.setattr("openprogram.agent.session_config.load_session_run_config", lambda _: {})


def _bind():
    return TurnBindings.bind(
        req=SimpleNamespace(session_id="cleanup-session", render_range=None, surface_context=None),
        assistant_msg_id="cleanup-turn", db=None, snapshot_project_baseline=False,
    )


@pytest.mark.parametrize("failure", [RuntimeError("entry failed"), KeyboardInterrupt()])
def test_failed_entry_restores_outer_context(bind_dependencies, failure):
    def scenario():
        outer = {"outer-tool"}
        programs_runtime.install_loaded_deferred(outer)
        with patch("openprogram.store.SessionNodeWriter", side_effect=failure):
            with pytest.raises(type(failure)) as caught:
                _bind()
        assert caught.value is failure
        assert get_current_execution_id() is None
        assert _current_turn_id.get() is None
        assert get_turn_request() is None
        assert programs_runtime._loaded_deferred.get() is outer
    Context().run(scenario)


def test_cleanup_failure_still_restores_tokens(bind_dependencies):
    def scenario():
        binding = _bind()
        binding._web_use_owner_id = "cleanup-owner"
        with patch(
            "openprogram.programs.workflow.browser.web_use_runtime.release_owner_if_initialized",
            side_effect=RuntimeError("cleanup failed"),
        ):
            with pytest.raises(RuntimeError, match="cleanup failed"):
                binding.release()
        assert get_current_execution_id() is None
        assert _current_turn_id.get() is None
        assert get_turn_request() is None
    Context().run(scenario)


def test_foreign_surface_token_does_not_skip_local_reset_or_release_resources():
    def scenario():
        binding = TurnBindings()
        binding._surface_token = Context().run(surface_var.set, None)
        binding._turn_id_token = _current_turn_id.set("cleanup-turn")
        binding._web_use_owner_id = "foreign-owner"
        with patch(
            "openprogram.programs.workflow.browser.web_use_runtime.release_owner_if_initialized",
        ) as release_owner:
            binding.release()
        assert _current_turn_id.get() is None
        release_owner.assert_not_called()
    Context().run(scenario)


def test_successful_nested_bind_restores_deferred_tools(bind_dependencies):
    def scenario():
        outer = {"outer-tool"}
        programs_runtime.install_loaded_deferred(outer)
        binding = _bind()
        try:
            assert programs_runtime._loaded_deferred.get() is not outer
        finally:
            binding.release()
        assert programs_runtime._loaded_deferred.get() is outer
    Context().run(scenario)
