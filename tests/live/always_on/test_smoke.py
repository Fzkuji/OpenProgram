"""One real check-in through the production path against a live free model.

Spends model cost, so the weekly checks run it only with ``--live``. Needs
OPENROUTER_API_KEY; skipped without it. State stays in a temporary directory.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

pytestmark = pytest.mark.slow

_KEY = os.environ.get("OPENROUTER_API_KEY")
_MODELS = ("openai/gpt-oss-120b:free", "meta-llama/llama-3.3-70b-instruct:free")


@pytest.mark.skipif(not _KEY, reason="OPENROUTER_API_KEY not set")
def test_a_real_check_in_finishes_and_leaves_its_row(tmp_path, monkeypatch):
    import openprogram.state.paths as paths
    from openprogram.models.providers import get_model
    from openprogram.runtime.agent_registry import manager
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation import always_on, turn_runtime
    from openprogram.runtime.conversation.always_on import store
    from openprogram.runtime.permissions import authority
    from openprogram.state.store.session import session_store

    model = next((found for found in (get_model("openrouter", name) for name in _MODELS) if found), None)
    if model is None:
        pytest.skip("no known free model in the catalog")
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    authority._reset_owner_cache_for_tests()
    session_store.shared._default_store = None
    monkeypatch.setattr(turn_runtime, "_resolve_model", lambda *a, **kw: model)

    agent_id = "smoke" + uuid.uuid4().hex[:8]
    manager.create(agent_id, name="Smoke")
    try:
        manager.update(agent_id, {"always_on": {"enabled": True, "research": True}})
        session_id = "smoke-" + uuid.uuid4().hex[:8]
        default_db().create_session(session_id, agent_id)
        always_on.join(session_id)
        default_db().append_message(session_id, {
            "id": "u-" + uuid.uuid4().hex[:8], "role": "user",
            "content": "Tell me if anything here needs a report.", "timestamp": time.time()})

        assert always_on.wake(session_id, reason="manual")["outcome"] == "check_in"
        deadline = time.time() + 180
        while time.time() < deadline and store.load_state(session_id).get("check_in"):
            time.sleep(1)

        assert not store.load_state(session_id).get("check_in")
        assert store.load_wake_log(session_id)[0]["outcome"] in ("reported", "nothing_new")
    finally:
        manager.delete(agent_id)
