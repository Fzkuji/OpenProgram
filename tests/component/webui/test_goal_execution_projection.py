"""Persisted Goal execution nodes expose their actual prompt and reply."""
import json

import pytest

from openprogram.context.nodes import Call, ROLE_CODE, ROLE_LLM
from openprogram.store import SessionNodeWriter, SessionStore
from openprogram.webui._exec_dag import build_exec_dag_by_id


@pytest.mark.parametrize("reply", ["验收完成", [{"type": "text", "text": "完成"}], ""])
def test_goal_tree_restores_llm_prompt_and_output(tmp_path, monkeypatch, reply):
    store = SessionStore(tmp_path / "sessions")
    store.create_session("goal-test", "main")
    writer = SessionNodeWriter(store, "goal-test")
    root = Call(role=ROLE_CODE, name="goal", input={"prompt": "Write review"}, output="done")
    writer.append(root)
    writer.append(Call(role=ROLE_LLM, name="test-model", caller=root.id,
        input={"system": "system text"}, output=reply,
        metadata={"prompt_text": "Verify the article", "status": "completed"}))
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store)
    tree = build_exec_dag_by_id("goal-test", root.id)
    node = tree["children"][0]
    expected = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
    assert node["params"]["_content"] == "Verify the article"
    assert node["params"]["system"] == "system text"
    assert node["output"] == expected
    assert node["raw_reply"] == expected
    assert tree["params"]["prompt"] == "Write review"
    assert store.get_nodes("goal-test")[-1].output == reply


def test_legacy_llm_input_remains_visible(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "sessions")
    store.create_session("legacy", "main")
    writer = SessionNodeWriter(store, "legacy")
    node = Call(role=ROLE_LLM, input="Legacy prompt", output="Legacy reply")
    writer.append(node)
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store)
    tree = build_exec_dag_by_id("legacy", node.id)
    assert tree["params"]["_content"] == "Legacy prompt"
    assert tree["output"] == "Legacy reply"
