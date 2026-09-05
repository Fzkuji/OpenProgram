"""Live ``branches_list`` graph fingerprint — structure only.

``_poll`` used to ``json.dumps`` the whole graph, so a streaming
preview/content/output change forced a full History-graph push every
~1.2s. The cheap signature must stay stable for those fields and still
change when the canvas would actually redraw.
"""

from __future__ import annotations

from openprogram.webui._exec_dag import _graph_push_signature


def _node(**kw):
    row = {
        "id": "a1",
        "predecessor": "u1",
        "role": "assistant",
        "display": None,
        "status": "running",
        "preview": "hello",
        "content": "hello",
        "output": "hello",
    }
    row.update(kw)
    return row


def test_preview_content_output_do_not_change_signature():
    a = [_node(preview="hi", content="hi", output="hi")]
    b = [_node(preview="hi there", content="hi there", output="hi there")]
    assert _graph_push_signature(a, "a1") == _graph_push_signature(b, "a1")


def test_status_change_changes_signature():
    a = [_node(status="running")]
    b = [_node(status="completed")]
    assert _graph_push_signature(a, "a1") != _graph_push_signature(b, "a1")


def test_new_node_changes_signature():
    a = [_node()]
    b = [_node(), _node(id="a2", predecessor="a1", preview="new")]
    assert _graph_push_signature(a, "a1") != _graph_push_signature(b, "a2")


def test_head_change_changes_signature():
    g = [_node()]
    assert _graph_push_signature(g, "a1") != _graph_push_signature(g, "a2")


def test_covers_and_is_error_change_signature():
    base = [_node()]
    assert _graph_push_signature(base) != _graph_push_signature(
        [_node(covers_ids=["u0", "a0"])])
    assert _graph_push_signature(base) != _graph_push_signature(
        [_node(is_error=True)])


def test_frontend_structural_fields_change_signature():
    base = [_node()]
    assert _graph_push_signature(base) != _graph_push_signature(
        [_node(display="runtime")])
    assert _graph_push_signature(base) != _graph_push_signature(
        [_node(predecessor="other")])
    assert _graph_push_signature(base) != _graph_push_signature(
        [_node(role="user")])
    assert _graph_push_signature(base) != _graph_push_signature(
        [_node(_lane=1, _tier=2)])
    assert _graph_push_signature(base) != _graph_push_signature(
        [_node(attach_ref="x", attach_label="y")])
    assert _graph_push_signature(base) != _graph_push_signature(
        [_node(superseded_summary=True)])
    assert _graph_push_signature(base) != _graph_push_signature(
        [_node(spawned_from={"label": "agent"})])
    assert _graph_push_signature(base) != _graph_push_signature(
        [_node(branch_name="fork")])
    assert _graph_push_signature(base) != _graph_push_signature(
        [_node(
            spawn_remote=True,
            spawn_remote_session="source",
            spawn_remote_id="source-node",
        )])
    assert _graph_push_signature(base) != _graph_push_signature(
        [_node(spawn_out=True, spawn_out_session="target", spawn_out_head="h")])
    assert _graph_push_signature([]) == _graph_push_signature(None)
