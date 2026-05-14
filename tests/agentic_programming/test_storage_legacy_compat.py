"""GraphStore._row_to_node must tolerate legacy data.

Old DBs were written when nodes had type='UserMessage' / 'ModelCall' /
'FunctionCall' and data_json field names ``content`` / ``model`` /
``function_name`` / ``arguments`` / ``result`` / ``system_prompt`` /
``triggered_by``. We need to load those rows back into the unified
``Call`` dataclass without crashing.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from openprogram.context.nodes import Call, ROLE_USER, ROLE_LLM, ROLE_CODE
from openprogram.context.storage import GraphStore, init_db


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "legacy.sqlite"
    init_db(p)
    return p


def _seed_session(db_path: Path, session_id: str) -> None:
    """Insert a session row directly so we can hand-craft node rows
    with legacy schema afterwards."""
    with sqlite3.connect(str(db_path)) as conn:
        now = time.time()
        conn.execute(
            """INSERT INTO sessions
               (id, title, created_at, updated_at, model, agent_id,
                source, extra_json, last_node_id)
               VALUES (?, '', ?, ?, '', '', '', '{}', NULL)""",
            (session_id, now, now),
        )
        conn.commit()


def _insert_raw_node(db_path: Path, session_id: str, *,
                     node_id: str, type_: str, predecessor,
                     seq: int, data_json_str: str) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """INSERT INTO nodes
               (id, session_id, type, predecessor, created_at, seq, data_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (node_id, session_id, type_, predecessor, time.time(),
             seq, data_json_str),
        )
        conn.commit()


def test_legacy_user_message_loads_as_user_call(db_path):
    """Old 'UserMessage' rows had ``content`` field; map to ``output``."""
    _seed_session(db_path, "s1")
    _insert_raw_node(
        db_path, "s1",
        node_id="u1", type_="UserMessage", predecessor=None, seq=0,
        data_json_str=json.dumps({
            "content": "hello legacy",
            "metadata": {},
        }),
    )

    store = GraphStore(db_path, "s1")
    g = store.load()
    n = g.nodes["u1"]
    assert n.is_user()
    assert n.output == "hello legacy"
    assert n.content == "hello legacy"      # property accessor still works


def test_legacy_model_call_loads_with_system_prompt(db_path):
    """Old 'ModelCall' had ``model``, ``system_prompt``, ``output`` —
    map onto Call.name + Call.input['system'] + Call.output."""
    _seed_session(db_path, "s1")
    _insert_raw_node(
        db_path, "s1",
        node_id="m1", type_="ModelCall", predecessor=None, seq=0,
        data_json_str=json.dumps({
            "model": "claude-opus",
            "system_prompt": "be terse",
            "reads": [],
            "output": "ok",
            "metadata": {},
        }),
    )
    store = GraphStore(db_path, "s1")
    g = store.load()
    n = g.nodes["m1"]
    assert n.is_llm()
    assert n.name == "claude-opus"
    assert n.model == "claude-opus"
    assert n.output == "ok"
    assert n.system_prompt == "be terse"


def test_legacy_function_call_loads(db_path):
    """Old 'FunctionCall' had function_name/arguments/result/triggered_by."""
    _seed_session(db_path, "s1")
    _insert_raw_node(
        db_path, "s1",
        node_id="f1", type_="FunctionCall", predecessor=None, seq=0,
        data_json_str=json.dumps({
            "function_name": "search",
            "arguments": {"q": "test"},
            "result": {"hits": 3},
            "triggered_by": "parent_id",
            "metadata": {},
        }),
    )
    store = GraphStore(db_path, "s1")
    g = store.load()
    n = g.nodes["f1"]
    assert n.is_code()
    assert n.name == "search"
    assert n.function_name == "search"
    assert n.arguments == {"q": "test"}
    assert n.result == {"hits": 3}
    assert n.called_by == "parent_id"


def test_legacy_field_with_unknown_extra_is_dropped(db_path):
    """A legacy field name we don't know about must NOT crash Call()."""
    _seed_session(db_path, "s1")
    _insert_raw_node(
        db_path, "s1",
        node_id="u1", type_="UserMessage", predecessor=None, seq=0,
        data_json_str=json.dumps({
            "content": "x",
            "weird_legacy_field": "ignore me",
            "metadata": {},
        }),
    )
    store = GraphStore(db_path, "s1")
    g = store.load()
    assert "u1" in g.nodes
    assert g.nodes["u1"].output == "x"


def test_mixed_old_and_new_rows_in_same_session(db_path):
    """A session can have legacy rows and new-format rows side by side."""
    _seed_session(db_path, "s1")
    # Legacy
    _insert_raw_node(
        db_path, "s1",
        node_id="u_old", type_="UserMessage", predecessor=None, seq=0,
        data_json_str=json.dumps({"content": "old", "metadata": {}}),
    )
    # New
    _insert_raw_node(
        db_path, "s1",
        node_id="u_new", type_="user", predecessor="u_old", seq=1,
        data_json_str=json.dumps({
            "name": "", "input": None, "output": "new",
            "called_by": "", "reads": [],
            "metadata": {"parent_id": "u_old"},
        }),
    )
    store = GraphStore(db_path, "s1")
    g = store.load()
    assert g.nodes["u_old"].output == "old"
    assert g.nodes["u_new"].output == "new"
    assert g.nodes["u_new"].is_user()
