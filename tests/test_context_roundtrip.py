"""Additional regression tests for Context JSON serialization roundtrips."""

from agentic import Context, agentic_function


def test_context_from_dict_uses_current_defaults_for_legacy_payloads():
    """Missing legacy fields should fall back to current Context defaults."""
    restored = Context.from_dict({"name": "legacy", "children": []})

    assert restored.render == "summary"
    assert restored.status == "running"
    assert restored.compress is False

def test_context_json_roundtrip_preserves_attempts_and_render_metadata():
    """Roundtripping via _to_dict()/from_dict() keeps retry and render fields intact."""

    @agentic_function(render="detail", compress=True)
    def task():
        child()
        return "done"

    @agentic_function(render="result")
    def child():
        return "child output"

    task()

    task.context.attempts = [
        {"attempt": 1, "error": "temporary failure", "raw_reply": None},
        {"attempt": 2, "error": None, "raw_reply": "fixed on retry"},
    ]
    task.context.error = "temporary failure"
    task.context.status = "success"
    task.context.source_file = "/tmp/task.py"
    task.context.children[0].source_file = "/tmp/child.py"

    restored = Context.from_dict(task.context._to_dict())

    assert restored.attempts == task.context.attempts
    assert restored.error == "temporary failure"
    assert restored.status == "success"
    assert restored.render == "detail"
    assert restored.compress is True
    assert restored.source_file == "/tmp/task.py"

    restored_child = restored.children[0]
    original_child = task.context.children[0]
    assert restored_child.render == original_child.render
    assert restored_child.output == original_child.output
    assert restored_child.source_file == "/tmp/child.py"
