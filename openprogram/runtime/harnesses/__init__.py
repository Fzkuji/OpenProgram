"""Outside coding agents that run a conversation's turns (Codex, Claude Code, and DeepSeek Harness and Gemini CLI over ACP).

A conversation whose model belongs to a harness provider (``codex-harness``)
hands each turn to that agent's engine instead of OpenProgram's agent loop:
the agent makes the model calls and runs the tools, OpenProgram streams what
it does, asks the person its approvals and keeps the record.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Protocol


class HarnessEngine(Protocol):
    """Runs one turn of a conversation in an outside agent."""

    provider: str
    label: str

    def run_turn(self, *, req: Any, history: list[dict], on_event: Any, cancel_event: Any,
                 assistant_msg_id: Optional[str], ordered_blocks_out: Optional[list[dict]],
                 execution_context: Optional[dict]) -> tuple[str, dict, list[dict]]:
        ...


#: Provider id → module path and class of its engine. A harness provider id
#: ends in ``-harness`` (``claude-code-harness``, ``<agent>-harness`` for ACP).
_ENGINES: dict[str, tuple[str, str]] = {
    "codex-harness": ("openprogram.runtime.harnesses.codex.engine", "CodexEngine"),
    "deepseek-harness": ("openprogram.runtime.harnesses.deepseek.engine", "DeepSeekHarnessEngine"),
    "claude-code-harness": ("openprogram.runtime.harnesses.claude_code.engine", "ClaudeCodeEngine"),
    "gemini-cli-harness": ("openprogram.runtime.harnesses.gemini_cli.engine", "GeminiCliEngine"),
}
_loaded: dict[str, HarnessEngine] = {}


def harness_providers() -> tuple[str, ...]:
    return tuple(_ENGINES)


def is_harness_provider(provider: Optional[str]) -> bool:
    return bool(provider) and provider in _ENGINES


def engine_for(provider: Optional[str]) -> Optional[HarnessEngine]:
    """The engine that runs turns for ``provider``, or None for OpenProgram's own loop."""
    if not is_harness_provider(provider):
        return None
    engine = _loaded.get(provider)  # type: ignore[arg-type]
    if engine is None:
        import importlib
        module, name = _ENGINES[provider]  # type: ignore[index]
        engine = getattr(importlib.import_module(module), name)()
        _loaded[provider] = engine  # type: ignore[index]
    return engine


def split_ref(model_ref: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """``provider/model`` split at the first slash (a harness model id may hold more)."""
    if not model_ref or "/" not in model_ref:
        return None, model_ref
    provider, model = model_ref.split("/", 1)
    return provider, model


#: The harness setting's value for each harness provider (``None`` is OpenProgram's own engine).
HARNESS_IDS: dict[str, str] = {"codex-harness": "codex", "deepseek-harness": "deepseek-harness",
                               "claude-code-harness": "claude-code", "gemini-cli-harness": "gemini-cli"}


def provider_of(harness: Optional[str]) -> Optional[str]:
    """The provider id of the harness setting ``harness`` (``codex`` → ``codex-harness``)."""
    return next((provider for provider, name in HARNESS_IDS.items() if name == harness), None)


def branch_setting(req: Any, key: str) -> Any:
    """One stored setting of the branch a turn runs on (``harness_agent``, ``provider_override``)."""
    session_id = req.get("session_id") if isinstance(req, Mapping) else getattr(req, "session_id", None)
    branch_id = req.get("branch_id") if isinstance(req, Mapping) else getattr(req, "branch_id", None)
    if not session_id:
        return None
    try:
        from openprogram.runtime.context.session_db import default_db
        row = default_db().get_session(session_id) or {}
        if branch_id:
            from openprogram.state.store.session.branch_config import config_for_branch
            return config_for_branch(row, branch_id).get(key)
        return row.get(key)
    except Exception:  # noqa: BLE001 - an unreadable setting is an unset one
        return None


def turn_provider(req: Any) -> Optional[str]:
    """The provider a turn runs on: its model override, else the conversation's pin."""
    override = getattr(req, "model_override", None)
    if isinstance(req, Mapping):
        override = req.get("model_override")
    provider, _ = split_ref(override) if isinstance(override, str) else (None, None)
    if provider:
        return provider
    return branch_setting(req, "provider_override")


def turn_engine(req: Any) -> Optional[HarnessEngine]:
    """The harness engine a turn runs in, or None."""
    return engine_for(turn_provider(req))


__all__ = [
    "HARNESS_IDS", "HarnessEngine", "branch_setting", "engine_for", "harness_providers",
    "is_harness_provider", "provider_of", "split_ref", "turn_engine", "turn_provider",
]
