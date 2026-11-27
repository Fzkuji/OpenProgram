"""Shared fixtures of the gateway's outside-agent tests."""
from __future__ import annotations

from pathlib import Path

import pytest

#: The models the agent tests connect with: a reasoning model with a window,
#: a plain one with none.
MODELS = [
    {"id": "deepseek/deepseek-chat", "provider": "deepseek", "name": "DeepSeek Chat", "context_window": 128000,
     "max_tokens": 8192, "vision": False, "reasoning": True, "thinking_levels": ["low", "medium", "high"],
     "default_thinking_level": "medium"},
    {"id": "openai/gpt-5", "provider": "openai", "name": "GPT-5", "context_window": None, "max_tokens": None,
     "vision": True, "reasoning": False, "thinking_levels": [], "default_thinking_level": None},
]


@pytest.fixture
def agent_home(tmp_path, monkeypatch) -> Path:
    """A home of its own for an agent's files, the gateway at 127.0.0.1:18100,
    :data:`MODELS` enabled. Returns the home folder."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    from openprogram.models.gateway import address, conversion
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(model) for model in MODELS])
    return home


@pytest.fixture
def models(monkeypatch):
    """Sets the enabled models (a list of :data:`MODELS`-shaped rows)."""
    from openprogram.models.gateway import conversion

    def use(rows):
        monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(row) for row in rows])
    return use


def keyed_base(agent_id: str, protocol: str = "openai-chat") -> str:
    """The base URL an agent that sends no key is given (``Agent.path_keyed``)."""
    from openprogram.models.gateway.address import PROTOCOL_PATHS
    from openprogram.models.gateway.keys import path_key, token_for
    return f"http://127.0.0.1:18100/gateway/agent/{path_key(token_for(agent_id))}{PROTOCOL_PATHS[protocol]}"
