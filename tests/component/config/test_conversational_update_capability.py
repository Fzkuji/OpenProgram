"""Chat source updates must not reserve a request without a controller backend."""
import asyncio
from types import SimpleNamespace

import pytest

from openprogram import _compat
from openprogram.programs.tools.system import self_update as update_tools


@pytest.mark.parametrize("platform, expected", [
    ("win32", None), ("linux", None), ("freebsd14", None), ("darwin", "launchd"),
])
def test_source_update_backend_is_separate_from_worker_service(monkeypatch, platform, expected):
    monkeypatch.setattr(_compat, "_sys", SimpleNamespace(platform=platform))
    assert _compat.conversational_update_backend() == expected
    if platform in {"win32", "linux"}:
        assert _compat.worker_service_backend() is not None


@pytest.mark.parametrize("name, arguments", [
    ("self_update_prepare", dict(worktree_id="wt_example", candidate_sha="a" * 40,
                                 goal="Update", assertions=["Works"])),
    ("self_update_retry", dict(update_id="su_example", candidate_sha="a" * 40)),
])
@pytest.mark.parametrize("backend", [None, "launchd"])
def test_public_source_update_preflight_precedes_context_and_state(monkeypatch, name, arguments, backend):
    monkeypatch.setattr(_compat, "conversational_update_backend", lambda: backend)
    reached = []

    def context():
        reached.append("context")
        raise RuntimeError("fixture context reached")

    def unexpected_state(*args, **kwargs):
        pytest.fail("preflight must not create a manager, store or request")

    monkeypatch.setattr(update_tools, "_turn_context", context)
    monkeypatch.setattr(update_tools, "SelfUpdateStore", unexpected_state)
    monkeypatch.setattr(update_tools, "get_manager", unexpected_state)
    tool = getattr(update_tools, name)
    result = asyncio.run(tool.execute("capability-preflight", arguments, None, None))
    assert result.is_error
    message = "\n".join(part.text for part in result.content)
    if backend is None:
        assert reached == []
        assert "No update request was created" in message
        assert "openprogram upgrade" in message
        assert "Desktop release updater" in message
    else:
        assert reached == ["context"]
        assert "fixture context reached" in message


def test_native_backend_preflight():
    """Exercise actual host selection, not just mocked platform values."""
    if _compat.conversational_update_backend() is None:
        with pytest.raises(update_tools.SelfUpdateToolError, match="No update request was created"):
            update_tools._require_update_backend()
    else:
        update_tools._require_update_backend()
