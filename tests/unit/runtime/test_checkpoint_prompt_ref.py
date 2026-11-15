"""A checkpoint names the conversation's prompt by hash; the branch record
holds the text (Codex's persisted ``base_instructions``, Hermes's pinned
prompt)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.runtime.conversation.turn_runtime import loop_runner
from openprogram.runtime.execution.runtime_contract import system_prompt_sha256, validate_runtime_contract

PROMPT = "<system>you are helpful</system>"


def _req(**changes):
    values = dict(session_id="c1", user_msg_id="u1", goal_verification=None, background=False)
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.fixture
def record(monkeypatch):
    state = {"text": PROMPT}
    monkeypatch.setattr(loop_runner, "_recorded_prompt", lambda req: state["text"])
    return state


def _snapshot(prompt=PROMPT):
    return {"contract_version": 1, "model": {"id": "m"}, "system_prompt": prompt, "tools": []}


def test_the_conversations_turn_keeps_only_the_hash(record):
    kept = loop_runner.prompt_by_reference(_snapshot(), _req(), {})
    assert "system_prompt" not in kept
    assert kept["system_prompt_sha256"] == system_prompt_sha256(PROMPT)
    assert loop_runner.saved_prompt(kept, _req()) == PROMPT


def test_a_prompt_the_branch_does_not_hold_keeps_its_text(record):
    assert loop_runner.prompt_by_reference(_snapshot("own prompt"), _req(), {})["system_prompt"] == "own prompt"
    assert "system_prompt" in loop_runner.prompt_by_reference(
        _snapshot(), _req(background=True), {})


def test_a_record_that_changed_refuses_the_resume(record):
    kept = loop_runner.prompt_by_reference(_snapshot(), _req(), {})
    record["text"] = "<system>changed</system>"
    with pytest.raises(ValueError, match="no longer matches"):
        loop_runner.saved_prompt(kept, _req())


def test_an_old_checkpoint_with_the_text_still_resumes(record):
    record["text"] = None
    assert loop_runner.saved_prompt(_snapshot(), _req()) == PROMPT


def test_the_contract_check_accepts_the_hash_when_the_prompt_matches():
    from openprogram.runtime.execution.runtime_contract import _with_prompt_from_ref

    kept = {key: value for key, value in _snapshot().items() if key != "system_prompt"}
    kept["system_prompt_sha256"] = system_prompt_sha256(PROMPT)
    assert _with_prompt_from_ref(kept, {"system_prompt": PROMPT})["system_prompt"] == PROMPT
    assert _with_prompt_from_ref(kept, {"system_prompt": "other"})["system_prompt"] != "other"
    assert callable(validate_runtime_contract)


def _contract(prompt=PROMPT):
    return {
        "contract_version": 1,
        "model": {"id": "m", "api": "a", "provider": "p", "base_url": "u", "endpoint": "u"},
        "system_prompt": prompt,
        "tools": [],
        "structured_output": None,
        "toolset": None,
        "request_semantics": {"_execution_revision_id": "rev", "execution_location": {"workdir": "/w"}},
    }


def test_a_checkpoint_that_names_its_prompt_by_hash_passes_its_own_load_check(record):
    """A later turn's checkpoint keeps the hash (its branch already holds the
    prompt); loading it checks the snapshot against itself, which must pass,
    or every approval in a conversation's second turn stays paused."""
    kept = loop_runner.prompt_by_reference(_contract(), _req(), {})
    assert "system_prompt" not in kept
    validate_runtime_contract(kept, kept)
    validate_runtime_contract(kept, _contract())
    other = loop_runner.prompt_by_reference(_contract("<system>other</system>"), _req(), {"text": "<system>other</system>"})
    with pytest.raises(ValueError, match="system_prompt"):
        validate_runtime_contract(kept, other)
    with pytest.raises(ValueError, match="system_prompt"):
        validate_runtime_contract(kept, _contract("<system>other</system>"))
