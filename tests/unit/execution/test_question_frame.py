"""One client view of an open question for live, reload and REST."""
from __future__ import annotations

import types

from openprogram.runtime.execution.waits import question_frame_data


def test_question_frame_shows_reviewed_args_and_creation_time():
    wait = types.SimpleNamespace(
        wait_id="w1", kind="approval", execution_id="e1", claim_generation=2,
        policy_snapshot={"allowed_scopes": ["once"]},
        request={
            "prompt": "Run it?", "tool": "bash",
            "args": {"command": "rm -rf build"},
            "shown_args": {"command": "rm -rf build  # retry candidate"},
        },
        created_at=10.0, expires_at=0,
    )

    data = question_frame_data(wait, session_id="s1", expected_version=7)

    assert data["args"] == {"command": "rm -rf build  # retry candidate"}
    assert data["created_at"] == 10.0
    assert (data["id"], data["session_id"], data["expected_version"]) == ("w1", "s1", 7)
    assert data["allowed_scopes"] == ["once"]


def test_question_frame_falls_back_to_call_args():
    wait = types.SimpleNamespace(
        wait_id="w1", kind="approval", execution_id="e1", claim_generation=0,
        policy_snapshot={}, request={"args": {"path": "a.txt"}},
        created_at=1.0, expires_at=0,
    )

    assert question_frame_data(wait, session_id=None, expected_version=0)["args"] == {"path": "a.txt"}
