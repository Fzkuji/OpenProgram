"""
Tests for Memory — persistent execution log.
"""

import json
import tempfile
from pathlib import Path

import pytest
from harness.memory import Memory, Event


@pytest.fixture
def memory(tmp_path):
    """Create a Memory with a temp directory."""
    return Memory(base_dir=str(tmp_path / "logs"))


def test_start_and_end_run(memory):
    """Basic run lifecycle."""
    run_id = memory.start_run(task="test task")
    assert run_id.startswith("run_")
    memory.end_run(status="success")

    # Check files exist
    run_dir = Path(memory._base_dir) / run_id
    assert (run_dir / "run.jsonl").exists()
    assert (run_dir / "run.md").exists()
    assert (run_dir / "media").is_dir()


def test_events_are_logged(memory):
    """Events are written to JSONL."""
    run_id = memory.start_run(task="test")

    memory.log_function_call("observe", params={"task": "look"}, scope="isolated")
    memory.log_message_sent("observe", message="Take a screenshot")
    memory.log_message_received("observe", reply='{"elements": ["button"]}')
    memory.log_function_return("observe", result={"elements": ["button"]}, status="success", duration_ms=150)
    memory.log_decision(action="call", reasoning="need to act", function_name="act")

    memory.end_run()

    # Read back
    events = memory.load_run(run_id)
    types = [e.type for e in events]
    assert "run_start" in types
    assert "function_call" in types
    assert "message_sent" in types
    assert "message_received" in types
    assert "function_return" in types
    assert "decision" in types
    assert "run_end" in types


def test_media_saving(memory, tmp_path):
    """Media files are saved to run directory."""
    run_id = memory.start_run(task="test")

    # Save from bytes
    media_path = memory.save_media("test.png", data=b"\x89PNG fake image data")
    assert media_path.startswith("media/")
    assert "test.png" in media_path

    # Check file exists
    full_path = Path(memory._base_dir) / run_id / media_path
    assert full_path.exists()
    assert full_path.read_bytes() == b"\x89PNG fake image data"

    # Save from source file
    source = tmp_path / "source.jpg"
    source.write_bytes(b"\xff\xd8\xff fake jpeg")
    media_path2 = memory.save_media("photo.jpg", source_path=str(source))
    full_path2 = Path(memory._base_dir) / run_id / media_path2
    assert full_path2.exists()

    memory.end_run()


def test_markdown_summary(memory):
    """Markdown summary is generated."""
    run_id = memory.start_run(task="Click login button")

    memory.log_function_call("observe", params={"task": "find button"}, scope="isolated")
    memory.log_function_return("observe", result={"found": True}, status="success", duration_ms=100)
    memory.log_decision(action="call", reasoning="button found, click it", function_name="act")
    memory.log_function_call("act", params={"action": "click"})
    memory.log_function_return("act", result={"success": True}, status="success", duration_ms=200)
    memory.log_error("element not found", function_name="verify")

    memory.end_run(status="success")

    summary = memory.get_summary(run_id)
    assert "Click login button" in summary
    assert "observe" in summary
    assert "act" in summary
    assert "✓" in summary
    assert "Error" in summary


def test_list_runs(memory):
    """Can list all runs."""
    run1 = memory.start_run(task="task 1")
    memory.end_run()

    run2 = memory.start_run(task="task 2")
    memory.end_run(status="error")

    runs = memory.list_runs()
    assert len(runs) == 2
    tasks = {r["task"] for r in runs}
    assert "task 1" in tasks
    assert "task 2" in tasks


def test_parent_id_tracking(memory):
    """Events have correct parent IDs."""
    memory.start_run(task="test")

    call_id = memory.log_function_call("outer")
    inner_id = memory.log_function_call("inner")
    memory.log_function_return("inner")
    memory.log_function_return("outer")

    memory.end_run()

    events = [e for e in memory._events if e.type == "function_call"]
    assert events[0].parent_id is None  # outer has no parent
    assert events[1].parent_id == call_id  # inner's parent is outer


def test_load_nonexistent_run(memory):
    """Loading a nonexistent run raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        memory.load_run("run_doesnotexist")


def test_event_defaults():
    """Event auto-generates timestamp and id."""
    e = Event(type="test")
    assert e.timestamp  # non-empty
    assert e.id  # non-empty
    assert len(e.id) == 12


def test_media_in_markdown(memory, tmp_path):
    """Media paths appear as links in Markdown summary."""
    run_id = memory.start_run(task="media test")

    media_path = memory.save_media("screenshot.png", data=b"fake png")
    memory.log_function_call("observe")
    memory.log_function_return("observe", result={}, media=[media_path])

    memory.end_run()

    summary = memory.get_summary(run_id)
    assert "📎" in summary
    assert "screenshot.png" in summary


def test_memory_with_function(memory):
    """Memory can log function calls manually."""
    run_id = memory.start_run(task="test")

    memory.log_function_call("test_fn", params={"task": "test"}, scope="isolated")
    memory.log_function_return("test_fn", result={"status": "ok"}, status="success", duration_ms=100)

    memory.end_run()

    events = memory.load_run(run_id)
    fn_calls = [e for e in events if e.type == "function_call"]
    fn_returns = [e for e in events if e.type == "function_return"]
    assert len(fn_calls) == 1
    assert fn_calls[0].function_name == "test_fn"
    assert len(fn_returns) == 1
    assert fn_returns[0].status == "success"
